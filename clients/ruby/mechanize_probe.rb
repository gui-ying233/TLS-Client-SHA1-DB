require 'json'
require 'mechanize'
require 'openssl'
require 'rbconfig'

expected, endpoint = ARGV
actual = Gem.loaded_specs.fetch('mechanize').version.to_s
raise "Mechanize version #{actual} != #{expected}" unless actual == expected
raise 'HTTPS required' unless endpoint.start_with?('https://')
agent = Mechanize.new
verify = ENV['FPDB_TLS_VERIFY'] != 'false'
agent.verify_mode = OpenSSL::SSL::VERIFY_NONE unless verify
agent.open_timeout = 20
agent.read_timeout = 25
agent.redirect_ok = false
agent.follow_meta_refresh = false
agent.request_headers['Accept'] = 'application/json'
agent.request_headers['Connection'] = 'close'
page = agent.get(endpoint)
raise "HTTP #{page.code}" unless page.code.to_i.between?(200,299)
echo = JSON.parse(page.body)
puts JSON.generate({
  runtime_version: RUBY_VERSION, client_version: actual, verify: verify,
  environment: {os: RbConfig::CONFIG['host_os'], arch: RbConfig::CONFIG['host_cpu'],
    tls_backend: {name: 'OpenSSL', version: OpenSSL::OPENSSL_VERSION},
    dependencies: {'mechanize' => actual}}, echo: echo
})
