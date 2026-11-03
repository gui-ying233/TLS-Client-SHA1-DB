"""Launch a probe with only its selected SDK and case-local rocks tree."""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys


def main() -> None:
    directory = Path(sys.argv[1])
    endpoint = sys.argv[2]
    metadata = json.loads((directory / "lua.json").read_text(encoding="utf-8"))
    abi, tree = metadata["abi"], Path(metadata["tree"])
    environment = os.environ.copy()
    fallback = ";;" if metadata["engine"] == "openresty" else ""
    environment["LUA_PATH"] = f"{tree}/share/lua/{abi}/?.lua;{tree}/share/lua/{abi}/?/init.lua" + fallback
    environment["LUA_CPATH"] = f"{directory}/?.so;{tree}/lib/lua/{abi}/?.so" + fallback
    environment["FPDB_LUA_METADATA"] = str(directory / "lua.json")
    environment["FPDB_LUA_OS"] = platform.system().lower()
    environment["FPDB_LUA_ARCH"] = platform.machine().lower()
    version = subprocess.run([metadata["lua_bin"], "-v"], capture_output=True, text=True, check=True)
    match = re.search(r"(?:LuaJIT|Lua) ([0-9]+(?:\.[0-9]+){1,2})", version.stdout + version.stderr)
    if not match or (metadata["engine"] != "openresty" and match.group(1) != metadata["runtime_version"]):
        raise ValueError("Lua runtime changed after preparation")
    probe = str(Path(__file__).with_name("probe.lua"))
    command = [metadata["binary"]]
    if metadata["engine"] == "openresty":
        process = subprocess.run([metadata["binary"], "-V"], capture_output=True, text=True, check=True)
        if "openresty/" + metadata["runtime_version"] not in process.stdout + process.stderr:
            raise ValueError("OpenResty runtime changed after preparation")
        ca = environment.get("FPDB_LOCAL_CA")
        if ca:
            command += ["--http-conf", 'lua_ssl_trusted_certificate "' + ca.replace('"', '\\"') + '";']
    command += [probe, endpoint]
    process = subprocess.run(command, env=environment, cwd=directory, check=False)
    raise SystemExit(process.returncode)


if __name__ == "__main__":
    main()
