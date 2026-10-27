import 'dart:convert';
import 'dart:io';

Future<void> main(List<String> args) async {
  if (args.length != 2 || args[0] != 'stdlib') {
    throw ArgumentError('usage: probe.dart stdlib URL');
  }
  final sdk = Platform.version.split(' ').first;
  final arch = (await Process.run('uname', ['-m'])).stdout.toString().trim();
  final context = SecurityContext.defaultContext;
  final caPath = Platform.environment['FPDB_LOCAL_CA'];
  if (caPath != null) context.setTrustedCertificates(caPath);
  final client = HttpClient(context: context)
    ..connectionTimeout = const Duration(seconds: 25)
    ..idleTimeout = Duration.zero
    ..findProxy = ((uri) => 'DIRECT');
  try {
    final request = await client.getUrl(Uri.parse(args[1]));
    request.headers.set(HttpHeaders.acceptHeader, 'application/json');
    request.headers.set(HttpHeaders.connectionHeader, 'close');
    final response = await request.close().timeout(const Duration(seconds: 25));
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw HttpException('HTTP ${response.statusCode}');
    }
    final body = await utf8.decoder.bind(response).join();
    final echo = jsonDecode(body);
    stdout.writeln(jsonEncode({
      'runtime_version': sdk,
      'client_version': 'stdlib',
      'environment': {
        'os': Platform.operatingSystem,
        'arch': arch,
        'tls_backend': {'name': 'dart:io SecureSocket', 'version': sdk}
      },
      'echo': echo
    }));
  } finally {
    client.close(force: true);
  }
}
