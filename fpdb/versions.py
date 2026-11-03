"""Generic version discovery providers. Adapters and language-specific filters are external."""
from __future__ import annotations

import json
from datetime import datetime
from functools import cache
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Literal, TypeAlias, cast

from fpdb.core import DataError, json_object
from fpdb.runtime_compat import remember_pypi_releases, uv_available_python
from fpdb.version_io import go_minimum_cached, host_slot, once_discover
from fpdb.schema import JSONValue, VersionSource

VersionKey: TypeAlias = tuple[tuple[int, int | str], ...]


def sort_key(version: str) -> VersionKey:
    # Natural version ordering (not a full PEP 440/SemVer parser).
    parts = re.findall(r"\d+|[a-zA-Z]+", version.lstrip("vV"))
    return tuple((0, int(part)) if part.isdigit() else (1, part.lower()) for part in parts)


def prerelease(version: str) -> bool:
    return bool(re.search(r"(?:alpha|beta|rc|pre|preview|dev|snapshot|canary|nightly)", version, re.I))


def _get_json(url: str) -> JSONValue:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "TLS-Client-SHA1-DB/1"})
    token = os.environ.get("GITHUB_TOKEN")
    if token and urllib.parse.urlsplit(url).hostname == "api.github.com":
        request.add_header("Authorization", f"Bearer {token}")
    with host_slot(url):
        with urllib.request.urlopen(request, timeout=25) as response:
            return cast(JSONValue, json.load(response))


def _get_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "TLS-Client-SHA1-DB/1"})
    with host_slot(url):
        with urllib.request.urlopen(request, timeout=25) as response:
            return response.read().decode("utf-8")


def _select(data: JSONValue, path: str) -> JSONValue:
    current = data
    if not path:
        return current
    for part in path.split("."):
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            raise DataError(f"Cannot resolve JSON path {path!r}")
    return current


def discover(source: VersionSource) -> list[str]:
    # Single-flight memoization: concurrent targets share one in-progress fetch.
    key = json.dumps(source, sort_keys=True, separators=(",", ":"))
    return once_discover(key, lambda: _discover_uncached(source))


