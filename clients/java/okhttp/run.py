from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 4 or not re.fullmatch(r"[a-f0-9]{24}", sys.argv[1]):
        raise SystemExit("usage: run.py CASE_ID VERSION URL")
    root = Path(__file__).resolve().parents[3]
    directory = root / ".fpdb-local/build" / sys.argv[1]
    classpath = str(directory) + os.pathsep + (directory / "classpath.txt").read_text(encoding="utf-8").strip()
    return subprocess.run(["java", "-cp", classpath, "ProbeOkHttp", *sys.argv[2:]], check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
