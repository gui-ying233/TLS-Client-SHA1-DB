"""Discover missing combinations, keeping selection policy separate from GitHub Actions."""
from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from typing import Literal, cast

from fpdb.core import DataError, digest, json_array, json_object, read_json
from fpdb.schema import Case, CaseIdentity, Config, Job, Target
from fpdb.versions import discover, minimum_runtime, sort_key


def load_config(root: Path) -> Config:
    config = json_object(read_json(root / "config.json"))
    if config.get("schema_version") != 1 or not isinstance(config.get("targets"), list):
        raise DataError("Invalid config.json")
    environments = root / "config.environments.json"
    if environments.exists():
        additional = json_object(read_json(environments))
        if not isinstance(additional.get("targets"), list):
            raise DataError("Invalid config.environments.json")
        json_array(config["targets"]).extend(json_array(additional["targets"]))
    return cast(Config, config)


def completed_case_ids(root: Path) -> set[str]:
    ids: set[str] = set()
    for path in (root / "data" / "observations").rglob("*.json"):
        record = json_object(read_json(path))
        if record.get("schema_version") == 1:
            case_id = record["case_id"]
            if not isinstance(case_id, str):
                raise DataError("case_id must be a string")
            ids.add(case_id)
    return ids


def make_case(target: Target, runtime: str, client: str, *, verify: Literal[False] | None = None) -> Case:
    identity: CaseIdentity = {
        "target": target["id"],
        "language": target["language"],
        "client": target["client"],
        "runtime_version": runtime,
        "client_version": client,
        "platform": target.get("platform", "linux-x86_64"),
        "runner": target.get("runner", "ubuntu-24.04"),
        "revision": str(target.get("revision", "1")),
    }
    if verify is False:
        identity["verify"] = False
    elif verify is not None:
        raise DataError("verify must be false or omitted for the client's default")
    return {**identity, "id": digest(identity)}


def job_batches(cases: list[Case], max_cases: int = 8, max_jobs: int | None = None) -> list[Job]:
    """Fill compatible runtime batches first, then stop at the Job budget."""
    groups: dict[tuple[str, str, str, str, bool], list[Case]] = {}
    for case in cases:
        key = (case["runner"], case["platform"], case["language"],
               case["runtime_version"],
               case["language"] == "python" and case["target"].startswith("env-"))
        groups.setdefault(key, []).append(case)

    per_language: defaultdict[str, list[list[Case]]] = defaultdict(list)
    for key, items in groups.items():
        per_language[key[2]].append(items)
    # Largest compatible groups first. Stable ties keep the discovery/version order.
    def batch_priority(items: list[Case]) -> int:
        return -len(items)

    queues = {language: deque(sorted(buckets, key=batch_priority))
              for language, buckets in per_language.items()}
    jobs: list[Job] = []
    while any(queues.values()) and (max_jobs is None or len(jobs) < max_jobs):
        for language in sorted(queues):
            if not queues[language]:
                continue
            items = queues[language][0]
            batch = items[:max_cases]
            del items[:max_cases]
            if not items:
                queues[language].popleft()
            jobs.append({"case": batch[0], "cases": batch,
                         "id": digest([case["id"] for case in batch])})
            if max_jobs is not None and len(jobs) >= max_jobs:
                break
    return jobs


def _matches_range(pair: tuple[str, str], constraint: dict[str, str]) -> bool:
    runtime, client = pair
    for key, value in (("runtime", runtime), ("client", client)):
        if key in constraint and value != constraint[key]:
            return False
        if key + "_min" in constraint and sort_key(value) < sort_key(constraint[key + "_min"]):
            return False
        if key + "_max" in constraint and sort_key(value) > sort_key(constraint[key + "_max"]):
            return False
    return True


def _compatible(pair: tuple[str, str], target: Target) -> bool:
    rules = target.get("compatibility", {})
    includes = rules.get("include", [])
    excludes = rules.get("exclude", [])
    matches = (not includes or any(_matches_range(pair, rule) for rule in includes)) and not any(
        _matches_range(pair, rule) for rule in excludes)
    if not matches:
        return False
    required = minimum_runtime(target["client_versions"], pair[1])
    return required is None or sort_key(pair[0]) >= sort_key(required)


def _pairs(target: Target, mode: str, active_runtime_count: int) -> list[tuple[str, str]]:
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


def plan(root: Path, *, mode: str, target_filter: str = "",
         recheck: bool = False, batch_size: int | None = None) -> list[Case]:
    config = load_config(root)
    if not config["profiles"].get(mode, {}).get("enabled", False):
        raise DataError(f"Profile {mode!r} is disabled in config.json")
    active_runtime_count = int(config["execution"]["active_runtime_count"])
    max_matrix_size = min(256, int(config["execution"].get("max_matrix_size", 200)))
    limit = int(batch_size if batch_size is not None else config["execution"]["batch_size"])
    if not (1 <= limit <= max_matrix_size):
        raise DataError(f"batch_size must be between 1 and {max_matrix_size}")
    completed: set[str] = completed_case_ids(root) if not recheck else set()
    seen: set[str] = set()
    cases: list[Case] = []
    verify_modes = config.get("tls_verify_modes", ["default"])
    if not verify_modes or any(mode not in ("default", "disabled") for mode in verify_modes):
        raise DataError("tls_verify_modes must contain default and/or disabled")
    target_ids: set[str] = set()
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
            for verify_mode in verify_modes:
                case = make_case(target, runtime, client,
                                 verify=False if verify_mode == "disabled" else None)
                if case["id"] in seen or (case["id"] in completed and mode not in target.get("recheck_modes", [])):
                    continue
                seen.add(case["id"])
                cases.append(case)
    if target_filter and target_filter not in target_ids:
        raise DataError(f"Unknown or disabled target {target_filter}")
    # batch_size now limits runner Jobs, not individual fingerprint cases.
    # Select from all pending combinations so reuse never leaves the matrix half empty.
    jobs = job_batches(cases, max_jobs=limit)
    return [case for job in jobs for case in job["cases"]]
