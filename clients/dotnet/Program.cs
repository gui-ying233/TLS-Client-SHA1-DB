using System;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Diagnostics;

if (args.Length!=2 || args[0]!="stdlib") throw new Exception("stdlib URL");
var verify=Environment.GetEnvironmentVariable("FPDB_TLS_VERIFY")!="false";
using var handler=new HttpClientHandler{UseProxy=false,AllowAutoRedirect=false};
if (!verify) handler.ServerCertificateCustomValidationCallback=HttpClientHandler.DangerousAcceptAnyServerCertificateValidator;
using var http=new HttpClient(handler){Timeout=TimeSpan.FromSeconds(25)};
using var req=new HttpRequestMessage(HttpMethod.Get,args[1]);
req.Headers.TryAddWithoutValidation("Accept","application/json");
req.Headers.ConnectionClose=true;
using var resp=await http.SendAsync(req);
resp.EnsureSuccessStatusCode();
var body=await resp.Content.ReadAsStringAsync();
using var doc=JsonDocument.Parse(body);
var openssl=new ProcessStartInfo("openssl","version") {RedirectStandardOutput=true,UseShellExecute=false};
using var process=Process.Start(openssl)!;
var version=process.StandardOutput.ReadToEnd().Trim();process.WaitForExit();
Console.WriteLine(JsonSerializer.Serialize(new {
 runtime_version=Environment.Version.ToString(),client_version="stdlib",verify,
 environment=new {os=Environment.OSVersion.Platform.ToString(),arch=System.Runtime.InteropServices.RuntimeInformation.ProcessArchitecture.ToString(),
 tls_backend=new {name="OpenSSL",version}},echo=doc.RootElement
}));
