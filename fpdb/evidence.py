"""Lossless, bounded joint TLS samples, separate from legacy compact observations."""
from __future__ import annotations

from typing import Literal

from fpdb.core import FIELDS, DataError, canonical, json_array, json_object, required_sha1
from fpdb.schema import Evidence, JointSample, JSONObject

TEXT_AUX: tuple[Literal["tlsCipher", "tlsVersion", "tlsAlpn"], ...] = ("tlsCipher", "tlsVersion", "tlsAlpn")
AUX = ("tlsClientHelloLength", *TEXT_AUX)
MAX_JOINT_SAMPLES = 128


def extract_joint(payload: JSONObject) -> JointSample:
    body = json_object(payload.get("echo", payload))
    tls = json_object(json_object(body["http"])["tls"])
    row: JointSample = {
        "tlsClientCiphersSha1": required_sha1(tls["tlsClientCiphersSha1"], "tlsClientCiphersSha1"),
        "tlsClientExtensionsSha1": required_sha1(tls["tlsClientExtensionsSha1"], "tlsClientExtensionsSha1"),
        "tlsClientExtensionsSha1Le": required_sha1(tls["tlsClientExtensionsSha1Le"], "tlsClientExtensionsSha1Le"),
    }
    length = tls.get("tlsClientHelloLength")
    if length is not None:
        if isinstance(length, bool) or not str(length).isdigit():
            raise DataError("Invalid tlsClientHelloLength")
        row["tlsClientHelloLength"] = int(str(length))
    for field in TEXT_AUX:
        val = tls.get(field)
        if val is None:
            continue
        if isinstance(val, str) and len(val) <= 128:
            row[field] = val
    return row


def validate_evidence(rows: object) -> None:
    rows = json_array(rows)
    if len(rows) > MAX_JOINT_SAMPLES:
        raise DataError("Invalid evidence size")
    for row in rows:
        if not isinstance(row, dict) or set(row) - {*FIELDS, *AUX, "count"}:
            raise DataError("Invalid evidence fields")
        for f in FIELDS:
            required_sha1(row.get(f), f)
        count = row.get("count")
        if type(count) is not int or count < 1:
            raise DataError("Invalid evidence count")
        for key in AUX:
            if key not in row:
                continue
            val = row[key]
            if key == "tlsClientHelloLength":
                if type(val) is not int or not 0 < val < 1000000:
                    raise DataError("Invalid ClientHello length")
            elif not isinstance(val, str) or len(val) > 128:
                raise DataError("Invalid TLS metadata")


def merge_evidence(old: list[Evidence], new: list[Evidence]) -> tuple[list[Evidence], bool]:
    validate_evidence(old)
    validate_evidence(new)
    totals: dict[str, Evidence] = {}
    for item in old + new:
        row = {k: v for k, v in item.items() if k != "count"}
        key = canonical(row)
        if key not in totals:
            totals[key] = {**item, "count": 0}
        totals[key]["count"] += item["count"]
    # Keep the most often observed exact tuples; do not pretend a truncated
    # sample store is exhaustive. Deterministic ties and ordering.
    def sample_priority(sample: Evidence) -> tuple[int, str]:
        return -sample["count"], canonical(sample)

    ordered = sorted(totals.values(), key=sample_priority)
    truncated = len(ordered) > MAX_JOINT_SAMPLES
    return ordered[:MAX_JOINT_SAMPLES], truncated
