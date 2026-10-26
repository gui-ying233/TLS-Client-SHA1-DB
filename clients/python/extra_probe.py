"""Probe genuine Python HTTP libraries; one process = one HTTPS request."""
import asyncio
import importlib.metadata
import io
import json
import platform
import ssl
import sys

HEADERS = {'Accept': 'application/json', 'Connection': 'close'}


def fetch(name, endpoint):
    if name == 'urllib3':
        import urllib3
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
        async def do_request():
            timeout = aiohttp.ClientTimeout(total=25)
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
                async with session.get(endpoint, headers=HEADERS, allow_redirects=False) as response:
                    response.raise_for_status()
                    return await response.json(content_type=None)
        return asyncio.run(do_request())

    if name == 'pycurl':
        import pycurl
        sink = io.BytesIO()
        curl = pycurl.Curl()
        try:
            curl.setopt(pycurl.URL, endpoint)
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
        async def do_request():
            client = AsyncHTTPClient(force_instance=True)
            try:
                request = HTTPRequest(endpoint, method='GET', headers=HEADERS,
                                      request_timeout=25, follow_redirects=False)
                response = await client.fetch(request, raise_error=True)
                return json.loads(response.body)
            finally:
                client.close()
        return asyncio.run(do_request())

    if name == 'scrapy':
        import scrapy
        from scrapy.crawler import CrawlerProcess
        class OneRequest(scrapy.Spider):
            name = 'fpdb-single-probe'
            result = None
            failure = None
            async def start(self):
                yield scrapy.Request(endpoint, headers=HEADERS, dont_filter=True,
                                     callback=self.parse, errback=self.on_failure,
                                     meta={'dont_redirect': True, 'dont_retry': True})
            def parse(self, response):
                if not 200 <= response.status < 300:
                    raise RuntimeError('HTTP {}'.format(response.status))
                type(self).result = json.loads(response.text)
            def on_failure(self, failure):
                type(self).failure = str(failure.value)[:350]
        # No robots.txt fetch, proxy middleware, redirects or retry connections.
        crawler = CrawlerProcess(settings={
            'LOG_ENABLED': False, 'ROBOTSTXT_OBEY': False, 'RETRY_ENABLED': False,
            'REDIRECT_ENABLED': False, 'HTTPPROXY_ENABLED': False,
            'COOKIES_ENABLED': False, 'TELNETCONSOLE_ENABLED': False,
            'CONCURRENT_REQUESTS': 1, 'DOWNLOAD_TIMEOUT': 25,
            'DEPTH_LIMIT': 1,
        })
        crawler.crawl(OneRequest)
        crawler.start(stop_after_crawl=True)
        if OneRequest.result is None:
            raise RuntimeError('Scrapy request failed: {}'.format(OneRequest.failure))
        return OneRequest.result

    raise ValueError('Unsupported Python client: {}'.format(name))


def main():
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
        dependencies['Twisted'] = importlib.metadata.version('Twisted')
        backend_name = 'Twisted/OpenSSL'
    echo = fetch(name, endpoint)
    print(json.dumps({
        'runtime_version': platform.python_version(), 'client_version': actual,
        'environment': {'os': platform.system().lower(), 'arch': platform.machine().lower(),
                        'tls_backend': {'name': backend_name, 'version': backend_version},
                        'dependencies': dependencies}, 'echo': echo}, ensure_ascii=False))


if __name__ == '__main__':
    main()
