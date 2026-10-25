"""OS-distribution-native requests/curl probe, no browser impersonation."""
import importlib.metadata
import json
import os
import platform
import ssl
import subprocess
import sys


def distro():
    try:
        with open('/etc/os-release', encoding='utf-8') as stream:
            pairs = dict(line.rstrip().split('=', 1) for line in stream if '=' in line)
        return pairs.get('ID', 'unknown').strip('"') + ':' + pairs.get('VERSION_ID', '?').strip('"')
    except OSError:
        return platform.platform()


def main():
    kind, endpoint = sys.argv[1:]
    if not endpoint.startswith('https://'):
        raise ValueError('HTTPS required')
    dependencies = {}
    if kind == 'requests':
        import requests
        with requests.Session() as session:
            session.trust_env = False
            response = session.get(endpoint, headers={'Accept': 'application/json', 'Connection': 'close'},
                                   timeout=25, allow_redirects=False)
            response.raise_for_status()
            echo = response.json()
        version = requests.__version__
        dependencies['requests'] = version
        dependencies['urllib3'] = importlib.metadata.version('urllib3')
        backend = {'name': 'LibreSSL' if 'LibreSSL' in ssl.OPENSSL_VERSION else 'OpenSSL',
                   'version': ssl.OPENSSL_VERSION}
    elif kind in ('curl', 'curl-http1'):
        cmd = ['curl', '--silent', '--show-error', '--fail', '--max-time', '25',
               '--noproxy', '*', '--header', 'Accept: application/json',
               '--header', 'Connection: close']
        if kind == 'curl-http1':
            cmd += ['--http1.1']
        cmd += [endpoint]
        echo = json.loads(subprocess.check_output(cmd, text=True, timeout=30))
        version_text = subprocess.check_output(['curl', '--version'], text=True).splitlines()[0]
        parts = version_text.split()
        version = parts[1]
        dependencies['curl'] = version
        backend = {'name': 'libcurl', 'version': version_text[:128]}
    else:
        raise ValueError('Unknown environment client')
    print(json.dumps({
        'runtime_version': platform.python_version() if kind == 'requests' else 'system',
        'client_version': 'system',
        'environment': {'os': platform.system().lower(), 'arch': platform.machine().lower(),
                        'image': os.getenv('FPDB_IMAGE_LABEL', distro()), 'tls_backend': backend, 'dependencies': dependencies},
        'echo': echo,
    }))

if __name__ == '__main__':
    main()
