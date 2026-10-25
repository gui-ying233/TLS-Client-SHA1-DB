import java.net.URI;
import java.net.HttpURLConnection;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;

public class Probe {
 static String q(String s) {
   return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r") + "\"";
 }
 public static void main(String[] args) throws Exception {
  if(args.length < 2 || !args[0].equals("stdlib")) throw new IllegalArgumentException("stdlib URL [urlconnection]");
  String body;
  if(args.length>2 && args[2].equals("urlconnection")) {
   HttpURLConnection c=(HttpURLConnection)URI.create(args[1]).toURL().openConnection(java.net.Proxy.NO_PROXY);
   c.setConnectTimeout(25000);c.setReadTimeout(25000);c.setRequestProperty("Accept","application/json");
   c.setRequestProperty("Connection","close");c.setInstanceFollowRedirects(false);
   if(c.getResponseCode()!=200) throw new IllegalStateException("HTTP "+c.getResponseCode());
   try(var in=c.getInputStream()){body=new String(in.readAllBytes(),StandardCharsets.UTF_8);}finally{c.disconnect();}
  } else {
   HttpClient http=HttpClient.newBuilder().proxy(new java.net.ProxySelector(){
     public java.util.List<java.net.Proxy> select(URI uri){return java.util.List.of(java.net.Proxy.NO_PROXY);}
     public void connectFailed(URI uri,java.net.SocketAddress a,java.io.IOException e){}
   }).version(HttpClient.Version.HTTP_1_1).followRedirects(HttpClient.Redirect.NEVER)
     .connectTimeout(Duration.ofSeconds(25)).build();
   HttpRequest req=HttpRequest.newBuilder(URI.create(args[1])).header("Accept","application/json").GET().build();
   HttpResponse<String> response=http.send(req,HttpResponse.BodyHandlers.ofString());
   if(response.statusCode()!=200) throw new IllegalStateException("HTTP "+response.statusCode());body=response.body();
  }
  if(!body.trim().startsWith("{")) throw new IllegalArgumentException("Not JSON object");
  System.out.println("{\"runtime_version\":"+q(System.getProperty("java.version"))+
   ",\"client_version\":\"stdlib\",\"environment\":{\"os\":"+q(System.getProperty("os.name"))+
   ",\"arch\":"+q(System.getProperty("os.arch"))+
   ",\"tls_backend\":{\"name\":\"JSSE\",\"version\":"+q(System.getProperty("java.runtime.version"))+"}},\"echo\":"+body+"}");
 }
}
