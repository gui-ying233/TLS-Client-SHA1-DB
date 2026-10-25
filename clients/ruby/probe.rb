require 'json'
require 'net/http'
require 'openssl'
require 'rbconfig'
client_version, endpoint = ARGV
raise 'expected stdlib' unless client_version == 'stdlib'
uri = URI(endpoint)
http = Net::HTTP.new(uri.host, uri.port, nil)
http.use_ssl = true
http.open_timeout = 20
http.read_timeout = 25
http.start do |session|
  res = session.get(uri.request_uri, {'Accept'=>'application/json', 'Connection'=>'close'})
  raise "HTTP #{res.code}" unless res.is_a?(Net::HTTPSuccess)
  echo = JSON.parse(res.body)
  puts JSON.generate({runtime_version:RUBY_VERSION,client_version:'stdlib',
    environment:{os:RbConfig::CONFIG['host_os'],arch:RbConfig::CONFIG['host_cpu'],
    tls_backend:{name:'OpenSSL',version:OpenSSL::OPENSSL_VERSION}},echo:echo})
end
