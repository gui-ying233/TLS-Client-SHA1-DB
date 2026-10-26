"""Generic version discovery providers. Adapters and language-specific filters are external."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from fpdb.core import DataError


def sort_key(version: str) -> tuple:
    # Natural version ordering (not a full PEP 440/SemVer parser).
    parts = re.findall(r"\d+|[a-zA-Z]+", version.lstrip("vV"))
    return tuple((0, int(part)) if part.isdigit() else (1, part.lower()) for part in parts)


def prerelease(version: str) -> bool:
    return bool(re.search(r"(?:alpha|beta|rc|pre|preview|dev|snapshot|canary|nightly)", version, re.I))


def _get_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "TLS-Client-SHA1-DB/1"})
    token = os.environ.get("GITHUB_TOKEN")
    if token and urllib.parse.urlsplit(url).hostname == "api.github.com":
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.load(response)


def _select(data: object, path: str) -> object:
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


def discover(source: dict) -> list[str]:
    kind = source.get("type")
    if kind == "static":
        values = source.get("versions", [])
    elif kind == "json_url":
        url = source["url"]
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
                for file in entry.get("files", []) if isinstance(file, dict)
            )
        ]
    elif kind == "node_dist":
        payload = _get_json("https://nodejs.org/dist/index.json")
        if not isinstance(payload, list):
            raise DataError("Invalid Node.js distribution feed")
        # Node.js v26.8.0's release binary reports 26.8.0-alpha.0.0.0.
        # Do not collect a mislabeled runtime as the stable 26.8.0 release.
        values = [item["version"].lstrip("v") for item in payload
                  if isinstance(item, dict) and item.get("version") and
                  item["version"] != "v26.8.0" and
                  "linux-x64" in item.get("files", [])]
    elif kind == "go_dist":
        payload = _get_json("https://go.dev/dl/?mode=json&include=all")
        if not isinstance(payload, list):
            raise DataError("Invalid Go distribution feed")
        values = [item["version"].removeprefix("go") for item in payload
                  if isinstance(item, dict) and item.get("stable") and
                  any(f.get("os") == "linux" and f.get("arch") == "amd64"
                      for f in item.get("files", []))]
    elif kind == "npm":
        package = source["package"]
        if not re.fullmatch(r"(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+", package, re.I):
            raise DataError("Invalid npm package name")
        payload = _get_json("https://registry.npmjs.org/" + urllib.parse.quote(package, safe="@"))
        if not isinstance(payload, dict) or not isinstance(payload.get("versions"), dict):
            raise DataError("Invalid npm registry response")
        values = [v for v in payload["versions"] if re.fullmatch(r"[0-9]+(?:\.[0-9]+){2}", v)]
    elif kind == "rubygems":
        name = source["package"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise DataError("Invalid RubyGems package name")
        payload = _get_json(f"https://rubygems.org/api/v1/versions/{name}.json")
        if not isinstance(payload, list):
            raise DataError("Invalid RubyGems response")
        values = [row["number"] for row in payload if isinstance(row, dict)
                  and not row.get("prerelease") and row.get("number")]
    elif kind == "pypi":
        package = source["package"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", package):
            raise DataError("Invalid PyPI package name")
        payload = _get_json(f"https://pypi.org/pypi/{package}/json")
        if not isinstance(payload, dict) or not isinstance(payload.get("releases"), dict):
            raise DataError("Invalid PyPI project response")
        # Prefer numeric stable releases; skip withdrawn releases and empty records.
        values = [
            version for version, files in payload["releases"].items()
            if re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version)
            and isinstance(files, list) and files
            and any(isinstance(file, dict) and not file.get("yanked", False) for file in files)
        ]
    elif kind == "github_releases":
        repo = source["repo"]
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
                if not release.get("draft") and not release.get("prerelease"):
                    tag = release["tag_name"]
                    if tag.startswith(tag_prefix):
                        version = tag[len(tag_prefix):]
                        if not tag_prefix or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
                            values.append(version)
            if len(releases) < 100:
                break
    else:
        raise DataError(f"Unknown version source: {kind!r}")

    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise DataError("Version source must return string versions")
    minimum = source.get("min_version")
    maximum = source.get("max_version")
    return sorted(set(
        v for v in values
        if v and (source.get("allow_prerelease", False) or not prerelease(v))
        and (minimum is None or sort_key(v) >= sort_key(minimum))
        and (maximum is None or sort_key(v) <= sort_key(maximum))
    ), key=lambda v: (sort_key(v), v), reverse=True)
