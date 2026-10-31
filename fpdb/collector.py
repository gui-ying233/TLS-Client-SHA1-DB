"""Execute adapter commands. Each probe subprocess must create one NEW TLS connection."""
from __future__ import annotations

import errno
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

from fpdb.core import DataError, FIELDS, compact, extract_echo, json_array, json_object, record_identity, digest
from fpdb.evidence import extract_joint, merge_evidence
from fpdb.planner import load_config, make_case
from fpdb.schema import Case, Environment, Evidence, Fingerprint, JSONObject, JSONValue, Record

log = logging.getLogger(__name__)


def _command(template: object, replacements: dict[str, str]) -> list[str]:
    parts = json_array(template)
    if not parts or not all(isinstance(part, str) for part in parts):
        raise DataError("Adapter command must be a nonempty argv array")
    try:
        return [part.format_map(replacements) for part in cast(list[str], parts)]
    except (ValueError, KeyError) as exc:
        raise DataError(f"Invalid adapter command placeholder: {exc}") from exc


def _node_cli_entry(command: list[str], root: Path) -> list[str] | None:
    """Resolve npm's declared CLI entry when a Windows bind mount cannot execute .bin.

    Only known local probe executables are considered, and the resolved entry
    must remain inside its package directory.
    """
    cli = Path(command[0]).name
    package = {"tsx": "tsx", "oxnode": "@oxc-node/cli"}.get(cli)
    if not package:
        return None
    directory = (root / "clients/js/node_modules" / package).resolve()
    try:
        meta = json.loads((directory / "package.json").read_text(encoding="utf-8"))
        bins = meta.get("bin", {})
        entry = bins.get(cli) if isinstance(bins, dict) else bins
        if not isinstance(entry, str):
            return None
        binary = (directory / entry).resolve()
        if not binary.is_relative_to(directory) or not binary.is_file():
            return None
        return ["node", str(binary), *command[1:]]
    except (OSError, ValueError):
        return None


def _run(command: list[str], root: Path, timeout: int, environment: dict[str, str], *, retry_diagnostics: bool = False) -> JSONValue:
    is_oxnode = Path(command[0]).name == "oxnode"
    if is_oxnode:
        # Older oxnode releases resolve their Node loader from the working directory.
        workdir = root / "clients/js"
        environment = {**environment, "FPDB_ROOT": str(root)}
        command = [str((root / command[0]).resolve()),
                   str((root / command[1]).resolve()), *command[2:]]
    else:
        workdir = root
    try:
        process = subprocess.run(command, cwd=workdir, env=environment, capture_output=True, text=True,
                                 timeout=timeout, check=False)
    except OSError as exc:
        if exc.errno not in (errno.ENOEXEC, errno.ENOENT):
            raise
        fallback = _node_cli_entry(command, root)
        if fallback is None:
            if exc.errno != errno.ENOEXEC or not is_oxnode:
                raise
            # Legacy oxnode distributions shipped a JS CLI without a shebang.
            fallback = ["node", *command]
        process = subprocess.run(fallback, cwd=workdir, env=environment,
                                 capture_output=True, text=True, timeout=timeout, check=False)
    if process.returncode != 0:
        raise DataError(f"Adapter exited {process.returncode}; stderr={process.stderr[-2500:]!r}")
    if retry_diagnostics and process.stderr.strip():
        log.warning("Adapter retry diagnostics: %s", process.stderr[-2500:].strip())
    try:
        return cast(JSONValue, json.loads(process.stdout))
    except json.JSONDecodeError as exc:
        raise DataError("Adapter stdout must be exactly one JSON document") from exc


def _echo_shape(payload: JSONValue) -> dict[str, str | list[str]]:
    """Print structural diagnostics only; never log response bodies or request headers."""
    def keys(value: JSONValue) -> list[str]:
        return sorted(json_object(value))[:24] if isinstance(value, dict) else []

    body = json_object(payload).get("echo", payload) if isinstance(payload, dict) else payload
    http = json_object(body).get("http") if isinstance(body, dict) else None
    tls = json_object(http).get("tls") if isinstance(http, dict) else None
    return {
        "body_type": type(body).__name__,
        "body_keys": keys(body),
        "http_type": type(http).__name__,
        "http_keys": keys(http),
        "tls_type": type(tls).__name__,
        "tls_keys": keys(tls),
    }


