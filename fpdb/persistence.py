"""Merge valid artifacts into the repository."""
from __future__ import annotations

from pathlib import Path

from fpdb.core import (DataError, columns, digest, merge_observations,
                       read_json, record_path, save_json)
from fpdb.planner import load_config


def _check_record(record: dict) -> None:
    if record.get("schema_version") != 1 or not isinstance(record.get("identity"), dict):
        raise DataError("Unsupported record")
    identity = record["identity"]
    if record.get("record_id") != digest(identity):
        raise DataError("Record ID doesn't match identity")
    requested = {key: identity[key] for key in (
        "target", "language", "client", "runtime_version", "client_version",
        "platform", "runner", "revision")}
    if record.get("case_id") != digest(requested):
        raise DataError("case_id does not match the requested configuration")
    environment = identity.get("environment")
    if not isinstance(environment, dict) or not isinstance(environment.get("tls_backend"), dict):
        raise DataError("Missing observed TLS backend")
    columns(record["observations"])


def persist(root: Path, inbox: Path) -> tuple[int, int]:
    config = load_config(root)
    sampling = config["sampling"]
    files = sorted(inbox.rglob("*.json")) if inbox.exists() else []
    changed = 0
    for artifact in files:
        fresh = read_json(artifact)
        _check_record(fresh)
        path = record_path(root, fresh["identity"])
        if path.exists():
            existing = read_json(path)
            _check_record(existing)
            if existing["identity"] != fresh["identity"] or existing["case_id"] != fresh["case_id"]:
                raise DataError("Record collision or inconsistent case_id")
            combined = merge_observations(existing["observations"], fresh["observations"],
                unique_limit=int(sampling["max_unique_per_field"]),
                combination_limit=int(sampling["max_combinations"]))
            fresh = {**existing, "observations": combined}
        if not path.exists() or read_json(path) != fresh:
            save_json(path, fresh)
            changed += 1
    return changed, len(files)


