<?php
if ($argc !== 4) throw new RuntimeException('usage: probe.php stream|curl stdlib URL');
[$script,$client,$expected,$endpoint]=$argv;
if ($expected !== 'stdlib') throw new RuntimeException('Unexpected client version');
if ($client === 'stream') {
 $context=stream_context_create(['http'=>['header'=>"Accept: application/json\r\nConnection: close\r\n",'timeout'=>25,'follow_location'=>0,'ignore_errors'=>true],
  'ssl'=>['verify_peer'=>true,'verify_peer_name'=>true]]);
 $raw=file_get_contents($endpoint,false,$context);
 if($raw === false) throw new RuntimeException('HTTP stream failure');
 if(!preg_match('/^HTTP\/\S+ 2\d\d/', $http_response_header[0]??''))throw new RuntimeException('HTTP non-2xx');
 $backend='OpenSSL';$backendVersion=OPENSSL_VERSION_TEXT;
} elseif($client === 'curl') {
 $ch=curl_init($endpoint);
 curl_setopt_array($ch,[CURLOPT_RETURNTRANSFER=>true,CURLOPT_HTTPHEADER=>['Accept: application/json','Connection: close'],
  CURLOPT_TIMEOUT=>25,CURLOPT_FOLLOWLOCATION=>false,CURLOPT_PROXY=>'',CURLOPT_NOPROXY=>'*']);
 $raw=curl_exec($ch);$code=curl_getinfo($ch,CURLINFO_HTTP_CODE);
 if($raw === false || $code<200 || $code>=300)throw new RuntimeException('curl failure: '.curl_error($ch));
 $info=curl_version();$backend=$info['ssl_version']??'unknown';$backendVersion=$backend;
 curl_close($ch);
} else throw new RuntimeException('Unknown PHP client');
$echo=json_decode($raw,true,512,JSON_THROW_ON_ERROR);
print json_encode(['runtime_version'=>PHP_VERSION,'client_version'=>'stdlib',
'environment'=>['os'=>PHP_OS_FAMILY,'arch'=>php_uname('m'),
 'tls_backend'=>['name'=>$backend,'version'=>$backendVersion],
 'dependencies'=>$client==='curl'?['libcurl'=>(string)($info['version']??'unknown')]:(object)[]],
 'echo'=>$echo],JSON_THROW_ON_ERROR|JSON_UNESCAPED_SLASHES)."\n";
