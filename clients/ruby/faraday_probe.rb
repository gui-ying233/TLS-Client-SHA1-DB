require 'json'
require 'faraday'
require 'openssl'
require 'rbconfig'

expected, endpoint = ARGV
raise 'HTTPS required' unless endpoint.start_with?('https://')
actual = Gem.loaded_specs.fetch('faraday').version.to_s
raise "Faraday version #{actual} != #{expected}" unless actual == expected
verify = ENV['FPDB_TLS_VERIFY'] != 'false'
connection = Faraday.new(url: endpoint, proxy: nil, ssl: {verify: verify}) do |builder|
  builder.adapter :net_http
end
response = connection.get do |request|
  request.headers['Accept'] = 'application/json'
  request.headers['Connection'] = 'close'
  request.options.timeout = 25
  request.options.open_timeout = 20
end
raise "HTTP #{response.status}" unless (200..299).cover?(response.status)
echo = JSON.parse(response.body)
puts JSON.generate({runtime_version: RUBY_VERSION, client_version: actual, verify: verify,
  environment: {os: RbConfig::CONFIG['host_os'], arch: RbConfig::CONFIG['host_cpu'],
    tls_backend: {name: 'OpenSSL', version: OpenSSL::OPENSSL_VERSION},
    dependencies: {'faraday' => actual}}, echo: echo})
