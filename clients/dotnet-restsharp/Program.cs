using System;
using System.Net;
using System.Text.Json;
using System.Diagnostics;
using RestSharp;

if (args.Length != 2 || args[0] != "112.1.0") throw new Exception("112.1.0 URL");
var options = new RestClientOptions(args[1]) {FollowRedirects = false, Timeout = TimeSpan.FromSeconds(25)};
using var client = new RestClient(options);
var request = new RestRequest("", Method.Get);
request.AddHeader("Accept", "application/json");
request.AddHeader("Connection", "close");
var response = await client.ExecuteAsync(request);
if (!response.IsSuccessful || response.Content == null) throw new Exception($"HTTP {(int)response.StatusCode}: {response.ErrorMessage}");
using var doc = JsonDocument.Parse(response.Content);
var openssl = new ProcessStartInfo("openssl", "version") {RedirectStandardOutput = true, UseShellExecute = false};
using var process = Process.Start(openssl)!;
var version = process.StandardOutput.ReadToEnd().Trim(); process.WaitForExit();
Console.WriteLine(JsonSerializer.Serialize(new {
  runtime_version = Environment.Version.ToString(), client_version = "112.1.0",
  environment = new {os = Environment.OSVersion.Platform.ToString(),
    arch = System.Runtime.InteropServices.RuntimeInformation.ProcessArchitecture.ToString(),
    tls_backend = new {name = "OpenSSL", version},
    dependencies = new {RestSharp = "112.1.0"}}, echo = doc.RootElement
}));
