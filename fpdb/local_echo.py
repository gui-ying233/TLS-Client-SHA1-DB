"""Loopback HTTPS echo: hash the genuine TLS ClientHello sent to this runner."""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

HOST = "echo.fpdb.test"
PORT = 18443


def _sha1(data: bytes) -> str:
    return base64.b64encode(hashlib.sha1(data).digest()).decode("ascii")


def _client_hello(sock: socket.socket) -> tuple[bytes, bytes, bytes]:
    """Peek through TLS records without consuming bytes needed by OpenSSL."""
    sock.settimeout(10)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        data = sock.recv(65535, socket.MSG_PEEK)
        body = bytearray()
        offset = 0
        while len(data) - offset >= 5:
            kind = data[offset]
            length = int.from_bytes(data[offset + 3:offset + 5], "big")
            if len(data) - offset < 5 + length:
                break
            if kind == 22:
                body.extend(data[offset + 5:offset + 5 + length])
            offset += 5 + length
        if len(body) >= 4 and body[0] == 1:
            hello_length = int.from_bytes(body[1:4], "big")
            if len(body) >= 4 + hello_length:
                hello = bytes(body[4:4 + hello_length])
                pos = 2 + 32
                session_len = hello[pos]
                pos += 1 + session_len
                cipher_len = int.from_bytes(hello[pos:pos + 2], "big")
                pos += 2
                ciphers = hello[pos:pos + cipher_len]
                pos += cipher_len
                compression_len = hello[pos]
                pos += 1 + compression_len
                extensions = bytearray()
                if pos < len(hello):
                    total = int.from_bytes(hello[pos:pos + 2], "big")
                    pos += 2
                    end = pos + total
                    if end != len(hello):
                        raise ValueError("Invalid ClientHello extension length")
                    while pos < end:
                        if pos + 4 > end:
                            raise ValueError("Truncated extension")
                        extensions.extend(hello[pos:pos + 2])
                        length = int.from_bytes(hello[pos + 2:pos + 4], "big")
                        pos += 4 + length
                    if pos != end:
                        raise ValueError("Invalid extension data")
                # Echo's length is the ClientHello handshake message length, not a TLS record.
                return ciphers, bytes(extensions), bytes(body[:4 + hello_length])
        time.sleep(.01)
    raise TimeoutError("Incomplete TLS ClientHello")


def _serve(certdir: Path) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(certdir / "server.crt"), str(certdir / "server.key"))
    context.set_alpn_protocols(["http/1.1"])
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", PORT))
        listener.listen(64)
        while True:
            raw, _ = listener.accept()
            try:
                ciphers, extensions, client_hello = _client_hello(raw)
                with context.wrap_socket(raw, server_side=True) as stream:
                    stream.settimeout(10)
                    # Request content is irrelevant, but consume the HTTP headers.
                    request = bytearray()
                    while b"\r\n\r\n" not in request and len(request) < 65536:
                        fragment = stream.recv(4096)
                        if not fragment:
                            break
                        request.extend(fragment)
                    little = b"".join(extensions[i:i + 2][::-1] for i in range(0, len(extensions), 2))
                    cipher = stream.cipher()
                    if cipher is None:
                        raise ValueError("TLS handshake did not negotiate a cipher")
                    payload = json.dumps({"http": {"tls": {
                        "tlsClientCiphersSha1": _sha1(ciphers),
                        "tlsClientExtensionsSha1": _sha1(extensions),
                        "tlsClientExtensionsSha1Le": _sha1(little),
                        "tlsClientHelloLength": len(client_hello) - 4,
                        "tlsVersion": stream.version(),
                        "tlsCipher": cipher[0],
                        "tlsAlpn": stream.selected_alpn_protocol() or "",
                    }}}).encode()
                    response = (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                                + b"Content-Length: " + str(len(payload)).encode()
                                + b"\r\nConnection: close\r\n\r\n" + payload)
                    stream.sendall(response)
            except (OSError, ValueError, ssl.SSLError) as exc:
                print(f"local TLS echo: {exc}", file=sys.stderr, flush=True)
            finally:
                raw.close()


