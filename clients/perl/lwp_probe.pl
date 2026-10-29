use strict;
use warnings;
use LWP::UserAgent;
use JSON::PP;
use Net::SSLeay;
use Config;
my ($version, $endpoint) = @ARGV;
die "expected system" unless defined($version) && $version eq 'system';
my $agent = LWP::UserAgent->new(agent => "TLS-Client-SHA1-DB/1", timeout => 25, max_redirect => 0, ssl_opts => {verify_hostname => 1});
my $verify = ($ENV{FPDB_TLS_VERIFY} // '') ne 'false';
$agent->ssl_opts(verify_hostname => 0, SSL_verify_mode => 0) unless $verify;
my $request = HTTP::Request->new(GET => $endpoint);
$request->header('Accept' => 'application/json');
$request->header('Connection' => 'close');
my $response = $agent->request($request);
die "HTTP ".$response->status_line unless $response->is_success;
my $echo = decode_json($response->decoded_content);
print encode_json({runtime_version => sprintf('%vd', $^V), client_version => 'system',
    verify => $verify ? JSON::PP::true : JSON::PP::false,
    environment => {os => $^O, arch => $Config{archname},
      tls_backend => {name => 'OpenSSL', version => Net::SSLeay::SSLeay_version(0)},
      dependencies => {'LWP::UserAgent' => $LWP::UserAgent::VERSION}},
    echo => $echo}), "\n";
