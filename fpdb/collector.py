"""Execute adapter commands. Each probe subprocess must create one NEW TLS connection."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from fpdb.core import DataError, FIELDS, compact, extract_echo, record_identity, digest
from fpdb.evidence import extract_joint, merge_evidence
from fpdb.planner import load_config

log = logging.getLogger(__name__)


def _command(template: list[str], replacements: dict[str, str]) -> list[str]:
    if not isinstance(template, list) or not template or not all(isinstance(x, str) for x in template):
        raise DataError("Adapter command must be a nonempty argv array")
    try:
        return [part.format_map(replacements) for part in template]
    except (ValueError, KeyError) as exc:
        raise DataError(f"Invalid adapter command placeholder: {exc}") from exc


def _run(command: list[str], root: Path, timeout: int, environment: dict, *, retry_diagnostics: bool = False) -> dict:
    process = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True,
                             timeout=timeout, check=False)
    if process.returncode != 0:
        raise DataError(f"Adapter exited {process.returncode}; stderr={process.stderr[-2500:]!r}")
    if retry_diagnostics and process.stderr.strip():
        log.warning("Adapter retry diagnostics: %s", process.stderr[-2500:].strip())
    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise DataError("Adapter stdout must be exactly one JSON document") from exc


def _echo_shape(payload: object) -> dict:
    """Print structural diagnostics only; never log response bodies or request headers."""
    def keys(value: object) -> list[str]:
        return sorted(str(key) for key in value)[:24] if isinstance(value, dict) else []

    body = payload.get("echo", payload) if isinstance(payload, dict) else payload
    http = body.get("http") if isinstance(body, dict) else None
    tls = http.get("tls") if isinstance(http, dict) else None
    return {
        "body_type": type(body).__name__,
        "body_keys": keys(body),
        "http_type": type(http).__name__,
        "http_keys": keys(http),
        "tls_type": type(tls).__name__,
        "tls_keys": keys(tls),
    }


def _require_environment(payload: dict, case: dict) -> dict:
    environment = payload.get("environment")
    if not isinstance(environment, dict):
        raise DataError("Adapter must report environment object")
    backend = environment.get("tls_backend")
    if not isinstance(backend, dict) or not all(isinstance(backend.get(k), str) and backend[k] for k in ("name", "version")):
        raise DataError("Adapter must report tls_backend.name and tls_backend.version")
    for key in ("os", "arch"):
        if not isinstance(environment.get(key), str) or not environment[key]:
            raise DataError(f"Adapter environment requires {key}")
    requested_runtime, actual_runtime = case["runtime_version"], payload.get("runtime_version")
    if not isinstance(actual_runtime, str) or not actual_runtime:
        raise DataError("Adapter must report actual runtime_version")
    if requested_runtime not in ("system", "stable") and not (
        actual_runtime == requested_runtime or
        (requested_runtime.count(".") < 2 and actual_runtime.startswith(requested_runtime + "."))
    ):
        raise DataError(f"Adapter runtime_version={actual_runtime!r} differs from {requested_runtime!r}")
    if payload.get("client_version") != case["client_version"]:
        raise DataError("Adapter client_version differs from requested version")
    environment["runtime_resolved_version"] = actual_runtime
    # Keep metadata small, stable, and JSON-safe (no secrets or transient data).
    allowed = {"os", "arch", "tls_backend", "image", "dependencies", "runtime_resolved_version"}
    if any(k not in allowed for k in environment):
        raise DataError("Unexpected environment keys in adapter response")
    if "runtime_resolved_version" in environment and not isinstance(environment["runtime_resolved_version"], str):
        raise DataError("Invalid resolved runtime version")
    if "image" in environment and not isinstance(environment["image"], str):
        raise DataError("environment.image must be string")
    if "dependencies" in environment:
        deps = environment["dependencies"]
        if not isinstance(deps, dict) or not all(
            isinstance(name, str) and isinstance(version, str) and name and version
            for name, version in deps.items()
        ):
            raise DataError("environment.dependencies must map package names to version strings")
    return environment


def collect(root: Path, case: dict) -> dict | None:
    config = load_config(root)
    target = next((target for target in config["targets"] if target["id"] == case["target"] and target.get("enabled")), None)
    if not target:
        raise DataError("Unknown enabled target")
    # Rebuild the case server-side so an injected matrix value cannot choose an arbitrary command.
    from fpdb.planner import _case
    expected = _case(target, case["runtime_version"], case["client_version"])
    if expected != case:
        raise DataError("Matrix case does not match configured target")

    sampling = config["sampling"]
    first = int(sampling["initial_handshakes"])
    expanded = int(sampling["expanded_handshakes"])
    if first < 1 or expanded < first:
        raise DataError("Invalid sampling counts")
    timeout = int(sampling["timeout_seconds"])
    retry = int(sampling["retry_per_handshake"])
    if retry != 1:
        raise DataError("Current policy requires exactly one retry per failed handshake")
    replacements = {key: str(val) for key, val in case.items() if isinstance(val, (str, int, float))}
    replacements["endpoint"] = config["endpoint"]
    replacements["python_bin"] = os.environ.get("FPDB_PYTHON_BIN") or sys.executable
    adapter = target["adapter"]
    runtime_env = os.environ.copy()
    runtime_env.update({"FPDB_ENDPOINT": config["endpoint"], "FPDB_TARGET": case["target"]})
    # The collector uses its own control Python. Each Python probe (and pip
    # prepare command) must resolve libpython from the selected target build,
    # not from the control interpreter's LD_LIBRARY_PATH.
    if case["language"] == "python" and sys.platform == "linux":
        target_python = Path(replacements["python_bin"]).resolve()
        runtime_env["LD_LIBRARY_PATH"] = str(target_python.parent.parent / "lib")
    # Avoid accidental proxy-based TLS termination. Adapter may opt into a proxy
    # only by explicit own code; proxy variables are not inherited here.
    for name in list(runtime_env):
        if name.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}:
            runtime_env.pop(name, None)
    for key, value in adapter.get("env", {}).items():
        if not isinstance(value, str):
            raise DataError("adapter.env values must be strings")
        runtime_env[key] = value.format_map(replacements)

    prepare = adapter.get("prepare", [])
    if prepare:
        for trial in range(2):
            try:
                prepared = subprocess.run(_command(prepare, replacements), cwd=root, env=runtime_env,
                                          capture_output=True, text=True,
                                          timeout=int(sampling.get("prepare_timeout_seconds", 900)), check=False)
                if prepared.returncode:
                    raise DataError(f"prepare exited {prepared.returncode}: {prepared.stderr[-1000:]}")
                break
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                log.warning("case=%s prepare attempt=%d failed: %s", case["id"], trial + 1, exc)
        else:
            return None

    command = _command(adapter["probe"], replacements)
    observed = []
    joint_samples = []
    resolved_environment = None
    for sample_index in range(expanded):
        result = None
        for trial in range(retry + 1):
            try:
                # Keep the first attempt quiet. On the retry, request safe HTTP
                # metadata from adapters without changing the TLS connection.
                probe_env = ({**runtime_env, "FPDB_DIAGNOSTIC_RETRY": "1"}
                             if trial else runtime_env)
                result = _run(command, root, timeout, probe_env,
                              retry_diagnostics=bool(trial))
                if not isinstance(result, dict):
                    raise DataError("Adapter output must be JSON object")
                try:
                    observed_row = extract_echo(result)
                except DataError:
                    if trial:
                        log.warning("case=%s sample=%d retry Echo shape: %s",
                                    case["id"], sample_index + 1, _echo_shape(result))
                    raise
                observed_env = _require_environment(result, case)
                if target.get("capture_profile"):
                    observed_env["capture_profile"] = target["capture_profile"]
                if resolved_environment is None:
                    resolved_environment = observed_env
                elif resolved_environment != observed_env:
                    raise DataError("Environment changed within one sampling run")
                joint_samples.append({**extract_joint(result), "count": 1})
                observed.append(observed_row)
                break
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                log.warning("case=%s sample=%d attempt=%d failed: %s", case["id"], sample_index + 1, trial + 1, exc)
        if result is None or len(observed) <= sample_index:
            log.warning("Skipping case %s after one retry", case["id"])
            return None
        if len(observed) == first and len({tuple(row[field] for field in FIELDS) for row in observed}) == 1:
            break

    observations = compact(observed,
        unique_limit=int(sampling["max_unique_per_field"]),
        combination_limit=int(sampling["max_combinations"]))
    # This independent, bounded sample store retains real joint triples and
    # metadata even if compact() must suppress an unstable index column.
    evidence, lost = merge_evidence([], joint_samples)
    identity = record_identity(case, resolved_environment)
    return {
        "schema_version": 1,
        "case_id": case["id"],
        "record_id": digest(identity),
        "identity": identity,
        "observations": observations,
        "evidence": evidence,
        "evidence_truncated": lost,
    }