def _require_environment(payload: JSONObject, case: Case) -> Environment:
    if case.get("verify") is False and payload.get("verify") is not False:
        raise DataError("Adapter must confirm verify=false")
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
        (requested_runtime.count(".") < 2 and actual_runtime.startswith(requested_runtime + ".")) or
        (case["language"] == "deno" and actual_runtime.startswith(requested_runtime + "+"))
    ):
        raise DataError(f"Adapter runtime_version={actual_runtime!r} differs from {requested_runtime!r}")
    if payload.get("client_version") != case["client_version"]:
        raise DataError("Adapter client_version differs from requested version")
    environment["runtime_resolved_version"] = actual_runtime
    # Keep metadata small, stable, and JSON-safe (no secrets or transient data).
    allowed = {"os", "arch", "tls_backend", "image", "dependencies", "runtime_resolved_version"}
    if any(k not in allowed for k in environment):
        raise DataError("Unexpected environment keys in adapter response")
    if "image" in environment and not isinstance(environment["image"], str):
        raise DataError("environment.image must be string")
    if "dependencies" in environment:
        deps = environment["dependencies"]
        if not isinstance(deps, dict) or not all(
            isinstance(version, str) and name and version
            for name, version in deps.items()
        ):
            raise DataError("environment.dependencies must map package names to version strings")
    return cast(Environment, environment)


