"""Probe the Ubuntu executable itself. Python orchestrates, never creates the TLS socket."""
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

def run(cmd, *, input=None):
    p=subprocess.run(cmd, input=input, capture_output=True,text=True,timeout=30,check=False)
    if p.returncode:raise RuntimeError(f'{cmd[0]} exit {p.returncode}: {p.stderr[-500:]}')
    return p.stdout

def main():
    client, expected, endpoint=sys.argv[1:]
    if expected!='system':raise ValueError('expected system')
    if not shutil.which(client):raise RuntimeError('not installed: '+client)
    dependencies={}
    if client=='curl':
        info=run(['curl','--version']).splitlines()[0]
        tls=re.search(r'(OpenSSL|GnuTLS|NSS|wolfSSL|mbedTLS|rustls)/([^\s]+)',info,re.I)
        backend={'name':tls.group(1),'version':tls.group(2)} if tls else {'name':'unknown','version':info}
        dependencies['curl']=info.split()[1]
        raw=run(['curl','-sS','--fail','--max-time','25','--noproxy','*','-H','Accept: application/json','-H','Connection: close',endpoint])
    elif client=='wget':
        version=run(['wget','--version']).splitlines()[0]
        details=run(['wget','--version'])
        tls=re.search(r'\+ssl/(openssl|gnutls)',details,re.I)
        name=(tls.group(1).upper() if tls else 'system TLS')
        sslver=run(['openssl','version']).strip() if name=='OPENSSL' else version
        backend={'name':name,'version':sslver}
        dependencies['wget']=version
        raw=run(['wget','--no-proxy','-qO-','--timeout=25','--tries=1','--header=Accept: application/json',endpoint])
    elif client=='openssl':
        version=run(['openssl','version']).strip()
        backend={'name':'OpenSSL','version':version};dependencies['openssl']=version
        url=urlsplit(endpoint)
        if url.scheme!='https' or not url.hostname:raise ValueError('https URL required')
        host=url.hostname;port=url.port or 443
        request=f'GET {url.path or "/"}{("?"+url.query) if url.query else ""} HTTP/1.1\r\nHost: {host}\r\nAccept: application/json\r\nConnection: close\r\n\r\n'
        wire=run(['openssl','s_client','-connect',f'{host}:{port}','-servername',host,'-verify_hostname',host,'-verify_return_error','-ignore_unexpected_eof','-quiet','-ign_eof'], input=request)
        sep='\r\n\r\n' if '\r\n\r\n' in wire else '\n\n'
        headers,_,raw=wire.partition(sep)
        if not headers.startswith('HTTP/') or not re.search(r'^HTTP/\S+ 2\d\d',headers):raise RuntimeError('Bad HTTPS response')
        if re.search(r'(?im)^Transfer-Encoding:\s*chunked', headers):
            parts=[];rest=raw
            while rest:
                line,sep2,rest=rest.partition('\r\n')
                size=int(line.split(';',1)[0],16)
                if not size:break
                parts.append(rest[:size]);rest=rest[size+2:]
            raw=''.join(parts)
    else:raise ValueError('Unknown system client')
    echo=json.loads(raw)
    print(json.dumps({'runtime_version':platform.freedesktop_os_release().get('VERSION_ID','unknown'),
        'client_version':'system','environment':{'os':platform.system().lower(),'arch':platform.machine().lower(),
        'tls_backend':backend,'dependencies':dependencies},'echo':echo}))

if __name__=='__main__':main()
