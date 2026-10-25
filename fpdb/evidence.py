"""Lossless, bounded joint TLS samples, separate from legacy compact observations."""
from __future__ import annotations

from fpdb.core import FIELDS, DataError, canonical, required_sha1

AUX = ("tlsClientHelloLength", "tlsCipher", "tlsVersion", "tlsAlpn")
MAX_JOINT_SAMPLES = 128


def extract_joint(payload: dict) -> dict:
    body = payload.get("echo", payload)
    tls = body["http"]["tls"]
    row = {f: required_sha1(tls[f], f) for f in FIELDS}
    for field in AUX:
        val = tls.get(field)
        if val is None:
            continue
        if field == "tlsClientHelloLength":
            if isinstance(val, bool) or not str(val).isdigit():
                raise DataError("Invalid tlsClientHelloLength")
            row[field] = int(val)
        elif isinstance(val, str) and len(val) <= 128:
            row[field] = val
    return row


def validate_evidence(rows: list[dict]) -> None:
    if not isinstance(rows, list) or len(rows) > MAX_JOINT_SAMPLES:
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


def merge_evidence(old: list[dict], new: list[dict]) -> tuple[list[dict], bool]:
    validate_evidence(old)
    validate_evidence(new)
    totals: dict[str, dict] = {}
    for item in old + new:
        row = {k: v for k, v in item.items() if k != "count"}
        key = canonical(row)
        if key not in totals:
            totals[key] = {**row, "count": 0}
        totals[key]["count"] += item["count"]
    # Keep the most often observed exact tuples; do not pretend a truncated
    # sample store is exhaustive. Deterministic ties and ordering.
    ordered = sorted(totals.values(), key=lambda r: (-r["count"], canonical(r)))
    truncated = len(ordered) > MAX_JOINT_SAMPLES
    return ordered[:MAX_JOINT_SAMPLES], truncated
