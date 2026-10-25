use strict;
use warnings;
use WWW::Mechanize;
use JSON::PP;
use Net::SSLeay;
use Config;

my ($expected, $endpoint) = @ARGV;
die "Expected system WWW::Mechanize" unless $expected eq 'system';
die "HTTPS required" unless $endpoint =~ m{^https://};
my $agent = WWW::Mechanize->new(autocheck => 0, timeout => 25);
$agent->max_redirect(0);
my $response = $agent->get($endpoint, Accept => 'application/json', Connection => 'close');
die "HTTP " . $response->code unless $response->is_success;
my $echo = decode_json($response->decoded_content);
my $ssl_version = Net::SSLeay::SSLeay_version(0);
print encode_json({
  runtime_version => sprintf('%vd', $^V), client_version => 'system',
  environment => {os => $^O, arch => $Config{archname},
    tls_backend => {name => 'OpenSSL', version => $ssl_version},
    dependencies => {'WWW::Mechanize' => $WWW::Mechanize::VERSION}},
  echo => $echo
}), "\n";
