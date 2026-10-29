use strict;
use warnings;
use IO::Socket::SSL;
use JSON::PP;
use Config;
my ($version, $endpoint) = @ARGV;
die "expected system" unless $version eq 'system';
my $verify = ($ENV{FPDB_TLS_VERIFY} // '') ne 'false';
my ($host, $port, $path) = $endpoint =~ m{^https://([^/:]+)(?::([0-9]+))?(/.*)?$} or die "HTTPS endpoint required";
$port ||= 443;
$path //= '/';
my $sock=IO::Socket::SSL->new(PeerHost=>$host,PeerPort=>$port,SSL_hostname=>$host,
 SSL_verify_mode=>$verify ? IO::Socket::SSL::SSL_VERIFY_PEER() : IO::Socket::SSL::SSL_VERIFY_NONE(),SSL_verifycn_name=>$host,
 ($verify ? () : (SSL_verifycn_scheme=>'none')),
 Timeout=>25) or die IO::Socket::SSL::errstr();
print $sock "GET $path HTTP/1.1\r\nHost: $host\r\nAccept: application/json\r\nConnection: close\r\n\r\n";
my $wire='';while(my $line=<$sock>){$wire.=$line;}close $sock;
my ($head,$body)=split /\r?\n\r?\n/, $wire, 2;
die "Bad status" unless $head =~ /^HTTP\/\S+ 2\d\d/;
if ($head =~ /^Transfer-Encoding:\s*chunked/im) {
  my $decoded='';
  while (length $body) {
    $body =~ s/^([0-9a-fA-F]+)[^\r\n]*\r?\n// or die "invalid chunk size";
    my $size=hex($1);last if $size==0;
    $decoded.=substr($body,0,$size,'');
    $body =~ s/^\r?\n// or die "invalid chunk end";
  }
  $body=$decoded;
}
my $echo=decode_json($body);
my $ssl_version=Net::SSLeay::SSLeay_version(0);
print encode_json({runtime_version=>sprintf('%vd',$^V),client_version=>'system',verify=>$verify ? JSON::PP::true : JSON::PP::false,
 environment=>{os=>$^O,arch=>$Config{archname},tls_backend=>{name=>'OpenSSL',version=>$ssl_version},
 dependencies=>{'IO::Socket::SSL'=>$IO::Socket::SSL::VERSION}},echo=>$echo}),"\n";
