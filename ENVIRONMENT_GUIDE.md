# OS/TLS environment sampling and confidence-aware lookup

This suite measures native HTTP clients only. No browser ClientHello profiles,
TLS impersonation, or man-in-the-middle TLS proxy is used. Each subprocess
opens a new connection; previous fingerprints are never replayed as results.

## Matrix

`config.environments.json` adds distro-native Debian 13, Ubuntu 26.04 and
Alpine 3.22 probes, a native `ubuntu-26.04` runner, and macOS 15 (arm64/Intel)
and macOS 26 (arm64), using system Python and Homebrew Python. These targets
are separate from the older Ubuntu 24.04 matrix.

The only forced-protocol experiment is
`env-debian13-curl-http1-control` (`capture_profile=http1-explicit-control`).
All other targets retain the client's native/default protocol behavior. The
container probes install *distribution packages*, not official python.org
binaries; macOS system/HB clients are not interchangeable.

Two expensive source-build experiments are defined but disabled: OpenSSL 3.6.0
with CPython 3.14.8, and LibreSSL 2.8.3 with CPython 3.9.6. Their Dockerfiles
are starting controls, NOT independently verified historical reproductions.
Validate package checksums, build configuration, supported ciphers and CA
verification before enabling them; these legacy builds may require fixes.

## Provenance

New records include `identity.environment.image`, real `tls_backend`,
`dependencies`, `runtime_resolved_version`, and `capture_profile`. The new
optional `evidence` array stores actual co-observed SHA-1 triples and optional
Echo-supplied `tlsClientHelloLength`, `tlsVersion`, `tlsCipher` and `tlsAlpn`,
with a count per identical row. It retains at most 128 most frequent distinct
rows, setting `evidence_truncated=true` when truncation occurred. Existing
schema-v1 records remain readable; data not captured historically is **not**
backfilled or fabricated.

The legacy `observations` representation is retained for compatibility. When
new evidence is available, the three per-SHA1 JSON indexes are regenerated from
real joint evidence rows and remaining legacy summary rows, without combining
hashes from different handshakes or erasing historical matches on migration.

## Query

```sh
python -m fpdb.cli lookup --extensions '4oF0LRxQXUDhrRY0MazQAVJh7Go='
python -m fpdb.cli lookup --ciphers 'hCCNuWP9ky6AR69i97wdKYbhFQo=' \
  --extensions '4oF0LRxQXUDhrRY0MazQAVJh7Go=' \
  --extensions-le 'Ea9swA+X7+AQtmDnJ28MlyPK/Fg='
```

Matching one hash is a broad candidate; a two- or three-field joint match is
stronger *technical evidence*, not proof of a unique application or operator.
ClientHello length depends on SNI, ALPN and padding and is reported only as
auxiliary evidence. Echo does not expose extension order/preimages, so none is
invented. The offline lookup makes no assertion about user agent or intent.

## Execution/costs

The macOS jobs consume macOS Actions minutes, which cost more in private repos.
The Docker jobs install distro packages per new runner (and need Docker pull
access); initial runs will be slower. The source-build controls are opt-in.

The workflow still uses `--target` filtering. To capture only a particular
profile, use e.g. `gh workflow run historical-fingerprints.yml \
  -f mode=historical -f target=env-debian13-requests`.

Run `python -m unittest discover -s tests -v` for offline schema/lookup tests.
