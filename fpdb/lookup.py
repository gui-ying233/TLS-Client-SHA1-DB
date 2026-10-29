"""Rank per-capture SHA-1 matches without stitching separate handshakes together."""
from __future__ import annotations

from pathlib import Path
from typing import cast

from fpdb.core import FIELDS, canonical, json_array, read_json, required_sha1
from fpdb.evidence import TEXT_AUX
from fpdb.schema import Candidate, CandidateIdentity, IndexEntry, MatchDetail

MATCH_NAMES = {1: "single-field candidate", 2: "two-field joint match", 3: "three-field exact tuple"}


def lookup(root: Path, supplied: dict[str, str], limit: int = 100,
           *, require_all: bool = False) -> list[Candidate]:
    if not supplied or any(field not in FIELDS for field in supplied):
        raise ValueError("Supply one or more supported TLS SHA-1 fields")
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be 1..1000")
    for name, sha in supplied.items():
        required_sha1(sha, name)

    # A shard entry corresponds to ONE captured row.  Reconstruct its missing
    # shard-key field, then calculate the best *single-row* match; never combine
    # values from unrelated observations (even within one client identity).
    candidates: dict[str, Candidate] = {}
    per_candidate_details: dict[str, dict[str, int]] = {}
    for field, sha in supplied.items():
        path = root / "data" / "index" / field / (sha.replace("/", "_") + ".json")
        if not path.is_file():
            continue
        for entry in cast(list[IndexEntry], json_array(read_json(path))):
            fingerprints = {name: (sha if name == field else entry.get(name))
                            for name in FIELDS}
            matched_names = tuple(name for name, value in supplied.items()
                                  if fingerprints.get(name) == value)
            matched = len(matched_names)
            if require_all and matched != len(supplied):
                continue
            if not matched:
                continue
            identity: CandidateIdentity = {
                "target": entry.get("target"), "language": entry.get("language"),
                "client": entry.get("client"), "runtime_version": entry.get("runtime_version"),
                "client_version": entry.get("client_version"), "platform": entry.get("platform"),
                "environment": entry.get("environment"),
            }
            if "verify" in entry:
                identity["verify"] = entry["verify"]
            key = canonical(identity)
            current = candidates.get(key)
            if current is not None and matched < current["matched_fields"]:
                continue
            if current is None or matched > current["matched_fields"]:
                current = Candidate(**identity, match=MATCH_NAMES[matched],
                                    matched_fields=matched, supplied_fields=len(supplied),
                                    sample_count=0, details=[])
                candidates[key] = current
                per_candidate_details[key] = {}
            detail = cast(MatchDetail, {name: value for name, value in fingerprints.items() if value is not None})
            length = entry.get("tlsClientHelloLength")
            if length is not None:
                detail["tlsClientHelloLength"] = length
            for name in TEXT_AUX:
                value = entry.get(name)
                if value is not None:
                    detail[name] = value
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

    def candidate_priority(item: Candidate) -> tuple[int, int, str, str, str]:
        return (-item["matched_fields"], -item["sample_count"],
                item["language"] or "", item["target"] or "", item["runtime_version"] or "")

    return sorted(candidates.values(), key=candidate_priority)[:limit]