def _discover_uncached(source: VersionSource) -> list[str]:
    values: list[JSONValue]
    kind = source.get("type")
    if kind == "static":
        values = list(source.get("versions", []))
    elif kind == "lua_dist":
        values = list(re.findall(r'href="lua-(5\.[1-5](?:\.[0-9]+)?)\.tar\.gz"',
                                 _get_text("https://www.lua.org/ftp/"), re.I))
    elif kind == "openresty_dist":
        values = list(re.findall(r'href="(?:https://openresty.org/download/|download/)?(?:ngx_)?openresty-([0-9]+(?:\.[0-9]+){3})\.tar\.gz"',
                                 _get_text("https://openresty.org/en/download.html")))
    elif kind == "luajit_dist":
        # Upstream removed the old tags when switching to rolling releases.
        # Recover stable 2.0 releases from their original release commits.
        commits = _get_json("https://api.github.com/repos/LuaJIT/LuaJIT/commits?sha=v2.0&path=src/luajit.h&per_page=100")
        if not isinstance(commits, list):
            raise DataError("Invalid LuaJIT release history")
        values = []
        for item in commits:
            message = json_object(json_object(item)["commit"]).get("message")
            if isinstance(message, str) and (release := re.match(r"^RELEASE LuaJIT-(2\.0\.[0-9]+)(?:\s|$)", message)):
                values.append(release.group(1))
        head = json_object(_get_json("https://api.github.com/repos/LuaJIT/LuaJIT/commits/v2.1"))
        date = json_object(json_object(head["commit"])["committer"])["date"]
        if not isinstance(date, str):
            raise DataError("Invalid LuaJIT commit date")
        values.append("2.1." + str(int(datetime.fromisoformat(date.replace("Z", "+00:00")).timestamp())))
    elif kind == "luarocks":
        package = _source_text(source, "package")
        modules = {"luasec": "lunarmodules/luasec", "http": "daurnimator/http",
                   "lua-resty-http": "pintsized/lua-resty-http"}
        if package not in modules:
            raise DataError("Unsupported LuaRocks HTTP package")
        module = modules[package]
        values = list(re.findall(r'href="/modules/' + re.escape(module)
                                 + r'/([0-9]+(?:\.[0-9]+){0,3}-[0-9]+)"',
                                 _get_text("https://luarocks.org/modules/" + module)))
    elif kind == "go_proxy":
        package = _source_text(source, "package")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", package) or ".." in package:
            raise DataError("Invalid Go module path")
        escaped = "".join("!" + char.lower() if char.isupper() else char for char in package)
        values = [version.removeprefix("v") for version in
                  _get_text(f"https://proxy.golang.org/{escaped}/@v/list").splitlines()]
    elif kind == "maven":
        group, artifact = _source_text(source, "group"), _source_text(source, "artifact")
        if not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", group) or not re.fullmatch(r"[A-Za-z0-9_-]+", artifact):
            raise DataError("Invalid Maven coordinates")
        url = f"https://repo.maven.apache.org/maven2/{group.replace('.', '/')}/{artifact}/maven-metadata.xml"
        try:
            metadata = ET.fromstring(_get_text(url))
        except ET.ParseError as exc:
            raise DataError("Invalid Maven metadata") from exc
        values = [entry.text for entry in metadata.findall("./versioning/versions/version") if entry.text]
    elif kind == "packagist":
        package = _source_text(source, "package")
        if not re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", package):
            raise DataError("Invalid Packagist package")
        payload = json_object(_get_json(f"https://repo.packagist.org/p2/{package}.json"))
        packages = json_object(payload["packages"])
        entries = packages[package]
        if not isinstance(entries, list):
            raise DataError("Invalid Packagist versions")
        values = [json_object(entry)["version"] for entry in entries]
    elif kind == "crates":
        package = _source_text(source, "package")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", package):
            raise DataError("Invalid crate name")
        payload = json_object(_get_json(f"https://crates.io/api/v1/crates/{package}"))
        entries = payload["versions"]
        if not isinstance(entries, list):
            raise DataError("Invalid crate versions")
        values = [entry["num"] for item in entries
                  if not (entry := json_object(item)).get("yanked", False)]
    elif kind == "nuget":
        package = _source_text(source, "package")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", package):
            raise DataError("Invalid NuGet package")
        payload = json_object(_get_json(f"https://api.nuget.org/v3-flatcontainer/{package.lower()}/index.json"))
        entries = payload["versions"]
        if not isinstance(entries, list):
            raise DataError("Invalid NuGet versions")
        values = entries
    elif kind == "json_url":
        url = _source_text(source, "url")
        if not url.startswith("https://"):
            raise DataError("json_url sources require HTTPS")
        payload = _get_json(url)
        items = _select(payload, source.get("items_path", ""))
        if not isinstance(items, list):
            raise DataError("version feed must resolve to a list")
        key = source.get("version_key")
        values = [_select(item, key) if key else item for item in items]
    elif kind == "python_manifest":
        # List only interpreters setup-python can actually install on this runner.
        payload = _get_json(source.get("url", "https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json"))
        if not isinstance(payload, list):
            raise DataError("Invalid actions/python-versions manifest")
        values = [
            entry["version"] for entry in payload
            if isinstance(entry, dict) and entry.get("stable") is True
            and any(
                file.get("platform") == source.get("platform", "linux")
                and file.get("platform_version") == source.get("platform_version", "24.04")
                and file.get("arch") == source.get("arch", "x64")
                for file in _files(entry) if isinstance(file, dict)
            )
        ]
        # Local Docker uses uv-managed runtimes. setup-python's manifest also
        # lists exact patch versions that uv cannot install.
        if os.environ.get("UV_PYTHON_INSTALL_DIR"):
            installable = uv_available_python()
            values = [version for version in values if version in installable]
    elif kind == "node_dist":
        payload = _get_json("https://nodejs.org/dist/index.json")
        if not isinstance(payload, list):
            raise DataError("Invalid Node.js distribution feed")
        # Node.js v26.8.0's release binary reports 26.8.0-alpha.0.0.0.
        # Do not collect a mislabeled runtime as the stable 26.8.0 release.
        values = [_feed_version(item).lstrip("v") for item in payload
                  if isinstance(item, dict) and isinstance(item.get("version"), str) and item.get("version") and
                  item["version"] != "v26.8.0" and
                  "linux-x64" in _files(item)]
    elif kind == "go_dist":
        payload = _get_json("https://go.dev/dl/?mode=json&include=all")
        if not isinstance(payload, list):
            raise DataError("Invalid Go distribution feed")
        values = [_feed_version(item).removeprefix("go") for item in payload
                  if isinstance(item, dict) and item.get("stable") and
                  any(f.get("os") == "linux" and f.get("arch") == "amd64"
                      for f in _files(item) if isinstance(f, dict))]
    elif kind == "npm":
        package = _source_text(source, "package")
        if not re.fullmatch(r"(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+", package, re.I):
            raise DataError("Invalid npm package name")
        payload = _get_json("https://registry.npmjs.org/" + urllib.parse.quote(package, safe="@"))
        if not isinstance(payload, dict) or not isinstance(payload.get("versions"), dict):
            raise DataError("Invalid npm registry response")
        values = [v for v in json_object(payload["versions"]) if re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}", v)]
    elif kind == "rubygems":
        name = _source_text(source, "package")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise DataError("Invalid RubyGems package name")
        payload = _get_json(f"https://rubygems.org/api/v1/versions/{name}.json")
        if not isinstance(payload, list):
            raise DataError("Invalid RubyGems response")
        values = [row["number"] for row in payload if isinstance(row, dict)
                  and not row.get("prerelease") and row.get("number")]
    elif kind == "pypi":
        package = _source_text(source, "package")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", package):
            raise DataError("Invalid PyPI package name")
        payload = _get_json(f"https://pypi.org/pypi/{package}/json")
        if not isinstance(payload, dict) or not isinstance(payload.get("releases"), dict):
            raise DataError("Invalid PyPI project response")
        # Keep per-file Requires-Python for true run-time compatibility checks.
        remember_pypi_releases(package, json_object(payload["releases"]))
        # Prefer numeric stable releases; skip withdrawn releases and empty records.
        values = [
            version for version, files in json_object(payload["releases"]).items()
            if re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version)
            and isinstance(files, list) and files
            and any(isinstance(file, dict) and not file.get("yanked", False) for file in files)
        ]
    elif kind == "github_releases":
        repo = _source_text(source, "repo")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise DataError("github_releases repo must be owner/name")
        values = []
        tag_prefix = source.get("tag_prefix", "")
        max_pages = min(max(int(source.get("max_pages", 3)), 1), 10)
        for page in range(1, max_pages + 1):
            releases = _get_json(f"https://api.github.com/repos/{repo}/releases?per_page=100&page={page}")
            if not isinstance(releases, list):
                raise DataError("Invalid GitHub Releases response")
            for release in releases:
                release = json_object(release)
                if not release.get("draft") and not release.get("prerelease"):
                    tag = release["tag_name"]
                    if not isinstance(tag, str):
                        raise DataError("Invalid GitHub release tag")
                    if tag.startswith(tag_prefix):
                        version = tag[len(tag_prefix):]
                        if not tag_prefix or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
                            values.append(version)
            if len(releases) < 100:
                break
    else:
        raise DataError(f"Unknown version source: {kind!r}")

    if not all(isinstance(v, str) for v in values):
        raise DataError("Version source must return string versions")
    versions = cast(list[str], values)
    if kind in {"go_proxy", "maven", "packagist", "crates", "nuget"}:
        versions = [version.removeprefix("v") for version in versions
                    if re.fullmatch(r"v?[0-9]+(?:\.[0-9]+){1,3}", version)]
    minimum = source.get("min_version")
    maximum = source.get("max_version")

    def version_priority(version: str) -> tuple[VersionKey, str]:
        return sort_key(version), version

    return sorted(set(
        v for v in versions
        if v and (source.get("allow_prerelease", False) or not prerelease(v))
        and (minimum is None or sort_key(v) >= sort_key(minimum))
        and (maximum is None or sort_key(v) <= sort_key(maximum))
    ), key=version_priority, reverse=True)


