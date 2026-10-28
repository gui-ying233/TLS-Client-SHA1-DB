"""One process = one HTTPS request = one new TLS connection."""
from __future__ import annotations

import importlib.metadata
import json
import platform
import ssl
import sys
import urllib.request


def get_echo(client: str, endpoint: str) -> object:
    headers = {"Accept": "application/json", "Connection": "close"}
    if client == "urllib":
        # No proxy; prevent a middlebox from replacing our TLS ClientHello.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(endpoint, headers={**headers, "User-Agent": "TLS-Client-SHA1-DB/1"})
        with opener.open(request, timeout=25) as response:
            return json.load(response)
    if client == "requests":
        import requests
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(endpoint, headers=headers, timeout=25,
                                   verify=__import__('os').environ.get('FPDB_LOCAL_CA', True))
            response.raise_for_status()
            return response.json()
    if client == "httpx":
        import httpx
        with httpx.Client(trust_env=False, http2=False, timeout=25,
                          verify=__import__('os').environ.get('FPDB_LOCAL_CA', True)) as session:
            response = session.get(endpoint, headers=headers)
            response.raise_for_status()
            return response.json()
    raise ValueError("Unknown Python client: " + client)


def installed_versions(client: str) -> dict[str, str]:
    packages = {
        "urllib": (),
        "requests": ("requests", "urllib3", "certifi", "charset-normalizer", "idna"),
        "httpx": ("httpx", "httpcore", "h11", "anyio", "certifi", "sniffio"),
    }[client]
    result: dict[str, str] = {}
    for name in packages:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    return result


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("Usage: probe.py CLIENT EXPECTED_VERSION ENDPOINT")
    client, expected, endpoint = sys.argv[1:]
    dependencies = installed_versions(client)
    actual = "stdlib" if client == "urllib" else dependencies[client]
    runtime = platform.python_version()
    if actual != expected:
        raise ValueError("Client version mismatch: {} != {}".format(actual, expected))
    payload = {
        "runtime_version": runtime,
        "client_version": actual,
        "environment": {
            "os": platform.system().lower(),
            "arch": platform.machine().lower(),
            "tls_backend": {"name": "OpenSSL", "version": ssl.OPENSSL_VERSION},
            "dependencies": dependencies,
        },
        "echo": get_echo(client, endpoint),
    }
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
