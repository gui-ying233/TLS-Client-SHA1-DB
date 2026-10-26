"""Entry point used by both workflows: python -m fpdb.cli <command>."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from fpdb.core import ROOT, DataError, save_json
from fpdb.planner import batchify, plan
from fpdb.collector import collect
from fpdb.persistence import persist
from fpdb.lookup import lookup


def main() -> int:
    parser = argparse.ArgumentParser(description="TLS-Client-SHA1-DB")
    parser.add_argument("--root", type=Path, default=ROOT)
    subs = parser.add_subparsers(dest="command", required=True)
    p = subs.add_parser("plan")
    p.add_argument("--mode", required=True, choices=["incremental", "historical", "full"])
    p.add_argument("--target", default="")
    p.add_argument("--seed", default="0")
    p.add_argument("--batch-size", type=int)
    p.add_argument("--group-size", type=int, default=1,
                   help="Max cases per runtime/target job (1 keeps legacy planning)")
    p.add_argument("--recheck", action="store_true")
    p.add_argument("--output", type=Path, default=Path("matrix.json"))
    c = subs.add_parser("collect")
    c.add_argument("--case-env", default="FPDB_CASE_JSON")
    c.add_argument("--output-dir", type=Path, default=Path("outbox"))
    b = subs.add_parser("collect-batch")
    b.add_argument("--cases-env", default="FPDB_CASES_JSON")
    b.add_argument("--output-dir", type=Path, default=Path("outbox"))
    s = subs.add_parser("persist")
    s.add_argument("--inbox", type=Path, default=Path("inbox"))
    q = subs.add_parser("lookup", help="Rank candidates using co-observed SHA-1 values")
    q.add_argument("--ciphers", help="tlsClientCiphersSha1")
    q.add_argument("--extensions", help="tlsClientExtensionsSha1")
    q.add_argument("--extensions-le", help="tlsClientExtensionsSha1Le")
    q.add_argument("--limit", type=int, default=100)
    q.add_argument("--require-all", action="store_true", help="Only return complete joint matches")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    root = args.root.resolve()
    try:
        if args.command == "plan":
            cases = plan(root, mode=args.mode, target_filter=args.target, seed=args.seed,
                         recheck=args.recheck, batch_size=args.batch_size,
                         group_size=args.group_size)
            matrix = {"include": batchify(cases, args.group_size)}
            output = args.output.resolve()
            save_json(output, matrix)
            if os.getenv("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
                    stream.write("matrix=" + json.dumps(matrix, separators=(",", ":")) + "\n")
                    stream.write("has_work=" + ("true" if cases else "false") + "\n")
            print(f"planned={len(cases)} jobs={len(matrix['include'])} mode={args.mode}")
        elif args.command == "collect":
            case = json.loads(os.environ[args.case_env])
            record = collect(root, case)
            if record is None:
                # Keep an ephemeral artifact so the merge job also works if all cases fail.
                args.output_dir.mkdir(parents=True, exist_ok=True)
                (args.output_dir / (case["id"] + ".skip")).touch()
                print(f"skipped={case['id']}")
            else:
                path = args.output_dir.resolve() / (case["id"] + ".json")
                save_json(path, record)
                print(f"collected={case['id']} path={path}")
        elif args.command == "collect-batch":
            cases = json.loads(os.environ[args.cases_env])
            if not isinstance(cases, list) or not (1 <= len(cases) <= 16):
                raise DataError("Expected 1 to 16 cases per batch")
            first = cases[0]
            if not isinstance(first, dict):
                raise DataError("Invalid batch case")
            grouping = ("target", "language", "runner", "runtime_version", "platform")
            if any(not isinstance(case, dict) or
                   any(case.get(k) != first.get(k) for k in grouping)
                   for case in cases):
                raise DataError("Batch mixes runtime, target, or runner")
            if len({case["id"] for case in cases}) != len(cases):
                raise DataError("Duplicate case in batch")
            successes = 0
            for case in cases:
                overrides = {}
                try:
                    with tempfile.TemporaryDirectory(prefix="fpdb-case-") as directory:
                        workspace = Path(directory)
                        if case["language"] == "python" and not case["target"].startswith("env-"):
                            interpreter = os.environ.get("FPDB_PYTHON_BIN")
                            if not interpreter:
                                raise OSError("Selected Python interpreter is unavailable")
                            if len(cases) > 1:
                                venv = workspace / "venv"
                                subprocess.run([interpreter, "-m", "venv", str(venv)],
                                               cwd=root, check=True, timeout=180)
                                overrides["python_bin"] = str(venv / "bin" / "python")
                        if case["language"] == "node" and case["target"] in ("node-axios", "node-undici"):
                            # Each version gets a fresh package graph and probe.
                            probe = workspace / "probe.cjs"
                            shutil.copyfile(root / "clients/node/probe.cjs", probe)
                            overrides["node_dir"] = str(workspace)
                            overrides["node_probe"] = str(probe)
                        record = collect(root, case, overrides=overrides)
                except (OSError, subprocess.CalledProcessError,
                        subprocess.TimeoutExpired) as exc:
                    logging.warning("case=%s environment setup failed: %s", case["id"], exc)
                    record = None
                args.output_dir.mkdir(parents=True, exist_ok=True)
                if record is None:
                    (args.output_dir / (case["id"] + ".skip")).touch()
                    print(f"skipped={case['id']}")
                else:
                    output = args.output_dir / (case["id"] + ".json")
                    save_json(output, record)
                    successes += 1
                    print(f"collected={case['id']} path={output}")
            print(f"batch_success={successes} batch_skipped={len(cases) - successes}")
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
