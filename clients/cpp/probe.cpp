#include <boost/asio/connect.hpp>
#include <boost/asio/ip/tcp.hpp>
#include <boost/asio/ssl.hpp>
#include <boost/beast/core.hpp>
#include <boost/beast/http.hpp>
#include <boost/beast/ssl.hpp>
#include <openssl/ssl.h>
#include <openssl/x509_vfy.h>
#include <iostream>
#include <regex>
#include <stdexcept>
#include <string>
#include <cstdlib>
#include <sys/utsname.h>

namespace net = boost::asio;
namespace ssl = net::ssl;
namespace beast = boost::beast;
namespace http = beast::http;
using tcp = net::ip::tcp;

std::string quote(const std::string& s) {
  std::string out = "\"";
  for (unsigned char c : s) {
    if (c == '\\' || c == '"') { out += '\\'; out += c; }
    else if (c == '\n') out += "\\n";
    else if (c == '\r') out += "\\r";
    else if (c == '\t') out += "\\t";
    else if (c < 32) out += '?';
    else out += c;
  }
  return out + "\"";
}

int main(int argc, char** argv) {
  try {
    if (argc != 3 || std::string(argv[1]) != "system") throw std::runtime_error("usage: fpdb-cpp system https://...");
    std::smatch match;
    const std::string endpoint(argv[2]);
    if (!std::regex_match(endpoint, match, std::regex(R"(^https://([^/:?#]+)(?::([0-9]+))?([^#]*)$)")))
      throw std::runtime_error("HTTPS URL required");
    const std::string host = match[1];
    const std::string port = match[2].matched ? match[2].str() : "443";
    const std::string path = match[3].str().empty() ? "/" : match[3].str();
    net::io_context ctx;
    const char* mode = std::getenv("FPDB_TLS_VERIFY");
    const bool verify = !mode || std::string(mode) != "false";
    ssl::context tls(ssl::context::tls_client);
    tls.set_default_verify_paths();
    tls.set_verify_mode(verify ? ssl::verify_peer : ssl::verify_none);
    beast::ssl_stream<beast::tcp_stream> stream(ctx, tls);
    if (!SSL_set_tlsext_host_name(stream.native_handle(), host.c_str()))
      throw std::runtime_error("TLS SNI setup failed");
    if (verify && !X509_VERIFY_PARAM_set1_host(SSL_get0_param(stream.native_handle()), host.c_str(), host.size()))
      throw std::runtime_error("TLS hostname validation setup failed");
    tcp::resolver resolver(ctx);
    beast::get_lowest_layer(stream).expires_after(std::chrono::seconds(25));
    beast::get_lowest_layer(stream).connect(resolver.resolve(host, port));
    stream.handshake(ssl::stream_base::client);
    http::request<http::empty_body> req(http::verb::get, path, 11);
    req.set(http::field::host, host);
    req.set(http::field::accept, "application/json");
    req.set(http::field::connection, "close");
    http::write(stream, req);
    beast::flat_buffer buffer;
    http::response<http::string_body> resp;
    http::read(stream, buffer, resp);
    if (resp.result_int() < 200 || resp.result_int() >= 300)
      throw std::runtime_error("HTTP status " + std::to_string(resp.result_int()));
    if (resp.body().size() > (1u << 20) || resp.body().empty() || resp.body()[0] != '{')
      throw std::runtime_error("Echo response is not a JSON object");
    struct utsname system_info{};
    if (uname(&system_info) != 0) throw std::runtime_error("uname failed");
    std::cout << "{\"runtime_version\":\"system\",\"client_version\":\"system\",\"verify\":" << (verify ? "true" : "false") << ",\"environment\":{\"os\":"
      << quote(system_info.sysname) << ",\"arch\":" << quote(system_info.machine)
      << ",\"tls_backend\":{\"name\":\"OpenSSL\",\"version\":"
      << quote(OpenSSL_version(OPENSSL_VERSION)) << "}},\"echo\":" << resp.body() << "}\n";
  } catch (const std::exception& e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
