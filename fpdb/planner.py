"""Discover missing combinations, keeping selection policy separate from GitHub Actions."""
from __future__ import annotations

import hashlib
from pathlib import Path

from fpdb.core import DataError, digest, read_json
from fpdb.versions import discover, sort_key


def load_config(root: Path) -> dict:
    config = read_json(root / "config.json")
    if config.get("schema_version") != 1 or not isinstance(config.get("targets"), list):
        raise DataError("Invalid config.json")
    return config


def completed_case_ids(root: Path) -> set[str]:
    ids = set()
    for path in (root / "data" / "observations").rglob("*.json"):
        record = read_json(path)
        if record.get("schema_version") == 1:
            ids.add(record["case_id"])
    return ids


def _case(target: dict, runtime: str, client: str) -> dict:
    case = {
        "target": target["id"],
        "language": target["language"],
        "client": target["client"],
        "runtime_version": runtime,
        "client_version": client,
        "platform": target.get("platform", "linux-x86_64"),
        "runner": target.get("runner", "ubuntu-24.04"),
        "revision": str(target.get("revision", "1")),
    }
    case["id"] = digest(case)
    return case


def _matches_range(pair: tuple[str, str], constraint: dict) -> bool:
    runtime, client = pair
    for key, value in (("runtime", runtime), ("client", client)):
        if key in constraint and value != constraint[key]:
            return False
        if key + "_min" in constraint and sort_key(value) < sort_key(constraint[key + "_min"]):
            return False
        if key + "_max" in constraint and sort_key(value) > sort_key(constraint[key + "_max"]):
            return False
    return True


def _compatible(pair: tuple[str, str], target: dict) -> bool:
    rules = target.get("compatibility", {})
    includes = rules.get("include", [])
    excludes = rules.get("exclude", [])
    return (not includes or any(_matches_range(pair, rule) for rule in includes)) and not any(
        _matches_range(pair, rule) for rule in excludes)


def _pairs(target: dict, mode: str, active_runtime_count: int) -> list[tuple[str, str]]:
    runtimes = discover(target["runtime_versions"])
    clients = discover(target["client_versions"])
    if not runtimes or not clients:
        raise DataError(f"No discovered versions for target {target['id']}")

    if mode == "incremental":
        # Select the most recent compatible client for each active runtime.
        return [(runtime, next(client for client in clients if _compatible((runtime, client), target)))
                for runtime in runtimes[:active_runtime_count]
                if any(_compatible((runtime, c), target) for c in clients)]
    if mode == "historical":
        baseline = target.get("historical_baseline_client", clients[0])
        if baseline not in clients:
            raise DataError(f"Historical baseline {baseline!r} not in client versions for {target['id']}")
        anchor = target.get("historical_anchor_runtime", runtimes[0])
        if anchor not in runtimes:
            raise DataError(f"Historical anchor {anchor!r} not in runtime versions for {target['id']}")
        return [pair for pair in dict.fromkeys(
            [(runtime, baseline) for runtime in runtimes] +
            [(anchor, client) for client in clients]
        ) if _compatible(pair, target)]
    if mode == "full":
        return [(runtime, client) for runtime in runtimes for client in clients
                if _compatible((runtime, client), target)]
    raise DataError(f"Unknown mode {mode!r}")


def plan(root: Path, *, mode: str, target_filter: str = "", seed: str = "0",
         recheck: bool = False, batch_size: int | None = None) -> list[dict]:
    config = load_config(root)
    if not config["profiles"].get(mode, {}).get("enabled", False):
        raise DataError(f"Profile {mode!r} is disabled in config.json")
    active_runtime_count = int(config["execution"]["active_runtime_count"])
    max_matrix_size = min(256, int(config["execution"].get("max_matrix_size", 200)))
    limit = int(batch_size if batch_size is not None else config["execution"]["batch_size"])
    if not (1 <= limit <= max_matrix_size):
        raise DataError(f"batch_size must be between 1 and {max_matrix_size}")
    completed = completed_case_ids(root) if not recheck else set()
    seen = set()
    cases = []
    target_ids = set()
    for target in config["targets"]:
        if not target.get("enabled", False):
            continue
        target_id = target["id"]
        if target_id in target_ids:
            raise DataError(f"Duplicate target id {target_id}")
        target_ids.add(target_id)
        if target_filter and target_filter != target_id:
            continue
        for runtime, client in _pairs(target, mode, active_runtime_count):
            case = _case(target, runtime, client)
            if case["id"] in seen or (case["id"] in completed and mode not in target.get("recheck_modes", [])):
                continue
            seen.add(case["id"])
            cases.append(case)
    if target_filter and target_filter not in target_ids:
        raise DataError(f"Unknown or disabled target {target_filter}")
    if target_filter or mode == "full":
        if mode == "incremental":
            cases.sort(key=lambda case: sort_key(case["runtime_version"]), reverse=True)
        else:
            cases.sort(key=lambda case: hashlib.sha256(f"{seed}:{case['id']}".encode()).hexdigest())
        return cases[:limit]

    # Allocate a round-robin slot to each language, then each implementation.
    # A long Node/Python version history must not monopolize a limited matrix.
    from collections import defaultdict, deque
    grouped = defaultdict(lambda: defaultdict(list))
    for case in cases:
        grouped[case["language"]][case["target"]].append(case)
    per_language = {}
    for language, targets in grouped.items():
        queues = {}
        for name, items in targets.items():
            if mode == "incremental":
                items.sort(key=lambda item: sort_key(item["runtime_version"]), reverse=True)
            else:
                items.sort(key=lambda item: hashlib.sha256(f"{seed}:{item['id']}".encode()).hexdigest())
            queues[name] = deque(items)
        result = deque()
        while any(queues.values()):
            for name in sorted(queues, key=lambda key: hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()):
                if queues[name]:
                    result.append(queues[name].popleft())
        per_language[language] = result

    ordered = []
    while any(per_language.values()) and len(ordered) < limit:
        for language in sorted(per_language, key=lambda key: hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()):
            if per_language[language]:
                ordered.append(per_language[language].popleft())
                if len(ordered) >= limit:
                    break
    return ordered
