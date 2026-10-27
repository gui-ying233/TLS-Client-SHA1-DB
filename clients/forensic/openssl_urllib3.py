"""OpenSSL 3.5.5 s_client with urllib3 1.26.20 cipher policy."""
import json
import os
import platform
import subprocess
import sys
from urllib.parse import urlsplit

CIPHERS = ('ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:'
           'ECDH+AESGCM:DH+AESGCM:ECDH+AES:DH+AES:RSA+AESGCM:'
           'RSA+AES:!aNULL:!eNULL:!MD5:!DSS')


def main():
    expected, endpoint = sys.argv[1:]
    if expected != 'system':
        raise ValueError('Expected system client')
    url = urlsplit(endpoint)
    if url.scheme != 'https' or not url.hostname:
        raise ValueError('HTTPS required')
    prefix = os.path.join(os.environ.get('RUNNER_TEMP', '/tmp'), 'fpdb-openssl-3.5.5')
    exe = os.path.join(prefix, 'bin', 'openssl')
    version = subprocess.check_output([exe, 'version'], text=True).strip()
    if not version.startswith('OpenSSL 3.5.5 '):
        raise ValueError('Incorrect OpenSSL release: ' + version)
    ca = os.environ['FPDB_LOCAL_CA']
    cmd = [exe, 's_client', '-connect', '{}:{}'.format(url.hostname, url.port or 443),
           '-servername', url.hostname, '-alpn', 'http/1.1', '-no_rx_cert_comp',
           '-cipher', CIPHERS, '-CAfile', ca, '-verify_return_error',
           '-ignore_unexpected_eof', '-quiet']
    request = 'GET {} HTTP/1.1\r\nHost: {}\r\nAccept: application/json\r\nConnection: close\r\n\r\n'.format(
        url.path or '/', url.netloc)
    p = subprocess.run(cmd, input=request, text=True, capture_output=True, timeout=25)
    if p.returncode:
        raise RuntimeError('openssl s_client error: {}'.format(p.stderr[-1200:]))
    headers, sep, body = p.stdout.partition('\r\n\r\n' if '\r\n\r\n' in p.stdout else '\n\n')
    if not sep or not headers.startswith('HTTP/1.1 200 '):
        raise RuntimeError('Invalid HTTP echo response')
    echo = json.loads(body)
    print(json.dumps({'runtime_version': 'system', 'client_version': 'system',
                      'environment': {'os': platform.system().lower(),
                                      'arch': platform.machine().lower(),
                                      'tls_backend': {'name': 'OpenSSL', 'version': version},
                                      'dependencies': {'openssl': '3.5.5',
                                                       'cipher_policy': 'urllib3-1.26.20'}},
                      'echo': echo}))


if __name__ == '__main__':
    main()