def collect(root: Path, case: Case) -> Record | None:
    config = load_config(root)
    target = next((target for target in config["targets"] if target["id"] == case["target"] and target.get("enabled")), None)
    if not target:
        raise DataError("Unknown enabled target")
    # Rebuild the case server-side so an injected matrix value cannot choose an arbitrary command.
    expected = make_case(target, case["runtime_version"], case["client_version"], verify=case.get("verify"))
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
    replacements = {key: str(val) for key, val in case.items()}
    replacements["endpoint"] = config["endpoint"]
    replacements["python_bin"] = os.environ.get("FPDB_PYTHON_BIN") or sys.executable
    adapter = target["adapter"]
    runtime_env = os.environ.copy()
    runtime_env.update({"FPDB_ENDPOINT": config["endpoint"], "FPDB_TARGET": case["target"]})
    if case["language"] == "go":
        # Do not silently recompile with a newer Go toolchain than the case claims.
        runtime_env["GOTOOLCHAIN"] = "local"
    # The collector uses its own control Python. Each Python probe (and pip
    # prepare command) must resolve libpython from the selected target build,
    # not from the control interpreter's LD_LIBRARY_PATH.
    if (case["language"] == "python" and sys.platform == "linux"
            and not case["target"].startswith("env-")):
        target_python = Path(replacements["python_bin"]).resolve()
        runtime_env["LD_LIBRARY_PATH"] = str(target_python.parent.parent / "lib")
    # Avoid accidental proxy-based TLS termination. Adapter may opt into a proxy
    # only by explicit own code; proxy variables are not inherited here.
    for name in list(runtime_env):
        if name.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}:
            runtime_env.pop(name, None)
    for key, value in json_object(adapter.get("env", {})).items():
        if not isinstance(value, str):
            raise DataError("adapter.env values must be strings")
        runtime_env[key] = value.format_map(replacements)
    runtime_env["FPDB_TLS_VERIFY"] = "false" if case.get("verify") is False else "default"

    prepare = adapter.get("prepare", [])
    if prepare:
        prepare_command = _command(prepare, replacements)
        # Each Python case runs in a disposable venv. Preserve the exact Scrapy
        # release while choosing compatible versions of its TLS dependencies.
        if (case['target'] == 'python-scrapy' and len(prepare_command) >= 3
                and prepare_command[1:3] == ['-m', 'pip']):
            version = tuple(int(part) for part in case['client_version'].split('.'))
            if version <= (2, 11, 1):
                # Scrapy 2.10.1/2.11.0 require Twisted <23.8.0.
                prepare_command.append('Twisted<23.8.0')
            elif version <= (2, 13, 3):
                prepare_command.append('Twisted==24.11.0')
            if version <= (2, 12, 0):
                prepare_command.append('w3lib==2.2.1')
            if version <= (2, 6, 2):
                # Scrapy removed its SSLv3 references in 2.6.3.
                prepare_command.extend(['pyOpenSSL==22.0.0', 'cryptography<38'])
        # An old CLI range may otherwise resolve a newer core with incompatible
        # loader hooks. Pin the real Oxc core to its matching CLI release.
        if (case['target'] in ('node-oxc-ts', 'node-oxc-js')
                and prepare_command[:2] == ['npm', 'install']):
            prepare_command.append('@oxc-node/core@' + case['client_version'])
        prepare_input = None
        # Windows Git checkouts may expose CRLF .sh scripts to a Linux Docker
        # bind mount. Execute a LF-normalized copy over stdin; do not edit the
        # user's checkout, and preserve positional arguments after the script.
        if (prepare_command[0] == "bash" and len(prepare_command) >= 2
                and prepare_command[1].endswith(".sh")):
            script = root / prepare_command[1]
            if script.is_file():
                source = script.read_bytes()
                if b"\r\n" in source:
                    prepare_input = source.replace(b"\r\n", b"\n").decode("utf-8")
                    prepare_command = ["bash", "-s", "--", *prepare_command[2:]]
                    log.info("Running CRLF-normalized preparation script: %s", script)
        for trial in range(2):
            try:
                prepared = subprocess.run(prepare_command, input=prepare_input,
                                          cwd=root, env=runtime_env, capture_output=True, text=True,
                                          timeout=int(sampling.get("prepare_timeout_seconds", 900)), check=False)
                if prepared.returncode:
                    raise DataError(f"prepare exited {prepared.returncode}: {prepared.stderr[-1000:]}")
                break
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                log.warning("case=%s prepare attempt=%d failed: %s", case["id"], trial + 1, exc)
        else:
            return None

    command = _command(adapter["probe"], replacements)
    if case["language"] == "deno" and case.get("verify") is False:
        command.insert(2, "--unsafely-ignore-certificate-errors")
        command.append("--fpdb-no-verify")
    observed: list[Fingerprint] = []
    joint_samples: list[Evidence] = []
    resolved_environment: Environment | None = None
    for sample_index in range(expanded):
        result = None
        for trial in range(retry + 1):
            try:
                # Keep the first attempt quiet. On the retry, request safe HTTP
                # metadata from adapters without changing the TLS connection.
                probe_env = ({**runtime_env, "FPDB_DIAGNOSTIC_RETRY": "1"}
                             if trial else runtime_env)
                local_ca = probe_env.get("FPDB_LOCAL_CA")
                if local_ca:
                    probe_env.update({"SSL_CERT_FILE": local_ca, "CURL_CA_BUNDLE": local_ca,
                                      "NODE_EXTRA_CA_CERTS": local_ca, "DENO_CERT": local_ca})
                    store = str(Path(local_ca).with_name("java-truststore.jks"))
                    if Path(store).exists():
                        probe_env["JAVA_TOOL_OPTIONS"] = (
                            probe_env.get("JAVA_TOOL_OPTIONS", "") +
                            " -Djavax.net.ssl.trustStore=" + store +
                            " -Djavax.net.ssl.trustStorePassword=changeit").strip()
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
                local_profile = runtime_env.get("FPDB_CAPTURE_PROFILE")
                target_profile = target.get("capture_profile")
                if local_profile:
                    # Docker captures must never merge into the GH-hosted runner's identity.
                    observed_env["capture_profile"] = local_profile + (
                        ":" + target_profile if target_profile else "")
                elif target_profile:
                    observed_env["capture_profile"] = target_profile
                elif runtime_env.get("FPDB_LOCAL_CA"):
                    # Historical records remain untouched; only new captures get this provenance.
                    observed_env["capture_profile"] = "local-echo-fpdb-v1"
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
    if resolved_environment is None:
        raise DataError("No observed environment")
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
