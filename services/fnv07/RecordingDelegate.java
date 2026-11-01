package org.finos.fluxnova.benchmark;

import java.io.StringReader;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Properties;
import java.util.UUID;
import javax.sql.DataSource;
import org.finos.fluxnova.bpm.engine.delegate.DelegateExecution;
import org.finos.fluxnova.bpm.engine.delegate.JavaDelegate;
import org.finos.fluxnova.bpm.engine.impl.context.Context;

/** Synthetic test delegate. No payment system or live model is called. */
public final class RecordingDelegate implements JavaDelegate {
  private static final HttpClient CLIENT = HttpClient.newBuilder()
      .connectTimeout(Duration.ofSeconds(3)).version(HttpClient.Version.HTTP_1_1)
      .followRedirects(HttpClient.Redirect.NEVER).build();
  private static boolean registered;

  private static String identifier(Object value) {
    if (!(value instanceof String text) || !text.matches("[A-Za-z0-9_.:-]{1,160}")) {
      throw new IllegalArgumentException("Invalid FNV07 binding");
    }
    return text;
  }

  private static String form(Map<String, String> values) {
    StringBuilder body = new StringBuilder();
    values.forEach((key, value) -> {
      if (body.length() != 0) body.append('&');
      body.append(URLEncoder.encode(key, StandardCharsets.UTF_8)).append('=')
          .append(URLEncoder.encode(value, StandardCharsets.UTF_8));
    });
    return body.toString();
  }

  private static HttpResponse<String> post(String route, Map<String, String> values) throws Exception {
    String base = System.getenv("FNV07_PROBE_URL");
    URI uri = URI.create(base + route);
    if (!"http".equals(uri.getScheme()) || uri.getUserInfo() != null || uri.getQuery() != null
        || uri.getFragment() != null) throw new IllegalArgumentException("Invalid private probe endpoint");
    String token = System.getenv("FNV07_EXECUTE_TOKEN");
    if (token == null || token.isEmpty()) throw new IllegalArgumentException("Missing private probe token");
    HttpRequest request = HttpRequest.newBuilder(uri).timeout(Duration.ofSeconds(10))
        .header("Content-Type", "application/x-www-form-urlencoded")
        .header("X-Fnv07-Token", token).POST(HttpRequest.BodyPublishers.ofString(form(values))).build();
    return CLIENT.send(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.US_ASCII));
  }

  private static Object call(Object object, String method) throws Exception {
    return object.getClass().getMethod(method).invoke(object);
  }

  private static synchronized void registerNode(String node) throws Exception {
    if (registered) return;
    Object cfg = Context.getProcessEngineConfiguration();
    Object executor = call(cfg, "getJobExecutor");
    Map<String, String> values = new LinkedHashMap<>();
    values.put("node_id", node);
    values.put("java.version", System.getProperty("java.version"));
    values.put("timezone", System.getProperty("user.timezone", ""));
    values.put("engine.name", String.valueOf(call(cfg, "getProcessEngineName")));
    values.put("history.level", String.valueOf(call(call(cfg, "getHistoryLevel"), "getName")));
    values.put("job.executor_class", executor.getClass().getName());
    values.put("job.deployment_aware", String.valueOf(call(cfg, "isJobExecutorDeploymentAware")));
    for (String method : new String[] {"isActive", "getLockOwner", "getMaxJobsPerAcquisition",
        "getLockTimeInMillis", "getWaitTimeInMillis", "getMaxWait", "getBackoffTimeInMillis", "getMaxBackoff"}) {
      values.put("job." + method, String.valueOf(call(executor, method)));
    }
    Object pool = call(executor, "getTaskExecutor");
    for (String method : new String[] {"getCorePoolSize", "getMaxPoolSize", "getQueueCapacity"}) {
      values.put("pool." + method, String.valueOf(call(pool, method)));
    }
    DataSource source = (DataSource) call(cfg, "getDataSource");
    try (Connection connection = source.getConnection()) {
      values.put("database.isolation", Integer.toString(connection.getTransactionIsolation()));
      values.put("database.product", connection.getMetaData().getDatabaseProductName());
      values.put("database.version", connection.getMetaData().getDatabaseProductVersion());
    }
    HttpResponse<String> response = post("/node", values);
    if (response.statusCode() != 200) throw new IllegalStateException("FNV07 node registration failed");
    System.out.println("FNV07_NODE " + form(values));
    registered = true;
  }

  @Override
  public void execute(DelegateExecution execution) throws Exception {
    Map<String, String> request = new LinkedHashMap<>();
    request.put("run_id", identifier(execution.getVariable("fnv07RunId")));
    request.put("case_key", identifier(execution.getVariable("fnv07CaseKey")));
    request.put("process_id", identifier(execution.getProcessInstanceId()));
    request.put("business_key", identifier(execution.getProcessBusinessKey()));
    request.put("input_sha256", identifier(execution.getVariable("fnv07InputSha256")));
    request.put("stage", identifier(execution.getCurrentActivityId()));
    request.put("node_id", identifier(System.getenv("FNV07_NODE_ID")));
    request.put("invocation_id", UUID.randomUUID().toString());
    System.out.println("FNV07_ATTEMPT " + form(request));
    try {
      registerNode(request.get("node_id"));
      HttpResponse<String> response = post("/execute", request);
      if (response.statusCode() != 200) throw new IllegalStateException("FNV07 probe HTTP " + response.statusCode());
      Properties receipt = new Properties();
      receipt.load(new StringReader(response.body()));
      for (String key : new String[] {"case_key", "process_id", "input_sha256", "stage"}) {
        if (!request.get(key).equals(receipt.getProperty(key))) throw new IllegalStateException("FNV07 receipt binding mismatch");
      }
      String receiptId = identifier(receipt.getProperty("receipt_id"));
      String disposition = receipt.getProperty("disposition");
      if (!"committed".equals(disposition) && !"cached".equals(disposition)) throw new IllegalStateException("FNV07 receipt disposition mismatch");
      Object old = execution.getVariable("completedStages");
      if (!(old instanceof Number count)) throw new IllegalStateException("FNV07 stage counter missing");
      execution.setVariable("completedStages", count.intValue() + 1);
      execution.setVariable(request.get("stage") + "Receipt", receiptId);
      request.put("receipt_id", receiptId);
      request.put("disposition", disposition);
      System.out.println("FNV07_SUCCESS " + form(request));
    } catch (Exception error) {
      request.put("error_class", error.getClass().getName());
      System.out.println("FNV07_FAILURE " + form(request));
      throw error;
    }
  }
}
