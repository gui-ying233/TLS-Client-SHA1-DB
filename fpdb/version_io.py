"""Shared, host-scoped version fetch limits and on-disk Go metadata cache."""
from __future__ import annotations

from concurrent.futures import Future
from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import time
import urllib.error
import urllib.request
from threading import BoundedSemaphore, Lock, get_ident
from typing import Callable, Generator
from urllib.parse import urlsplit
from fpdb.core import json_object

# Each host gets an independent pool; this is not a global network cap.
_PER_HOST = int(os.environ.get("FPDB_VERSION_PER_HOST", "4"))
if not 1 <= _PER_HOST <= 64:
    raise ValueError("FPDB_VERSION_PER_HOST must be in 1..64")
_HOST_GUARD = Lock()
_HOST_SLOTS: dict[str, BoundedSemaphore] = {}


@contextmanager
def host_slot(url: str) -> Generator[None, None, None]:
    host = urlsplit(url).hostname
    if not host:
        raise ValueError(f"URL has no hostname: {url!r}")
    with _HOST_GUARD:
        slots = _HOST_SLOTS.setdefault(host.lower(), BoundedSemaphore(_PER_HOST))
    with slots:
        yield


def fetch_bytes(request: urllib.request.Request, *, timeout: int = 25) -> bytes:
    """Retry transient transport failures without caching failed discoveries."""
    for attempt in range(3):
        try:
            with host_slot(request.full_url):
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == 2:
                raise
            exc.close()
        except (OSError, http.client.IncompleteRead) as exc:
            if (attempt == 2 or isinstance(exc, ssl.SSLCertVerificationError)
                    or isinstance(exc, urllib.error.URLError)
                    and isinstance(exc.reason, ssl.SSLCertVerificationError)):
                raise
        time.sleep(attempt + 1)
    raise AssertionError("Unreachable HTTP retry state")


# functools.cache may execute concurrent misses more than once. A Future per
# source shares both the result and the in-flight request across plan workers.
_DISCOVERY_GUARD = Lock()
_DISCOVERY: dict[str, Future[tuple[str, ...]]] = {}


def once_discover(key: str, load: Callable[[], list[str]]) -> list[str]:
    with _DISCOVERY_GUARD:
        future = _DISCOVERY.get(key)
        owner = future is None
        if future is None:
            future = Future[tuple[str, ...]]()
            _DISCOVERY[key] = future
    if owner:
        try:
            future.set_result(tuple(load()))
        except BaseException as exc:
            future.set_exception(exc)
            # A failed endpoint may be retried on a later planning request.
            with _DISCOVERY_GUARD:
                if _DISCOVERY.get(key) is future:
                    del _DISCOVERY[key]
            raise
    return list(future.result())


_GO_VERSION = re.compile(r"(?m)^go\s+([0-9]+\.[0-9]+(?:\.[0-9]+)?)\s*(?://[^\n]*)?$")
_VALID_MINIMUM = re.compile(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?")


def go_minimum_cached(package: str, version: str, load: Callable[[], str]) -> str | None:
    """Cache immutable Go proxy .mod metadata between Docker invocations.

    The cache is opt-in outside Docker through FPDB_VERSION_CACHE_DIR. Corrupt
    entries are ignored. A failed download is never cached.
    """
    base = os.environ.get("FPDB_VERSION_CACHE_DIR")
    path: Path | None = None
    if base:
        key = hashlib.sha256(f"{package}\0{version}".encode()).hexdigest()
        path = Path(base) / "go-minimum" / key[:2] / (key + ".json")
        try:
            cached = json_object(json.loads(path.read_text(encoding="utf-8")))
            if (cached.get("package") == package
                    and cached.get("version") == version
                    and "minimum" in cached):
                minimum = cached["minimum"]
                if minimum is None or (isinstance(minimum, str) and _VALID_MINIMUM.fullmatch(minimum)):
                    return minimum
        except (OSError, ValueError, UnicodeError):
            pass

    manifest = load()
    match = _GO_VERSION.search(manifest)
    minimum = match.group(1) if match else None
    if path is not None:
        temporary = path.with_name(f"{path.name}.{os.getpid()}.{get_ident()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps({
                "package": package, "version": version, "minimum": minimum,
            }), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            # Cache availability must never make version discovery fail.
            pass
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return minimum
