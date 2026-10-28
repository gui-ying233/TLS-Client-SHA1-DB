"""Entry point used by both workflows: python -m fpdb.cli <command>."""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import cast

from fpdb.core import ROOT, DataError, json_array, json_object, save_json
from fpdb.planner import job_batches, plan
from fpdb.collector import collect
from fpdb.persistence import persist
from fpdb.lookup import lookup
from fpdb.schema import Case, JSONValue


class Arguments(argparse.Namespace):
    root: Path
    command: str
    mode: str
    target: str
    batch_size: int | None
    recheck: bool
    output: Path
    case_env: str
    cases_env: str | None
    output_dir: Path
    inbox: Path
    ciphers: str | None
    extensions: str | None
    extensions_le: str | None
    limit: int
    require_all: bool


def main() -> int:
    parser = argparse.ArgumentParser(description="TLS-Client-SHA1-DB")
    parser.add_argument("--root", type=Path, default=ROOT)
    subs = parser.add_subparsers(dest="command", required=True)
    p = subs.add_parser("plan")
    p.add_argument("--mode", required=True, choices=["incremental", "historical", "full"])
    p.add_argument("--target", default="")
    p.add_argument("--batch-size", type=int, help="Maximum collection Jobs (up to 8 cases each)")
    p.add_argument("--recheck", action="store_true")
    p.add_argument("--output", type=Path, default=Path("matrix.json"))
    c = subs.add_parser("collect")
    c.add_argument("--case-env", default="FPDB_CASE_JSON")
    c.add_argument("--cases-env", help="Environment variable containing a JSON case array")
    c.add_argument("--output-dir", type=Path, default=Path("outbox"))
    s = subs.add_parser("persist")
    s.add_argument("--inbox", type=Path, default=Path("inbox"))
    q = subs.add_parser("lookup", help="Rank candidates using co-observed SHA-1 values")
    q.add_argument("--ciphers", help="tlsClientCiphersSha1")
    q.add_argument("--extensions", help="tlsClientExtensionsSha1")
    q.add_argument("--extensions-le", help="tlsClientExtensionsSha1Le")
    q.add_argument("--limit", type=int, default=100)
    q.add_argument("--require-all", action="store_true", help="Only return complete joint matches")
    args = parser.parse_args(namespace=Arguments())
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    root = args.root.resolve()
    try:
        if args.command == "plan":
            cases = plan(root, mode=args.mode, target_filter=args.target,
                         recheck=args.recheck, batch_size=args.batch_size)
            jobs = job_batches(cases)
            matrix = {"include": jobs}
            output = args.output.resolve()
            save_json(output, matrix)
            if os.getenv("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
                    stream.write("matrix=" + json.dumps(matrix, separators=(",", ":")) + "\n")
                    stream.write("has_work=" + ("true" if cases else "false") + "\n")
            print(f"planned_cases={len(cases)} jobs={len(jobs)} mode={args.mode}")
        elif args.command == "collect":
            payload = cast(JSONValue, json.loads(
                os.environ[args.cases_env] if args.cases_env else os.environ[args.case_env]))
            if args.cases_env:
                if not isinstance(payload, list) or not payload:
                    raise DataError("Batch must contain at least one case")
                cases = [cast(Case, json_object(item)) for item in json_array(payload)]
            else:
                cases = [cast(Case, json_object(payload))]
            collected = skipped = 0
            for case in cases:
                try:
                    if (args.cases_env and case["language"] == "python"
                            and not case["target"].startswith("env-")
                            and case["target"] != "python-urllib"):
                        # Isolate pip dependencies between versions sharing a runner.
                        python = os.environ["FPDB_PYTHON_BIN"]
                        with tempfile.TemporaryDirectory(prefix="fpdb-venv-") as venv:
                            venv_env = os.environ.copy()
                            venv_env["LD_LIBRARY_PATH"] = str(Path(python).resolve().parent.parent / "lib")
                            prepared = subprocess.run([python, "-m", "venv", venv], env=venv_env,
                                                      capture_output=True, text=True)
                            if prepared.returncode:
                                raise DataError(f"venv failed: {prepared.stderr[-1000:]}")
                            os.environ["FPDB_PYTHON_BIN"] = str(Path(venv) / "bin" / "python")
                            try:
                                record = collect(root, case)
                            finally:
                                os.environ["FPDB_PYTHON_BIN"] = python
                    else:
                        record = collect(root, case)
                except (OSError, ValueError, KeyError, subprocess.CalledProcessError,
                        subprocess.TimeoutExpired) as exc:
                    if not args.cases_env:
                        raise
                    logging.error("case=%s failed: %s", case.get("id"), exc)
                    record = None
                if record is None:
                    skipped += 1
                    # A failed case must not prevent the other cases from being persisted.
                    args.output_dir.mkdir(parents=True, exist_ok=True)
                    (args.output_dir / (case["id"] + ".skip")).touch()
                    print(f"::warning::Case {case['id']} skipped")
                    print(f"skipped={case['id']}")
                else:
                    collected += 1
                    path = args.output_dir.resolve() / (case["id"] + ".json")
                    save_json(path, record)
                    print(f"collected={case['id']} path={path}")
            print(f"batch_summary collected={collected} skipped={skipped}")
            if args.cases_env and not collected:
                logging.error("All %d cases in this batch were skipped", skipped)
                return 1
        elif args.command == "persist":
            changed, processed = persist(root, args.inbox.resolve())
            print(f"artifacts={processed} changed_records={changed}")
        elif args.command == "lookup":
            supplied = {field: value for field, value in (
                ("tlsClientCiphersSha1", args.ciphers),
                ("tlsClientExtensionsSha1", args.extensions),
                ("tlsClientExtensionsSha1Le", args.extensions_le)) if value}
            print(json.dumps({"supplied": supplied, "candidates": lookup(root, supplied, args.limit, require_all=args.require_all)},
                             ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