def _setup(certdir: Path, *, java: bool = False) -> None:
    certdir.mkdir(parents=True, exist_ok=True)
    # Explicit X.509 CA extensions are mandatory for recent Python/OpenSSL:
    # an implicit self-signed certificate may be rejected as a CA without keyUsage.
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                    "-subj", "/CN=FPDB ephemeral local CA",
                    "-addext", "basicConstraints=critical,CA:TRUE",
                    "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                    "-addext", "subjectKeyIdentifier=hash",
                    "-keyout", str(certdir / "ca.key"),
                    "-out", str(certdir / "ca.crt")], check=True, capture_output=True)
    subprocess.run(["openssl", "req", "-newkey", "rsa:2048", "-nodes",
                    "-subj", f"/CN={HOST}", "-keyout", str(certdir / "server.key"),
                    "-out", str(certdir / "server.csr")], check=True, capture_output=True)
    (certdir / "server.ext").write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        f"subjectAltName=DNS:{HOST}\nextendedKeyUsage=serverAuth\n")
    subprocess.run(["openssl", "x509", "-req", "-in", str(certdir / "server.csr"),
                    "-CA", str(certdir / "ca.crt"), "-CAkey", str(certdir / "ca.key"),
                    "-CAcreateserial", "-days", "2", "-out", str(certdir / "server.crt"),
                    "-extfile", str(certdir / "server.ext")], check=True, capture_output=True)
    if sys.platform == "darwin":
        subprocess.run(["sudo", "security", "add-trusted-cert", "-d", "-r", "trustRoot",
                        "-k", "/Library/Keychains/System.keychain", str(certdir / "ca.crt")], check=True)
    else:
        subprocess.run(["sudo", "install", "-m", "644", str(certdir / "ca.crt"),
                        "/usr/local/share/ca-certificates/fpdb-local-echo.crt"], check=True)
        subprocess.run(["sudo", "update-ca-certificates"], check=True, capture_output=True)
    subprocess.run(["sudo", "sh", "-c", f"printf '\\n127.0.0.1 {HOST}\\n' >> /etc/hosts"], check=True)
    if java:
        java_home = os.environ.get("JAVA_HOME")
        keytool = str(Path(java_home) / "bin" / "keytool") if java_home else shutil.which("keytool")
        if not keytool:
            raise RuntimeError("Java TLS collection requires keytool from the selected JDK")
        subprocess.run([keytool, "-importcert", "-noprompt", "-storepass", "changeit",
                        "-keystore", str(certdir / "java-truststore.jks"),
                        "-file", str(certdir / "ca.crt"), "-alias", "fpdb-ephemeral"],
                       check=True)
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as output:
            output.write(f"FPDB_LOCAL_CA={certdir / 'ca.crt'}\n")
            output.write(f"FPDB_LOCAL_CERT_DIR={certdir}\n")
    print(f"Local TLS echo initialized: https://{HOST}:{PORT}/")


def _check(certdir: Path) -> None:
    context = ssl.create_default_context(cafile=str(certdir / "ca.crt"))
    connection = http.client.HTTPSConnection(HOST, PORT, timeout=2, context=context)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        response.read()
        if response.status != 200:
            raise RuntimeError(f"Local TLS echo returned HTTP {response.status}")
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("setup", "serve", "check"))
    parser.add_argument("certdir", type=Path)
    parser.add_argument("--java", action="store_true", help="Create a truststore using the selected JDK")
    args = parser.parse_args()
    if args.command == "setup":
        _setup(args.certdir, java=args.java)
    elif args.command == "check":
        _check(args.certdir)
    else:
        _serve(args.certdir)


if __name__ == "__main__":
    main()
