import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

/** Standalone shared-client diagnostic. No engine, model or payment calls. */
public class HttpProbeClient {
  public static void main(String[] args) throws Exception {
    URI uri = URI.create(args[0]);
    if (!"http".equals(uri.getScheme()) || !"127.0.0.1".equals(uri.getHost())
        || !"/node".equals(uri.getPath()) || uri.getUserInfo() != null
        || uri.getQuery() != null || uri.getFragment() != null) {
      throw new IllegalArgumentException("Loopback fixture only");
    }
    int concurrency = Integer.parseInt(args[1]), requests = Integer.parseInt(args[2]);
    if (concurrency < 1 || concurrency > 16 || requests < 1 || requests > 10000) {
      throw new IllegalArgumentException("Bounded diagnostic required");
    }
    String token = System.getenv("FNV07_EXECUTE_TOKEN");
    if (token == null || token.isEmpty()) throw new IllegalArgumentException("Missing fixture token");
    try (HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3))
          .version(HttpClient.Version.HTTP_1_1).followRedirects(HttpClient.Redirect.NEVER).build();
         var pool = Executors.newFixedThreadPool(concurrency)) {
      CountDownLatch start = new CountDownLatch(1);
      AtomicInteger next = new AtomicInteger();
      for (int worker = 0; worker < concurrency; worker++) {
        pool.submit(() -> {
          try { start.await(); } catch (InterruptedException e) { Thread.currentThread().interrupt(); return; }
          for (int id = next.getAndIncrement(); id < requests; id = next.getAndIncrement()) {
            long before = System.nanoTime();
            int status = 0;
            String outcome = "error", error = "none";
            try {
              HttpRequest request = HttpRequest.newBuilder(uri).timeout(Duration.ofSeconds(10))
                  .header("Content-Type", "application/x-www-form-urlencoded")
                  .header("X-Fnv07-Token", token)
                  .header("X-Fnv07-Diagnostic-Id", Integer.toString(id))
                  .POST(HttpRequest.BodyPublishers.ofString("node_id=n1&runtime=http-reproducer")).build();
              HttpResponse<String> response = client.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.US_ASCII));
              status = response.statusCode();
              outcome = status == 200 && response.body().equals("registered=n1\n") ? "ok" : "bad_response";
            } catch (Exception e) {
              error = e.getClass().getSimpleName();
              Throwable cause = e.getCause();
              for (int depth = 0; cause != null && depth < 4; depth++, cause = cause.getCause()) {
                error += ":" + cause.getClass().getSimpleName();
              }
            }
            System.out.println(id + "\t" + outcome + "\t" + status + "\t" + (System.nanoTime() - before) + "\t" + error);
          }
        });
      }
      start.countDown();
    }
  }
}
