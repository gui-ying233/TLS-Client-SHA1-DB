"""Prepare exact Lua-family SDKs and a case-local LuaRocks tree."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from fpdb.core import json_object
from fpdb.schema import JSONObject
from fpdb.version_io import fetch_bytes


def get_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "TLS-Client-SHA1-DB/1"})
    return fetch_bytes(request, timeout=30).decode("utf-8")

TARGETS = {"lua-luasec": ("lua", "luasec"), "lua-http": ("lua", "http"),
           "luajit-luasec": ("luajit", "luasec"), "luajit-http": ("luajit", "http"),
           "openresty-lua-resty-http": ("openresty", "lua-resty-http")}
LUAROCKS_VERSION = "3.13.0"
# 2.8-1 explicitly excludes Lua 5.5; 2.9-1 supports every Lua ABI collected here.
JSON_ROCK_VERSION = "2.9-1"
# v0.15's official source still declares _VERSION='0.14'. Accept only those
# exact upstream bytes, not a different module that happens to report 0.14.
RESTY_HTTP_015_SHA256 = "145b1bd8937a5bc12e9c4231a9e78a158ec2478941e3e3dfbec9f5f4ee160d24"


def run(argv: list[str], cwd: Path, env: dict[str, str] | None = None,
        *, lock_fd: int | None = None) -> str:
    process = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False,
                             pass_fds=() if lock_fd is None else (lock_fd,))
    if process.returncode:
        raise ValueError("Lua preparation failed: " + " ".join(argv) + "\n" +
                         (process.stdout + process.stderr)[-6000:])
    return process.stdout.strip()


def cache_root() -> Path:
    fallback = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "fpdb-local/lua"
    return Path(os.environ.get("FPDB_LUA_CACHE_ROOT", str(fallback))).expanduser().resolve()


def download(url: str, cache: Path, expected: str | None = None) -> Path:
    path = cache / "downloads" / url.rsplit("/", 1)[1]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not path.exists():
            temporary = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
            request = urllib.request.Request(url, headers={"User-Agent": "TLS-Client-SHA1-DB/1"})
            temporary.write_bytes(fetch_bytes(request, timeout=90))
            temporary.replace(path)
        if expected and hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            path.unlink()
            raise ValueError("Lua source SHA256 mismatch: " + url)
    return path


def unpack(archive: Path, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as source:
        members = source.getmembers()
        # These official SDK archives contain only regular files/directories.
        # Check their names before native tar extraction: data_filter's per-file
        # realpath calls are prohibitively slow on Windows Docker bind mounts.
        if any(not (member.isfile() or member.isdir()) or Path(member.name).is_absolute()
               or ".." in Path(member.name).parts for member in members):
            raise ValueError("Unexpected SDK archive member")
        roots = {Path(member.name).parts[0] for member in members if member.name.strip("./")}
    if len(roots) != 1:
        raise ValueError("Expected one source directory")
    run(["tar", "-xzf", str(archive), "-C", str(directory),
         "--no-same-owner", "--no-same-permissions"], ROOT)
    return directory / roots.pop()


def lua_version(binary: Path) -> str:
    process = subprocess.run([str(binary), "-v"], capture_output=True, text=True, check=True)
    match = re.search(r"(?:LuaJIT|Lua) ([0-9]+(?:\.[0-9]+){1,2})", process.stdout + process.stderr)
    if not match:
        raise ValueError("Lua executable did not report a version")
    return match.group(1)


def prepare_sdk(engine: str, version: str, cache: Path) -> JSONObject:
    # Source-built SDKs must not survive a change of the host native ABI unnoticed.
    abi = platform.machine() + run(["cc", "-dumpmachine"], ROOT) + run(["openssl", "version"], ROOT)
    stamp = hashlib.sha256(("sdk-v1:" + abi).encode()).hexdigest()[:12]
    sdk = cache / "runtimes" / engine / version / stamp
    sdk.mkdir(parents=True, exist_ok=True)
    with (sdk / "prepare.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        marker = sdk / "sdk.json"
        if marker.exists():
            metadata = json_object(json.loads(marker.read_text(encoding="utf-8")))
            check_sdk(metadata, version)
            return metadata
        prefix = sdk / "install"
        commit: str | None = None
        if engine == "lua":
            page = get_text("https://www.lua.org/ftp/")
            row = re.search(r'lua-' + re.escape(version) + r'\.tar\.gz.*?</TR>', page, re.I | re.S)
            sums = re.findall(r"\b[a-f0-9]{64}\b", row.group(0) if row else "")
            if not sums:
                raise ValueError("No official Lua source checksum for " + version)
            source = unpack(download(f"https://www.lua.org/ftp/lua-{version}.tar.gz", cache, sums[0]), sdk / "source")
            run(["make", "-j2", "linux", "MYCFLAGS=-fPIC"], source)
            run(["make", "install", "INSTALL_TOP=" + str(prefix)], source)
            home, binary = prefix, prefix / "bin/lua"
        elif engine == "luajit":
            repository = cache / "sources/luajit.git"
            repository.parent.mkdir(parents=True, exist_ok=True)
            with (repository.parent / "luajit.lock").open("a+") as git_lock:
                fcntl.flock(git_lock, fcntl.LOCK_EX)
                if not repository.exists():
                    run(["git", "clone", "--mirror", "https://github.com/LuaJIT/LuaJIT.git", str(repository)], sdk)
                if version.startswith("2.1."):
                    timestamp = version.split(".")[2]
                    history = run(["git", "--git-dir=" + str(repository), "log", "refs/heads/v2.1", "--format=%H %ct"], sdk)
                    matches = [line.split()[0] for line in history.splitlines() if line.endswith(" " + timestamp)]
                    if not matches:
                        run(["git", "--git-dir=" + str(repository), "fetch", "origin"], sdk)
                        history = run(["git", "--git-dir=" + str(repository), "log", "refs/heads/v2.1", "--format=%H %ct"], sdk)
                        matches = [line.split()[0] for line in history.splitlines() if line.endswith(" " + timestamp)]
                    if not matches:
                        raise ValueError("LuaJIT release timestamp not found in upstream history")
                    commit = matches[0]
                else:
                    history = run(["git", "--git-dir=" + str(repository), "log", "--all", "--format=%H %s",
                                   "--fixed-strings", "--grep=RELEASE LuaJIT-" + version], sdk)
                    releases = [line.split()[0] for line in history.splitlines()
                                if re.match(r"^[a-f0-9]{40} RELEASE LuaJIT-" + re.escape(version) + r"(?:\s|$)", line)]
                    if not releases:
                        raise ValueError("LuaJIT stable release commit not found")
                    commit = releases[0]
                source = sdk / "source"
                if not source.exists():
                    run(["git", "clone", "--shared", "--no-checkout", str(repository), str(source)], sdk)
                run(["git", "checkout", "--detach", commit], source)
            run(["make", "-j2"], source)
            run(["make", "install", "PREFIX=" + str(prefix)], source)
            home, binary = prefix, prefix / "bin/luajit"
            if not binary.exists():
                binary = next((prefix / "bin").glob("luajit-*"))
        else:
            page = get_text("https://openresty.org/en/download.html")
            filename = re.search(r'((?:ngx_)?openresty-' + re.escape(version) + r'\.tar\.gz)', page)
            if filename is None:
                raise ValueError("No official OpenResty archive for " + version)
            source = sdk / "source" / filename.group(1).removesuffix(".tar.gz")
            # A timed-out caller may have spent its budget waiting for this SDK.
            # Preserve configured sources and object mtimes so the next owner
            # resumes make instead of unpacking/reconfiguring the whole SDK.
            # OpenResty writes Makefile before staging LuaJIT headers; it does
            # not prove configure completed successfully after an interruption.
            configured = source / ".fpdb-configured"
            if not configured.is_file():
                source = unpack(download("https://openresty.org/download/" + filename.group(1), cache), sdk / "source")
                run(["./configure", "--prefix=" + str(prefix), "--with-http_ssl_module"], source)
                configured.touch()
            run(["make", "-j2"], source)
            run(["make", "install"], source)
            home, binary = prefix / "luajit", prefix / "bin/resty"
        include = next((home / "include").rglob("lua.h")).parent
        lua_bin = home / "bin" / ("lua" if engine == "lua" else "luajit")
        if not lua_bin.exists():
            lua_bin = binary if engine == "luajit" else next((home / "bin").glob("luajit-*"))
        actual = lua_version(lua_bin)
        abi_version = "5.1" if engine != "lua" else ".".join(actual.split(".")[:2])
        rocks_source = unpack(download(f"https://luarocks.github.io/luarocks/releases/luarocks-{LUAROCKS_VERSION}.tar.gz", cache), sdk / "rocks-source")
        rocks_home = sdk / "luarocks"
        options = ["./configure", "--prefix=" + str(rocks_home), "--with-lua=" + str(home),
                   "--with-lua-include=" + str(include), "--lua-version=" + abi_version]
        if engine != "lua":
            options.append("--lua-suffix=jit")
        run(options, rocks_source)
        run(["make"], rocks_source)
        run(["make", "install"], rocks_source)
        metadata: JSONObject = {"engine": engine, "binary": str(binary), "lua_bin": str(lua_bin),
                                "home": str(home), "include": str(include), "abi": abi_version,
                                "luarocks": str(rocks_home / "bin/luarocks")}
        if commit:
            metadata["source_commit"] = commit
        check_sdk(metadata, version)
        marker.write_text(json.dumps(metadata), encoding="utf-8")
        return metadata


def check_sdk(metadata: JSONObject, expected: str) -> None:
    if metadata["engine"] == "openresty":
        process = subprocess.run([str(metadata["binary"]), "-V"], capture_output=True, text=True, check=True)
        match = re.search(r"openresty/([0-9.]+)", process.stdout + process.stderr)
        actual = match.group(1) if match else ""
    else:
        actual = lua_version(Path(str(metadata["lua_bin"])))
    if actual != expected:
        raise ValueError(f"Installed Lua-family runtime {actual} != {expected}")


def prepare(target: str, version: str, runtime: str, directory: Path) -> None:
    engine, package = TARGETS[target]
    cache = cache_root()
    sdk = prepare_sdk(engine, runtime, cache)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "prepare.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        tree = directory / "rocks"
        # Writers inherit this lock, so a remaining LFS marker after acquiring
        # it belongs to an interrupted install. Retain the partial tree intact.
        if (tree / "lockfile.lfs").exists():
            archive_rocks(directory)
        for attempt in range(2):
            try:
                install_case(sdk, package, version, runtime, directory, cache, lock.fileno())
                break
            except ValueError as exc:
                if (attempt == 0 and tree.exists() and
                        ("failed deploying files" in str(exc) or "LuaRocks did not install the exact" in str(exc))):
                    archive_rocks(directory)
                else:
                    raise


def archive_rocks(directory: Path) -> None:
    tree = directory / "rocks"
    archive = directory / ("rocks-interrupted-" + str(time.time_ns()))
    if tree.is_symlink() or tree.resolve().parent != directory.resolve() or archive.resolve().parent != directory.resolve():
        raise ValueError("Cannot archive a LuaRocks tree outside its case directory")
    tree.rename(archive)


def install_case(sdk: JSONObject, package: str, version: str, runtime: str,
                 directory: Path, cache: Path, lock_fd: int) -> None:
    tree = directory / "rocks"
    config = directory / "luarocks-config.lua"
    # Concurrent installs collided on LuaRocks' shared manifest locks.
    # A Case ID includes its Lua ABI; isolate downloads and native build temps.
    config.write_text("local_cache = " + json.dumps(str(cache / "downloads/rocks" / directory.name)) + "\n", encoding="utf-8")
    temporary = directory / "tmp"
    temporary.mkdir(exist_ok=True)
    env = {**os.environ, "LUAROCKS_CONFIG": str(config), "GIT_CONFIG_COUNT": "1",
           "GIT_CONFIG_KEY_0": "url.https://github.com/.insteadOf", "GIT_CONFIG_VALUE_0": "git://github.com/",
           "TMPDIR": str(temporary)}
    command = [str(sdk["luarocks"]), "--lua-version=" + str(sdk["abi"]), "--tree=" + str(tree)]
    if sdk["engine"] == "luajit" and runtime == "2.0.1":
        # LuaRocks' compat53 wrapper passes an explicit nil fourth argument.
        # LuaJIT fixed that table.concat bug in b0307884 (after 2.0.1).
        # Adapt the installer process only; the SDK and TLS probe are untouched.
        shim = ("local concat=table.concat; table.concat=function(t,s,i,j) "
                "if i==nil then i=1 end; if j==nil then j=#t end; return concat(t,s,i,j) end")
        command = [str(sdk["lua_bin"]), "-e", shim, *command]
    # Child processes inherit our advisory lock: if this preparer is killed,
    # a still-running LuaRocks writer continues to exclude the next preparer.
    run([*command, "install", package, version], directory, env, lock_fd=lock_fd)
    run([*command, "install", "dkjson", JSON_ROCK_VERSION], directory, env, lock_fd=lock_fd)
    installed = run([*command, "list", "--porcelain"], directory, env, lock_fd=lock_fd)
    dependencies: dict[str, str] = {}
    for line in installed.splitlines():
        fields = line.split("\t")
        if len(fields) >= 3 and fields[2] == "installed":
            dependencies[fields[0]] = fields[1]
    if dependencies.get(package) != version:
        raise ValueError(f"LuaRocks did not install the exact {package} {version}")
    run(["cc", "-O2", "-fPIC", "-shared", "-I" + str(sdk["include"]),
         str(ROOT / "clients/lua/tlsmeta.c"), "-o", str(directory / "fpdb_tlsmeta.so"), "-ldl"], directory,
        lock_fd=lock_fd)
    metadata = {**sdk, "runtime_version": runtime, "client_version": version,
                "package": package, "dependencies": dependencies, "tree": str(tree)}
    if package == "lua-resty-http" and version == "0.15-0":
        module = tree / "share/lua" / str(sdk["abi"]) / "resty/http.lua"
        if hashlib.sha256(module.read_bytes()).hexdigest() != RESTY_HTTP_015_SHA256:
            raise ValueError("lua-resty-http 0.15-0 differs from its official v0.15 source")
        metadata.update({"module_path": str(module), "module_sha256": RESTY_HTTP_015_SHA256,
                         "module_expected_version": "0.14"})
        dependencies["lua-resty-http.module_version"] = "0.14"
    (directory / "lua.json").write_text(json.dumps(metadata), encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit("usage: prepare-lua.py TARGET CLIENT_VERSION RUNTIME_VERSION BUILD_DIR")
    target, version, runtime, output = sys.argv[1:]
    directory = Path(output)
    if (target not in TARGETS or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}-[0-9]+", version)
            or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", runtime)
            or not directory.is_absolute() or not re.fullmatch(r"[a-f0-9]{24}", directory.name)):
        raise ValueError("Invalid Lua target, versions or case build directory")
    prepare(target, version, runtime, directory)


if __name__ == "__main__":
    main()
