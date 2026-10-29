require 'json'
require 'http'
require 'openssl'
require 'rbconfig'

expected, endpoint = ARGV
actual = Gem.loaded_specs.fetch('http').version.to_s
raise "HTTP.rb version #{actual} != #{expected}" unless actual == expected
verify = ENV['FPDB_TLS_VERIFY'] != 'false'
options = verify ? {} : {ssl: {verify_mode: OpenSSL::SSL::VERIFY_NONE, verify_hostname: false}}
response = HTTP.headers('Accept' => 'application/json', 'Connection' => 'close')
               .timeout(connect: 20, read: 25).get(endpoint, **options)
raise "HTTP #{response.code}" unless response.code.between?(200, 299)
echo = JSON.parse(response.to_s)
puts JSON.generate({runtime_version: RUBY_VERSION, client_version: actual, verify: verify,
  environment: {os: RbConfig::CONFIG['host_os'], arch: RbConfig::CONFIG['host_cpu'],
    tls_backend: {name: 'OpenSSL', version: OpenSSL::OPENSSL_VERSION},
    dependencies: {'http' => actual}}, echo: echo})
