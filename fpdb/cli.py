"""Entry point used by both workflows: python -m fpdb.cli <command>."""
from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from fpdb.core import ROOT, DataError, save_json
from fpdb.planner import plan
from fpdb.collector import collect
from fpdb.persistence import persist


def main() -> int:
    parser = argparse.ArgumentParser(description="TLS-Client-SHA1-DB")
    parser.add_argument("--root", type=Path, default=ROOT)
    subs = parser.add_subparsers(dest="command", required=True)
    p = subs.add_parser("plan")
    p.add_argument("--mode", required=True, choices=["incremental", "historical", "full"])
    p.add_argument("--target", default="")
    p.add_argument("--seed", default="0")
    p.add_argument("--batch-size", type=int)
    p.add_argument("--recheck", action="store_true")
    p.add_argument("--output", type=Path, default=Path("matrix.json"))
    c = subs.add_parser("collect")
    c.add_argument("--case-env", default="FPDB_CASE_JSON")
    c.add_argument("--output-dir", type=Path, default=Path("outbox"))
    s = subs.add_parser("persist")
    s.add_argument("--inbox", type=Path, default=Path("inbox"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    root = args.root.resolve()
    try:
        if args.command == "plan":
            cases = plan(root, mode=args.mode, target_filter=args.target, seed=args.seed,
                         recheck=args.recheck, batch_size=args.batch_size)
            matrix = {"include": [{"case": case} for case in cases]}
            output = args.output.resolve()
            save_json(output, matrix)
            if os.getenv("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
                    stream.write("matrix=" + json.dumps(matrix, separators=(",", ":")) + "\n")
                    stream.write("has_work=" + ("true" if cases else "false") + "\n")
            print(f"planned={len(cases)} mode={args.mode}")
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
        elif args.command == "persist":
            changed, processed = persist(root, args.inbox.resolve())
            print(f"artifacts={processed} changed_records={changed}")
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
