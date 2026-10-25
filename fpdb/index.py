"""Generate small JSON arrays addressable by TLS SHA-1 field and value."""
from __future__ import annotations

import json
from pathlib import Path

from fpdb.core import FIELDS, ROOT, DataError, canonical, columns, digest, read_json
from fpdb.evidence import validate_evidence, AUX


def build(root: Path = ROOT) -> bool:
    directory = root / "data" / "index"
    buckets: dict[Path, dict[str, dict]] = {}
    for source in sorted((root / "data" / "observations").rglob("*.json")):
        record = read_json(source)
        identity = record["identity"]
        if record.get("schema_version") != 1 or record.get("record_id") != digest(identity):
            raise DataError(f"Invalid observation record: {source}")
        columns(record["observations"])
        if "evidence" in record:
            validate_evidence(record["evidence"])
        client = {key: identity[key] for key in (
            "target", "language", "client", "runtime_version", "client_version", "platform"
        )}
        client["environment"] = identity["environment"]
        # Evidence contains exact joint captures, including fields deliberately
        # dropped from the older compact summary. Never cross-join hash lists.
        source_rows = list(record.get("evidence", []))
        # Older schema-v1 captures have only compact observations. Keep any
        # legacy summary tuple not corroborated by new joint evidence, so
        # transitioning a record never silently removes historical matches.
        for summary in record["observations"]:
            if not any(all(item.get(k) == v for k, v in summary.items())
                       for item in source_rows):
                source_rows.append(summary)
        for row in source_rows:
            for field in FIELDS:
                if field not in row:
                    continue
                sha1 = row[field]
                # '_' represents '/' (which cannot be used in a filename).
                # '+' and '=' remain unchanged, matching regular Base64 names.
                path = directory / field / (sha1.replace("/", "_") + ".json")
                entry = {**client, **{key: value for key, value in row.items()
                                     if key != field and (key in FIELDS or key in AUX)}}
                if "count" in row:
                    entry["sample_count"] = row["count"]
                buckets.setdefault(path, {})[canonical(entry)] = entry

    changed = False
    for path in sorted(buckets):
        entries = [item for _, item in sorted(buckets[path].items())]
        content = json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        if path.exists() and path.read_text(encoding="utf-8") == content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
        changed = True

    for path in directory.rglob("*.json") if directory.exists() else ():
        if path not in buckets:
            path.unlink()
            changed = True
    if directory.exists():
        for field in FIELDS:
            folder = directory / field
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()

    legacy = root / "data" / "index.json"
    if legacy.exists():
        legacy.unlink()
        changed = True
    return changed


if __name__ == "__main__":
    print("index_changed=" + str(build()).lower())
