<?php
require __DIR__ . '/vendor/autoload.php';
if ($argc !== 3) throw new RuntimeException('usage: probe.php 7.9.3 URL');
[$script, $expected, $endpoint] = $argv;
$actual = Composer\InstalledVersions::getPrettyVersion('guzzlehttp/guzzle');
if ($actual !== $expected) throw new RuntimeException("Guzzle $actual != $expected");
$client = new GuzzleHttp\Client(['http_errors' => true, 'allow_redirects' => false,
    'timeout' => 25, 'proxy' => '', 'headers' => ['Accept' => 'application/json', 'Connection' => 'close'],
    'curl' => [CURLOPT_NOPROXY => '*', CURLOPT_FRESH_CONNECT => true]]);
$response = $client->get($endpoint);
$echo = json_decode((string)$response->getBody(), true, 512, JSON_THROW_ON_ERROR);
$curl = curl_version();
print json_encode(['runtime_version' => PHP_VERSION, 'client_version' => $actual,
    'environment' => ['os' => PHP_OS_FAMILY, 'arch' => php_uname('m'),
    'tls_backend' => ['name' => 'libcurl/' . ($curl['ssl_version'] ?? 'unknown'),
                      'version' => $curl['ssl_version'] ?? 'unknown'],
    'dependencies' => ['guzzlehttp/guzzle' => $actual, 'libcurl' => $curl['version'] ?? 'unknown']],
    'echo' => $echo], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n";
