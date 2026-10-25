"""Generate small JSON arrays addressable by TLS SHA-1 field and value."""
from __future__ import annotations

import json
from pathlib import Path

from fpdb.core import FIELDS, ROOT, DataError, canonical, columns, digest, read_json


def build(root: Path = ROOT) -> bool:
    directory = root / "data" / "index"
    buckets: dict[Path, dict[str, dict]] = {}
    for source in sorted((root / "data" / "observations").rglob("*.json")):
        record = read_json(source)
        identity = record["identity"]
        if record.get("schema_version") != 1 or record.get("record_id") != digest(identity):
            raise DataError(f"Invalid observation record: {source}")
        columns(record["observations"])
        client = {key: identity[key] for key in (
            "target", "language", "client", "runtime_version", "client_version", "platform"
        )}
        client["environment"] = identity["environment"]
        for row in record["observations"]:
            for field, sha1 in row.items():
                # '_' represents '/' (which cannot be used in a filename).
                # '+' and '=' remain unchanged, matching regular Base64 names.
                path = directory / field / (sha1.replace("/", "_") + ".json")
                entry = {**client, **{key: value for key, value in row.items() if key != field}}
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
