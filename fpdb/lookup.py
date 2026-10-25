"""Rank per-capture SHA-1 matches without stitching separate handshakes together."""
from __future__ import annotations

from pathlib import Path

from fpdb.core import FIELDS, canonical, read_json, required_sha1

AUX = ("tlsClientHelloLength", "tlsCipher", "tlsVersion", "tlsAlpn")
MATCH_NAMES = {1: "single-field candidate", 2: "two-field joint match", 3: "three-field exact tuple"}


def lookup(root: Path, supplied: dict[str, str], limit: int = 100,
           *, require_all: bool = False) -> list[dict]:
    if not supplied or any(field not in FIELDS for field in supplied):
        raise ValueError("Supply one or more supported TLS SHA-1 fields")
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be 1..1000")
    for name, sha in supplied.items():
        required_sha1(sha, name)

    # A shard entry corresponds to ONE captured row.  Reconstruct its missing
    # shard-key field, then calculate the best *single-row* match; never combine
    # values from unrelated observations (even within one client identity).
    candidates: dict[str, dict] = {}
    per_candidate_details: dict[str, dict[str, int]] = {}
    for field, sha in supplied.items():
        path = root / "data" / "index" / field / (sha.replace("/", "_") + ".json")
        if not path.is_file():
            continue
        for entry in read_json(path):
            fingerprints = {name: (sha if name == field else entry.get(name))
                            for name in FIELDS}
            matched_names = tuple(name for name, value in supplied.items()
                                  if fingerprints.get(name) == value)
            matched = len(matched_names)
            if require_all and matched != len(supplied):
                continue
            if not matched:
                continue
            identity = {key: entry.get(key) for key in (
                "target", "language", "client", "runtime_version", "client_version",
                "platform", "environment")}
            key = canonical(identity)
            current = candidates.get(key)
            if current is not None and matched < current["matched_fields"]:
                continue
            if current is None or matched > current["matched_fields"]:
                current = {**identity, "match": MATCH_NAMES[matched],
                           "matched_fields": matched, "supplied_fields": len(supplied),
                           "sample_count": 0, "details": []}
                candidates[key] = current
                per_candidate_details[key] = {}
            detail = {name: value for name, value in fingerprints.items() if value is not None}
            detail.update({name: entry[name] for name in AUX if name in entry})
            # Attach which fields matched to each co-observed tuple; different
            # tuples from one client can corroborate different input pairs.
            detail["matched_field_names"] = list(matched_names)
            detail_key = canonical(detail)
            count = entry.get("sample_count", 0)
            previous = per_candidate_details[key].get(detail_key)
            if previous is None:
                per_candidate_details[key][detail_key] = count
                current["details"].append(detail)
                current["sample_count"] += count
            elif count > previous:
                per_candidate_details[key][detail_key] = count
                current["sample_count"] += count - previous

    return sorted(candidates.values(), key=lambda item: (
        -item["matched_fields"], -item["sample_count"],
        item["language"] or "", item["target"] or "", item["runtime_version"] or ""))[:limit]
