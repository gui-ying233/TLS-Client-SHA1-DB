using System;
using System.Net;
using System.Text.Json;
using System.Diagnostics;
using RestSharp;

if (args.Length != 2) throw new Exception("VERSION URL");
var actual = File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "client-version.txt")).Trim();
if (actual != args[0]) throw new Exception($"RestSharp {actual} != {args[0]}");
var verify = Environment.GetEnvironmentVariable("FPDB_TLS_VERIFY") != "false";
#if RESTSHARP_LEGACY
var client = new RestClient(args[1]) {FollowRedirects = false, Proxy = null, Timeout = 25000};
if (!verify) client.RemoteCertificateValidationCallback = (_, _, _, _) => true;
var request = new RestRequest("", Method.GET);
#else
var options = new RestClientOptions(args[1]) {FollowRedirects = false};
if (!verify) options.RemoteCertificateValidationCallback = (_, _, _, _) => true;
var client = new RestClient(options);
var request = new RestRequest("", Method.Get);
#endif
// RestClient became IDisposable after the earliest 107.x releases.
using var cleanup = (object)client as IDisposable;
request.AddHeader("Accept", "application/json");
request.AddHeader("Connection", "close");
using var timeout = new System.Threading.CancellationTokenSource(TimeSpan.FromSeconds(25));
var response = await client.ExecuteAsync(request, timeout.Token);
if (!response.IsSuccessful || response.Content == null) throw new Exception($"HTTP {(int)response.StatusCode}: {response.ErrorMessage}");
using var doc = JsonDocument.Parse(response.Content);
var openssl = new ProcessStartInfo("openssl", "version") {RedirectStandardOutput = true, UseShellExecute = false};
using var process = Process.Start(openssl)!;
var version = process.StandardOutput.ReadToEnd().Trim(); process.WaitForExit();
Console.WriteLine(JsonSerializer.Serialize(new {
  runtime_version = Environment.Version.ToString(), client_version = actual, verify,
  environment = new {os = Environment.OSVersion.Platform.ToString(),
    arch = System.Runtime.InteropServices.RuntimeInformation.ProcessArchitecture.ToString(),
    tls_backend = new {name = "OpenSSL", version},
    dependencies = new {RestSharp = actual}}, echo = doc.RootElement
}));
