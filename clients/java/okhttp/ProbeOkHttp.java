import java.net.Proxy;
import java.util.Collections;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import okhttp3.OkHttpClient;
import okhttp3.Protocol;
import okhttp3.Request;
import okhttp3.Response;

public final class ProbeOkHttp {
  static String q(String s) {
    return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"")
      .replace("\n", "\\n").replace("\r", "\\r") + "\"";
  }
  public static void main(String[] args) throws Exception {
    if (args.length != 2) throw new IllegalArgumentException("VERSION URL");
    String jar = OkHttpClient.class.getProtectionDomain().getCodeSource().getLocation().getPath();
    Matcher match = Pattern.compile("okhttp(?:-jvm)?-([0-9.]+)\\.jar$").matcher(jar);
    if (!match.find() || !match.group(1).equals(args[0])) throw new IllegalArgumentException("Unexpected OkHttp jar: " + jar);
    String actual = match.group(1);
    OkHttpClient client = new OkHttpClient.Builder()
      .proxy(Proxy.NO_PROXY).followRedirects(false).followSslRedirects(false)
      .protocols(Collections.singletonList(Protocol.HTTP_1_1)).connectTimeout(25, TimeUnit.SECONDS)
      .readTimeout(25, TimeUnit.SECONDS).build();
    Request request = new Request.Builder().url(args[1]).header("Accept", "application/json")
      .header("Connection", "close").build();
    String body;
    try (Response response = client.newCall(request).execute()) {
      if (!response.isSuccessful() || response.body() == null) throw new IllegalStateException("HTTP " + response.code());
      body = response.body().string();
    } finally {client.connectionPool().evictAll(); client.dispatcher().executorService().shutdown();}
    if (!body.trim().startsWith("{")) throw new IllegalArgumentException("Not JSON object");
    System.out.println("{\"runtime_version\":" + q(System.getProperty("java.version")) +
      ",\"client_version\":" + q(actual) + ",\"environment\":{\"os\":" + q(System.getProperty("os.name")) +
      ",\"arch\":" + q(System.getProperty("os.arch")) +
      ",\"tls_backend\":{\"name\":\"JSSE\",\"version\":" + q(System.getProperty("java.runtime.version")) +
      "},\"dependencies\":{\"okhttp\":" + q(actual) + "}},\"echo\":" + body + "}");
  }
}
