"""Shared schema helpers. Python standard library only."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from pathlib import Path
from typing import cast

from fpdb.schema import (Case, Fingerprint, FingerprintField, Environment,
                         JSONObject, JSONValue, RecordIdentity)

FIELDS: tuple[FingerprintField, ...] = (
    "tlsClientCiphersSha1",
    "tlsClientExtensionsSha1",
    "tlsClientExtensionsSha1Le",
)
ROOT = Path(__file__).resolve().parent.parent
_SAFE_PART = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class DataError(ValueError):
    pass


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()[:24]


def read_json(path: str | Path) -> JSONValue:
    with open(path, encoding="utf-8") as stream:
        return cast(JSONValue, json.load(stream))


def json_object(value: object) -> JSONObject:
    """Narrow an untrusted JSON value before accessing object members."""
    if not isinstance(value, dict):
        raise DataError("Expected a JSON object")
    return cast(JSONObject, value)


def json_array(value: object) -> list[JSONValue]:
    if not isinstance(value, list):
        raise DataError("Expected a JSON array")
    return cast(list[JSONValue], value)


def save_json(path: str | Path, obj: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A per-run artifact may be written before being uploaded; avoid partially written results.
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def safe_part(part: object) -> str:
    if not isinstance(part, str) or not _SAFE_PART.fullmatch(part):
        raise DataError(f"Unsafe path component: {part!r}")
    return part


def required_sha1(raw: object, field: str) -> str:
    if not isinstance(raw, str):
        raise DataError(f"{field}: expected Base64 text")
    try:
        data = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise DataError(f"{field}: invalid Base64") from exc
    if len(data) != 20 or base64.b64encode(data).decode("ascii") != raw:
        raise DataError(f"{field}: expected canonical Base64 of 20-byte SHA-1 digest")
    return raw


def extract_echo(response: object) -> dict[str, str]:
    """Accept the Echo response or an adapter wrapper {echo: <Echo response>}."""
    try:
        response = json_object(response)
        payload = json_object(response.get("echo", response))
        tls = json_object(json_object(payload["http"])["tls"])
        return {field: required_sha1(tls[field], field) for field in FIELDS}
    except (KeyError, TypeError, AttributeError) as exc:
        raise DataError("Echo JSON missing http.tls and/or required SHA-1 fields") from exc


def columns(rows: object) -> tuple[FingerprintField, ...]:
    rows = json_array(rows)
    if not rows:
        raise DataError("observations must be a nonempty list")
    if not all(isinstance(row, dict) for row in rows):
        raise DataError("observations must contain objects")
    objects = [json_object(row) for row in rows]
    first: tuple[FingerprintField, ...] = tuple(field for field in FIELDS if field in objects[0])
    for row in objects:
        if any(field not in FIELDS for field in row):
            raise DataError("Unknown fingerprint field")
        if tuple(field for field in FIELDS if field in row) != first:
            raise DataError("All observations must contain the same columns")
        for field in first:
            required_sha1(row[field], field)
    return first


def compact(rows: list[Fingerprint], *, unique_limit: int, combination_limit: int,
            allowed_fields: tuple[FingerprintField, ...] = FIELDS) -> list[Fingerprint]:
    """Monotone column elimination; preserve real joint observations of retained fields.

    allowed_fields comes from the previously stored record, if any. A dropped
    column is never reconstructed from later observations.
    """
    if unique_limit < 1 or combination_limit < 1:
        raise DataError("Limits must be positive")
    allowed: tuple[FingerprintField, ...] = tuple(field for field in FIELDS if field in allowed_fields)
    for row in rows:
        row_object = json_object(row)
        if any(field not in row_object for field in allowed):
            raise DataError("Input row missing an active field")
        for field in allowed:
            required_sha1(row[field], field)
    if not rows:
        raise DataError("Cannot compact zero observations")
    while True:
        uniques = {field: len({row[field] for row in rows}) for field in allowed}
        overflow = [field for field in allowed if uniques[field] > unique_limit]
        if overflow:
            allowed = tuple(field for field in allowed if field not in overflow)
            continue
        projected = {tuple(row[field] for field in allowed) for row in rows}
        if len(projected) <= combination_limit or not allowed:
            return [{field: val for field, val in zip(allowed, vals)} for vals in sorted(projected)]
        # Tie-breaking is deterministic. Columns with most unique values go first.
        def elimination_priority(field: FingerprintField) -> tuple[int, int]:
            return uniques[field], FIELDS.index(field)

        victim = max(allowed, key=elimination_priority)
        allowed = tuple(field for field in allowed if field != victim)


def merge_observations(old: list[Fingerprint] | None, fresh: list[Fingerprint], *,
                       unique_limit: int, combination_limit: int) -> list[Fingerprint]:
    fresh_columns = columns(fresh)
    if old is None:
        return compact(fresh, unique_limit=unique_limit, combination_limit=combination_limit)
    old_columns = columns(old)
    common: tuple[FingerprintField, ...] = tuple(field for field in FIELDS if field in old_columns and field in fresh_columns)
    joined = [{field: row[field] for field in common} for row in old + fresh]
    return compact(joined, unique_limit=unique_limit, combination_limit=combination_limit,
                   allowed_fields=common)


def record_identity(case: Case, resolved_environment: Environment) -> RecordIdentity:
    return {
        "target": case["target"],
        "language": case["language"],
        "client": case["client"],
        "runtime_version": case["runtime_version"],
        "client_version": case["client_version"],
        "platform": case["platform"],
        "runner": case["runner"],
        "revision": case["revision"],
        "environment": resolved_environment,
    }


def record_path(root: Path, identity: RecordIdentity) -> Path:
    return root / "data" / "observations" / safe_part(identity["language"]) / safe_part(identity["client"]) / (digest(identity) + ".json")
