"""Truthful runtime compatibility checks for local historical collection.

Do not substitute an interpreter/runtime behind a Case identity: if a pair is
unavailable, select another explicitly identified pair at planning time.
"""
from __future__ import annotations

from functools import cache
import os
from pathlib import Path
import re
import subprocess
from threading import Lock

_REQ_LOCK = Lock()
_PYPI_REQUIREMENTS: dict[str, dict[str, tuple[str | None, ...]]] = {}


def remember_pypi_releases(package: str, releases: dict) -> None:
    """Keep Requires-Python per non-yanked distribution file/version.

    Multiple files for one version can have different requirements. A pair is
    excluded only when every usable file explicitly rules it out.
    """
    result: dict[str, tuple[str | None, ...]] = {}
    for version, files in releases.items():
        if not isinstance(files, list):
            continue
        requirements = []
        for file in files:
            if not isinstance(file, dict) or file.get("yanked", False):
                continue
            value = file.get("requires_python")
            requirements.append(value if isinstance(value, str) and value.strip() else None)
        if requirements:
            result[version] = tuple(requirements)
    with _REQ_LOCK:
        _PYPI_REQUIREMENTS[package] = result


@cache
def _satisfies(runtime: str, requirement: str | None) -> bool:
    if not requirement:
        return True  # Unknown metadata must not silently erase historical cases.
    try:
        from packaging.specifiers import SpecifierSet
    except ImportError:
        try:
            from pip._vendor.packaging.specifiers import SpecifierSet
        except ImportError:
            raise RuntimeError("Python packaging specifiers are required for PyPI compatibility checking")
    try:
        return SpecifierSet(requirement).contains(runtime, prereleases=True)
    except ValueError:
        return True  # Unknown/invalid legacy metadata: allow actual installation to decide.


def pypi_allows(package: str, client_version: str, runtime: str) -> bool:
    with _REQ_LOCK:
        requirements = _PYPI_REQUIREMENTS.get(package, {}).get(client_version)
    return requirements is None or any(_satisfies(runtime, req) for req in requirements)


@cache
def uv_available_python() -> frozenset[str]:
    """Exact installable CPython versions for this container, not setup-python's list.

    Only enabled in the Docker runner (where UV_PYTHON_INSTALL_DIR is set).
    A failed inventory is a planning error, never a silent fallback to
    un-installable versions or substituted patch versions.
    """
    cmd = ["uv", "python", "list", "--no-config", "--only-downloads", "--all-versions"]
    process = subprocess.run(cmd, cwd="/tmp", capture_output=True, text=True, timeout=90)
    if process.returncode:
        raise ValueError(f"uv Python inventory failed: {process.stderr[-1500:]}")
    available: set[str] = set()
    # The text output uses: cpython-3.12.12-linux-x86_64-gnu  <download ...>
    for line in process.stdout.splitlines():
        match = re.match(r"^cpython-(\d+\.\d+\.\d+)-linux-x86_64-gnu(?:\s|$)", line)
        if match:
            available.add(match.group(1))
    root = Path(os.environ.get("UV_PYTHON_INSTALL_DIR", "/cache/uv-python"))
    if root.is_dir():
        for path in root.glob("cpython-*-linux-x86_64-gnu"):
            match = re.fullmatch(r"cpython-(\d+\.\d+\.\d+)-linux-x86_64-gnu", path.name)
            if match and (path / "bin/python3").exists():
                available.add(match.group(1))
    if not available:
        raise ValueError("uv Python inventory contained no linux-x86_64 CPython downloads")
    return frozenset(available)


def known_broken_pair(target_id: str, runtime: str, client: str) -> bool:
    """Narrow, observed failures; do not ban unknown/newer client versions.

    These bounds can be removed once a combination has been verified in Docker.
    Runtime comparisons are numeric, not lexicographic.
    """
    def ver(value: str) -> tuple[int, ...]:
        return tuple(map(int, value.split(".")))

    if target_id in {"node-tsx-ts", "node-tsx-js"}:
        # v1.0.0 does not expose a usable tsx binary in the observed npm package.
        # 1.0.0 has no executable .bin/tsx on both Node 23 and 24.
        return (client == "1.0.0" or
                (ver(runtime) >= (25, 0, 0) and ver(client) <= (3, 3, 0)))
    if target_id in {"node-oxc-ts", "node-oxc-js"}:
        # The legacy ESM hooks failed on Node 20.20.2 with 0.0.16-0.0.19,
        # and on Node 20.0.0-20.9.0 with 0.1.3-0.1.7. These are observed
        # loader failures, not TLS fingerprints. Try other genuine pairs.
        return ((ver(runtime) >= (21, 0, 0)
                 and (0, 0, 16) <= ver(client) <= (0, 0, 19))
                or (runtime == "20.20.2"
                    and (0, 0, 16) <= ver(client) <= (0, 0, 19))
                or (ver(runtime) < (20, 10, 0)
                    and (0, 1, 3) <= ver(client) <= (0, 1, 7)))
    if target_id == "python-aiohttp":
        return ((ver(runtime) >= (3, 12, 0) and ver(client) <= (3, 8, 6))
                or (ver(runtime) >= (3, 11, 0) and ver(client) <= (3, 8, 1)))
    if target_id == "python-pycurl":
        # Old C-API sources cannot compile against newer CPython headers.
        return ((ver(runtime) >= (3, 13, 0) and ver(client) <= (7, 45, 1))
                or (ver(runtime) >= (3, 10, 0) and ver(client) <= (7, 43, 0, 6))
                or (runtime == "3.8.12" and client in {"7.45.4", "7.45.5", "7.45.6", "7.45.7"}))
    if target_id == "python-requests":
        return ver(runtime) >= (3, 12, 0) and ver(client) <= (2, 24, 0)
    if target_id == "python-httpx":
        # Old httpcore mutates typing.Union, disallowed by CPython 3.14.
        return (ver(runtime) >= (3, 14, 0)
                and (0, 24, 0) <= ver(client) <= (0, 25, 0))
    if target_id == "python-scrapy":
        return ((ver(runtime) >= (3, 15, 0) and ver(client) <= (2, 19, 0))
                or (ver(runtime) >= (3, 14, 0) and ver(client) <= (2, 13, 3))
                # Scrapy <=2.11.1 needs older Twisted, which is not compatible
                # with the current Python 3.13 collection environment.
                or (ver(runtime) >= (3, 13, 0) and ver(client) <= (2, 11, 1))
                # Scrapy <=2.6.2 still uses APIs removed by recent pyOpenSSL;
                # use a genuine older runtime with old SSL dependencies.
                or (ver(runtime) >= (3, 11, 0) and ver(client) <= (2, 6, 2))
                or (runtime == "3.8.12" and client in {"2.11.1", "2.11.2"}))
    return False