def _files(entry: dict[str, JSONValue]) -> list[JSONValue]:
    files = entry.get("files", [])
    if not isinstance(files, list):
        raise DataError("Version feed files must be a list")
    return files


def _source_text(source: VersionSource, key: Literal["url", "package", "repo", "group", "artifact"]) -> str:
    value = source.get(key)
    if value is None:
        raise DataError(f"Version source requires {key}")
    return value


def minimum_runtime(source: VersionSource, client_version: str) -> str | None:
    """Read a Go module's declared minimum instead of guessing a toolchain."""
    if source["type"] != "go_proxy":
        return None
    package = _source_text(source, "package")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", package) or ".." in package:
        raise DataError("Invalid Go module path")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}", client_version):
        raise DataError("Invalid Go module version")
    return _go_minimum(package, client_version)


@cache
def _go_minimum(package: str, client_version: str) -> str | None:
    escaped = "".join("!" + char.lower() if char.isupper() else char for char in package)
    url = f"https://proxy.golang.org/{escaped}/@v/v{client_version}.mod"
    return go_minimum_cached(package, client_version, lambda: _get_text(url))


def _feed_version(entry: dict[str, JSONValue]) -> str:
    version = entry["version"]
    if not isinstance(version, str):
        raise DataError("Version feed must contain string versions")
    return version
