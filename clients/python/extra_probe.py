"""Probe genuine Python HTTP libraries; one process = one HTTPS request."""
from __future__ import annotations

import asyncio
import importlib.metadata
import io
import json
import os
import platform
import ssl
import sys
from typing import AsyncIterator, ClassVar

HEADERS = {'Accept': 'application/json', 'Connection': 'close'}
VERIFY = os.environ.get('FPDB_TLS_VERIFY') != 'false'


def fetch(name: str, endpoint: str) -> object:
    if name == 'urllib3':
        import urllib3
        ca = os.environ.get('FPDB_LOCAL_CA')
        if not VERIFY:
            pool = urllib3.PoolManager(retries=False, timeout=urllib3.Timeout(total=25), cert_reqs=ssl.CERT_NONE)
        elif ca:
            pool = urllib3.PoolManager(retries=False, timeout=urllib3.Timeout(total=25), ca_certs=ca)
        else:
            pool = urllib3.PoolManager(retries=False, timeout=urllib3.Timeout(total=25))
        try:
            response = pool.request('GET', endpoint, headers=HEADERS, redirect=False, retries=False)
            if not 200 <= response.status < 300:
                raise RuntimeError('HTTP {}'.format(response.status))
            return json.loads(response.data)
        finally:
            pool.clear()

    if name == 'aiohttp':
        import aiohttp
        async def aiohttp_request() -> object:
            timeout = aiohttp.ClientTimeout(total=25)
            local_ca = os.environ.get('FPDB_LOCAL_CA')
            connector = (aiohttp.TCPConnector(ssl=False) if not VERIFY else
                         aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=local_ca)) if local_ca else None)
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False, connector=connector) as session:
                async with session.get(endpoint, headers=HEADERS, allow_redirects=False) as response:
                    response.raise_for_status()
                    return await response.json(content_type=None)
        return asyncio.run(aiohttp_request())

    if name == 'pycurl':
        import pycurl
        sink = io.BytesIO()
        curl = pycurl.Curl()
        try:
            curl.setopt(pycurl.URL, endpoint)
            if not VERIFY:
                curl.setopt(pycurl.SSL_VERIFYPEER, 0)
                curl.setopt(pycurl.SSL_VERIFYHOST, 0)
            if os.environ.get('FPDB_LOCAL_CA'):
                curl.setopt(pycurl.CAINFO, os.environ['FPDB_LOCAL_CA'])
            # Match the identifier used by the working LWP probe; keep pycurl's TLS stack.
            curl.setopt(pycurl.USERAGENT, 'TLS-Client-SHA1-DB/1')
            curl.setopt(pycurl.HTTPHEADER, ['Accept: application/json', 'Connection: close'])
            curl.setopt(pycurl.PROXY, '')
            curl.setopt(pycurl.FOLLOWLOCATION, 0)
            curl.setopt(pycurl.FRESH_CONNECT, 1)
            curl.setopt(pycurl.FORBID_REUSE, 1)
            curl.setopt(pycurl.TIMEOUT, 25)
            curl.setopt(pycurl.WRITEFUNCTION, sink.write)
            curl.perform()
            status = curl.getinfo(pycurl.RESPONSE_CODE)
            if not 200 <= status < 300:
                raise RuntimeError('HTTP {}'.format(status))
            return json.loads(sink.getvalue())
        finally:
            curl.close()

    if name == 'tornado':
        from tornado.httpclient import AsyncHTTPClient, HTTPRequest
        async def tornado_request() -> object:
            client = AsyncHTTPClient(force_instance=True)
            try:
                request = HTTPRequest(endpoint, method='GET', headers=HEADERS,
                                      request_timeout=25, follow_redirects=False,
                                      validate_cert=VERIFY,
                                      ca_certs=os.environ.get('FPDB_LOCAL_CA'))
                response = await client.fetch(request, raise_error=True)
                return json.loads(response.body)
            finally:
                client.close()
        return asyncio.run(tornado_request())

    if name == 'scrapy':
        import scrapy
        from scrapy.crawler import CrawlerProcess
        from scrapy.http import Response, TextResponse
        from twisted.python.failure import Failure
        class OneRequest(scrapy.Spider):
            name = 'fpdb-single-probe'
            result: ClassVar[object | None] = None
            failure: ClassVar[str | None] = None
            def start_requests(self):
                # Scrapy <2.13 uses start_requests(), not async start().
                yield scrapy.Request(endpoint, headers=HEADERS, dont_filter=True,
                                     callback=self.parse, errback=self.on_failure,
                                     meta={'dont_redirect': True, 'dont_retry': True})
            async def start(self) -> AsyncIterator[scrapy.Request]:
                # Scrapy >=2.13 uses async start(); only one of the two entrypoints runs.
                for request in self.start_requests():
                    yield request
            def parse(self, response: Response, **kwargs: object) -> None:
                if not 200 <= response.status < 300:
                    raise RuntimeError('HTTP {}'.format(response.status))
                if not isinstance(response, TextResponse):
                    raise RuntimeError('Expected a text response')
                type(self).result = json.loads(response.text)
            def on_failure(self, failure: Failure) -> None:
                type(self).failure = failure.getErrorMessage()[:350]
        # No robots.txt fetch, proxy middleware, redirects or retry connections.
        settings: dict[str, object] = {
            'LOG_ENABLED': False, 'ROBOTSTXT_OBEY': False, 'RETRY_ENABLED': False,
            'REDIRECT_ENABLED': False, 'HTTPPROXY_ENABLED': False,
            'COOKIES_ENABLED': False, 'TELNETCONSOLE_ENABLED': False,
            'CONCURRENT_REQUESTS': 1, 'DOWNLOAD_TIMEOUT': 25,
            'DEPTH_LIMIT': 1,
        }
        if not VERIFY:
            settings['DOWNLOAD_VERIFY_CERTIFICATES'] = False
            settings['DOWNLOADER_CLIENTCONTEXTFACTORY'] = 'scrapy.core.downloader.contextfactory.ScrapyClientContextFactory'
        crawler = CrawlerProcess(settings=settings)
        crawler.crawl(OneRequest)
        # A single-shot probe process needs no OS signal handlers. Older
        # Scrapy calls reactor._handleSignals(), removed in newer Twisted.
        crawler.start(stop_after_crawl=True, install_signal_handlers=False)
        if OneRequest.result is None:
            raise RuntimeError('Scrapy request failed: {}'.format(OneRequest.failure))
        return OneRequest.result

    raise ValueError('Unsupported Python client: {}'.format(name))


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit('Usage: extra_probe.py CLIENT VERSION HTTPS_URL')
    name, expected, endpoint = sys.argv[1:]
    if not endpoint.startswith('https://'):
        raise ValueError('HTTPS required')
    actual = importlib.metadata.version(name)
    if actual != expected:
        raise ValueError('Client version mismatch: {} != {}'.format(actual, expected))
    dependencies = {name: actual}
    backend_name = 'OpenSSL'
    backend_version = ssl.OPENSSL_VERSION
    if name == 'pycurl':
        import pycurl
        info = pycurl.version_info()
        backend_name, backend_version = 'libcurl/' + str(info[5]), str(info[5])
        dependencies['libcurl'] = str(info[1])
    if name == 'scrapy':
        # Dependency versions matter when reproducing historical TLS stacks.
        for package in ('Twisted', 'w3lib', 'pyOpenSSL'):
            dependencies[package] = importlib.metadata.version(package)
        backend_name = 'Twisted/OpenSSL'
    echo = fetch(name, endpoint)
    print(json.dumps({
        'runtime_version': platform.python_version(), 'client_version': actual,
        'verify': VERIFY,
        'environment': {'os': platform.system().lower(), 'arch': platform.machine().lower(),
                        'tls_backend': {'name': backend_name, 'version': backend_version},
                        'dependencies': dependencies}, 'echo': echo}, ensure_ascii=False))


if __name__ == '__main__':
    main()
