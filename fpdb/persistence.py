"""Merge valid artifacts into the repository."""
from __future__ import annotations

from pathlib import Path
from typing import cast

from fpdb.core import (DataError, columns, digest, merge_observations,
                       json_object, read_json, record_path, save_json)
from fpdb.evidence import merge_evidence, validate_evidence
from fpdb.planner import load_config
from fpdb.index import build as build_index
from fpdb.schema import JSONObject, Record


def _check_record(record: JSONObject) -> Record:
    if record.get("schema_version") != 1 or not isinstance(record.get("identity"), dict):
        raise DataError("Unsupported record")
    identity = json_object(record["identity"])
    if record.get("record_id") != digest(identity):
        raise DataError("Record ID doesn't match identity")
    requested = {key: identity[key] for key in (
        "target", "language", "client", "runtime_version", "client_version",
        "platform", "runner", "revision")}
    if "verify" in identity:
        if identity["verify"] is not False:
            raise DataError("verify must be false or omitted for the client's default")
        requested["verify"] = False
    if record.get("case_id") != digest(requested):
        raise DataError("case_id does not match the requested configuration")
    environment = identity.get("environment")
    if not isinstance(environment, dict) or not isinstance(environment.get("tls_backend"), dict):
        raise DataError("Missing observed TLS backend")
    columns(record["observations"])
    if "evidence" in record:
        validate_evidence(record["evidence"])
    if "evidence_truncated" in record and type(record["evidence_truncated"]) is not bool:
        raise DataError("Invalid evidence_truncated")
    return cast(Record, record)


def persist(root: Path, inbox: Path, *, consume: bool = False) -> tuple[int, int]:
    """Merge artifacts. Consuming callers hold the inbox lock through cleanup."""
    config = load_config(root)
    sampling = config["sampling"]
    sources = set(inbox.rglob("*.json"))
    if consume:
        sources.update(path.with_suffix(".json") for path in inbox.rglob("*.merged"))
    files = sorted(sources)
    changed = 0
    for artifact in files:
        checkpoint = artifact.with_suffix(".merged")
        prepared = json_object(read_json(checkpoint)) if consume and checkpoint.is_file() else None
        fresh = _check_record(json_object(prepared["record"] if prepared is not None else read_json(artifact)))
        fresh_truncated = fresh.get("evidence_truncated", False)
        path = record_path(root, fresh["identity"])
        existing = _check_record(json_object(read_json(path))) if path.exists() else None
        before = digest(existing) if consume and existing is not None else None
        if prepared is not None:
            if existing != fresh and prepared["before"] != before:
                raise DataError(f"Observation changed since preparing {checkpoint}")
        elif existing is not None:
            if existing["identity"] != fresh["identity"] or existing["case_id"] != fresh["case_id"]:
                raise DataError("Record collision or inconsistent case_id")
            combined = merge_observations(existing["observations"], fresh["observations"],
                unique_limit=int(sampling["max_unique_per_field"]),
                combination_limit=int(sampling["max_combinations"]))
            evidence, lost = merge_evidence(existing.get("evidence", []), fresh.get("evidence", []))
            fresh = {**existing, "observations": combined}
            if evidence:
                fresh["evidence"] = evidence
            if "evidence" in fresh:
                fresh["evidence_truncated"] = (lost or existing.get("evidence_truncated", False)
                                               or fresh_truncated)
        if consume and prepared is None:
            # Save the final value before writing it so a restart cannot add its counts again.
            save_json(checkpoint, {"before": before, "record": fresh})
        if existing != fresh:
            save_json(path, fresh)
            changed += 1
    build_index(root)
    if consume:
        for artifact in files:
            # A remaining checkpoint can be replayed even after its raw artifact is gone.
            artifact.unlink(missing_ok=True)
            artifact.with_suffix(".merged").unlink()
    return changed, len(files)


