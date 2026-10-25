"""Shared schema helpers. Python standard library only."""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

FIELDS = (
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


def read_json(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def save_json(path: str | Path, obj: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A per-run artifact may be written before being uploaded; avoid partially written results.
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def safe_part(part: str) -> str:
    if not isinstance(part, str) or not _SAFE_PART.fullmatch(part):
        raise DataError(f"Unsafe path component: {part!r}")
    return part


def required_sha1(raw: object, field: str) -> str:
    if not isinstance(raw, str):
        raise DataError(f"{field}: expected Base64 text")
    try:
        data = base64.b64decode(raw, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise DataError(f"{field}: invalid Base64") from exc
    if len(data) != 20 or base64.b64encode(data).decode("ascii") != raw:
        raise DataError(f"{field}: expected canonical Base64 of 20-byte SHA-1 digest")
    return raw


def extract_echo(response: object) -> dict[str, str]:
    """Accept the Echo response or an adapter wrapper {echo: <Echo response>}."""
    try:
        if not isinstance(response, dict):
            raise KeyError("response")
        payload = response.get("echo", response)
        tls = payload["http"]["tls"]
        return {field: required_sha1(tls[field], field) for field in FIELDS}
    except (KeyError, TypeError, AttributeError) as exc:
        raise DataError("Echo JSON missing http.tls and/or required SHA-1 fields") from exc


def columns(rows: list[dict]) -> tuple[str, ...]:
    if not isinstance(rows, list) or not rows:
        raise DataError("observations must be a nonempty list")
    if not all(isinstance(row, dict) for row in rows):
        raise DataError("observations must contain objects")
    first = tuple(field for field in FIELDS if field in rows[0])
    for row in rows:
        if any(field not in FIELDS for field in row):
            raise DataError("Unknown fingerprint field")
        if tuple(field for field in FIELDS if field in row) != first:
            raise DataError("All observations must contain the same columns")
        for field in first:
            required_sha1(row[field], field)
    return first


def compact(rows: list[dict], *, unique_limit: int, combination_limit: int,
            allowed_fields: tuple[str, ...] = FIELDS) -> list[dict]:
    """Monotone column elimination; preserve real joint observations of retained fields.

    allowed_fields comes from the previously stored record, if any. A dropped
    column is never reconstructed from later observations.
    """
    if unique_limit < 1 or combination_limit < 1:
        raise DataError("Limits must be positive")
    allowed = tuple(field for field in FIELDS if field in allowed_fields)
    for row in rows:
        if not isinstance(row, dict) or any(field not in row for field in allowed):
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
        victim = max(allowed, key=lambda field: (uniques[field], FIELDS.index(field)))
        allowed = tuple(field for field in allowed if field != victim)


def merge_observations(old: list[dict] | None, fresh: list[dict], *,
                       unique_limit: int, combination_limit: int) -> list[dict]:
    fresh_columns = columns(fresh)
    if old is None:
        return compact(fresh, unique_limit=unique_limit, combination_limit=combination_limit)
    old_columns = columns(old)
    common = tuple(field for field in FIELDS if field in old_columns and field in fresh_columns)
    joined = [{field: row[field] for field in common} for row in old + fresh]
    return compact(joined, unique_limit=unique_limit, combination_limit=combination_limit,
                   allowed_fields=common)


def record_identity(case: dict, resolved_environment: dict) -> dict:
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


def record_path(root: Path, identity: dict) -> Path:
    return root / "data" / "observations" / safe_part(identity["language"]) / safe_part(identity["client"]) / (digest(identity) + ".json")
