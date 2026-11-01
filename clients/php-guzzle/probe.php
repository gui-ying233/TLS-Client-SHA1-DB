<?php
if ($argc !== 4 || !preg_match('/^[a-f0-9]{24}$/', basename($argv[1]))) throw new RuntimeException('usage: probe.php BUILD_DIR VERSION URL');
[$script, $buildDir, $expected, $endpoint] = $argv;
require $buildDir . '/vendor/autoload.php';
$actual = ltrim(Composer\InstalledVersions::getPrettyVersion('guzzlehttp/guzzle'), 'v');
if ($actual !== $expected) throw new RuntimeException("Guzzle $actual != $expected");
$curlOptions = [CURLOPT_FRESH_CONNECT => true];
$verify = getenv('FPDB_TLS_VERIFY') !== 'false';
// Guzzle 8 manages proxy options itself and rejects a raw CURLOPT_NOPROXY.
if (version_compare($actual, '8.0.0', '<')) $curlOptions[CURLOPT_NOPROXY] = '*';
$client = new GuzzleHttp\Client(['http_errors' => true, 'allow_redirects' => false,
    'timeout' => 25, 'proxy' => '', 'headers' => ['Accept' => 'application/json', 'Connection' => 'close'],
    'verify' => $verify, 'curl' => $curlOptions]);
$response = $client->get($endpoint);
$echo = json_decode((string)$response->getBody(), true, 512, JSON_THROW_ON_ERROR);
$curl = curl_version();
print json_encode(['runtime_version' => PHP_VERSION, 'client_version' => $actual, 'verify' => $verify,
    'environment' => ['os' => PHP_OS_FAMILY, 'arch' => php_uname('m'),
    'tls_backend' => ['name' => 'libcurl/' . ($curl['ssl_version'] ?? 'unknown'),
                      'version' => $curl['ssl_version'] ?? 'unknown'],
    'dependencies' => ['guzzlehttp/guzzle' => $actual, 'libcurl' => $curl['version'] ?? 'unknown']],
    'echo' => $echo], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR) . "\n";
