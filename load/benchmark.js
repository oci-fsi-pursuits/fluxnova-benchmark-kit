import http from 'k6/http';
import { sleep } from 'k6';
import exec from 'k6/execution';
import encoding from 'k6/encoding';
import { Counter, Trend } from 'k6/metrics';

const base = (__ENV.BENCH_BASE_URL || 'http://127.0.0.1:18080/engine-rest').replace(/\/$/, '');
const support = (__ENV.BENCH_SUPPORT_URL || 'http://127.0.0.1:18081').replace(/\/$/, '');
const runId = __ENV.BENCH_RUN_ID || 'UNSET';
const workload = __ENV.BENCH_SCENARIO || 'minimal';
const rate = Number(__ENV.BENCH_RATE || 1);
const duration = Number(__ENV.BENCH_DURATION_S || 30);
const warmup = Number(__ENV.BENCH_WARMUP_S || 0);
const drain = Number(__ENV.BENCH_DRAIN_S || 60);
const deadline = Number(__ENV.BENCH_DEADLINE_S || 30);
const poll = Number(__ENV.BENCH_POLL_INTERVAL_MS || 100) / 1000;
const latePoll = Number(__ENV.BENCH_LATE_POLL_INTERVAL_MS || __ENV.BENCH_POLL_INTERVAL_MS || 100) / 1000;
const abortOnWarmupUnknown = __ENV.BENCH_ABORT_ON_WARMUP_UNKNOWN === 'true';
const payload = 'x'.repeat(Number(__ENV.BENCH_PAYLOAD_BYTES || 64));
const preVus = Number(__ENV.BENCH_PREALLOCATED_VUS || 20);
const maxVus = Number(__ENV.BENCH_MAX_VUS || 100);
const headers = { 'Content-Type': 'application/json' };
if (__ENV.BENCH_USERNAME) headers.Authorization = 'Basic ' + encoding.b64encode(__ENV.BENCH_USERNAME + ':' + (__ENV.BENCH_PASSWORD || ''));
const supportHeaders = { 'Content-Type': 'application/json' };
if (__ENV.BENCH_SUPPORT_TOKEN) supportHeaders.Authorization = 'Bearer ' + __ENV.BENCH_SUPPORT_TOKEN;
const scheduledCount = new Counter('measurement_scheduled');
const startedCount = new Counter('measurement_started');
const completedCount = new Counter('measurement_completed');
const historyPolls = new Counter('measurement_history_polls');
const historyPollErrors = new Counter('measurement_history_poll_errors');
const engineRequests = new Counter('measurement_engine_requests');
const ackLatency = new Trend('start_ack_latency_s', false);
const completionLatency = new Trend('workflow_latency_s', false);
const scenarios = {};
function scenario(seconds, startTime) {
  return { executor: 'constant-arrival-rate', rate, timeUnit: '1s', duration: `${seconds}s`, startTime: `${startTime}s`, preAllocatedVUs: preVus, maxVUs: maxVus, gracefulStop: `${drain}s` };
}
if (warmup > 0) scenarios.warmup = scenario(warmup, 0);
scenarios.measurement = scenario(duration, warmup);
const measurementStart = warmup;
export const options = { scenarios, thresholds: { 'dropped_iterations{scenario:measurement}': ['count>=0'] }, summaryTrendStats: ['count', 'avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'] };

function nowElapsed() { return exec.instance.currentTestRunDuration / 1000 - measurementStart; }
function parsed(response) { try { return response.json(); } catch (_) { return null; } }
function req(method, path, data) {
  if (exec.scenario.name === 'measurement') engineRequests.add(1);
  return http.request(method, base + path, data === undefined ? null : JSON.stringify(data), { headers, timeout: '10s', tags: { name: path.replace(/[a-f0-9-]{30,}/g, ':id'), phase: exec.scenario.name } });
}
function emit(event, id, scheduled, status, details, latency) {
  const item = { event, run_id: runId, logical_request_id: id, attempt_id: id + ':1', scenario: workload, phase: exec.scenario.name, at: new Date().toISOString(), elapsed_s: nowElapsed(), status: status || 'unknown', deadline_s: deadline, details: Object.assign({ scheduled_elapsed_s: scheduled }, details || {}) };
  if (latency !== undefined) item.latency_s = latency;
  if (event === 'completed' && details && details.observed_complete_elapsed_s !== undefined) {
    item.elapsed_s = details.observed_complete_elapsed_s;
    item.at = details.observed_complete_at;
  }
  console.log('BENCH_EVENT ' + JSON.stringify(item));
  // Preserve the triggering event before aborting; scheduled/started events
  // use unknown provisionally and must never trip this guard.
  if (abortOnWarmupUnknown && item.phase === 'warmup' && item.status === 'unknown' &&
      ['error', 'outcome', 'completed'].includes(event)) {
    exec.test.abort('Warmup produced an unknown outcome; see retained BENCH_EVENT evidence.');
  }
}
function verify(instanceId, expected, id) {
  if (exec.scenario.name === 'measurement') historyPolls.add(1);
  const history = req('GET', '/history/process-instance/' + instanceId);
  if (exec.scenario.name === 'measurement' && history.status !== 200) historyPollErrors.add(1);
  if (history.status !== 200) return { terminal: false, reason: 'history_http_' + history.status };
  const instance = parsed(history);
  if (!instance || !instance.endTime) return { terminal: false };
  const observedElapsed = nowElapsed();
  const observedAt = new Date().toISOString();
  const terminal = (status, reason, extra) => Object.assign({ terminal: true, status, reason, observed_complete_elapsed_s: observedElapsed, observed_complete_at: observedAt }, extra || {});
  if (instance.state && instance.state !== 'COMPLETED') return terminal('incorrect', 'terminal_' + instance.state);
  if (instance.businessKey !== id) return terminal('incorrect', 'business_key_mismatch');
  if (workload === 'minimal') return terminal('correct', 'durable_completion_checked');
  const vr = req('GET', '/history/variable-instance?processInstanceId=' + instanceId + '&deserializeValues=false');
  if (vr.status !== 200 || !Array.isArray(parsed(vr))) return terminal('unknown', 'missing_variable_evidence');
  const vars = {};
  parsed(vr).forEach(v => { vars[v.name] = v.value; });
  const payloadVariable = parsed(vr).find(v => v.name === 'payload');
  if (vars.anotherVariable !== expected || !payloadVariable || payloadVariable.type !== 'Bytes') return terminal('incorrect', 'persistent_variable_mismatch');
  const payloadData = req('GET', '/history/variable-instance/' + payloadVariable.id + '/data');
  if (payloadData.status !== 200) return terminal('unknown', 'missing_payload_bytes');
  if (payloadData.body !== payload) return terminal('incorrect', 'persistent_payload_mismatch');
  if (Object.prototype.hasOwnProperty.call(vars, 'aVariable')) return terminal('incorrect', 'transient_variable_persisted');
  if (workload === 'variables') {
    const ar = req('GET', '/history/activity-instance?processInstanceId=' + instanceId + '&activityId=' + (expected ? 'true_end' : 'false_end'));
    if (ar.status !== 200 || !Array.isArray(parsed(ar))) return terminal('unknown', 'missing_branch_evidence');
    return terminal(parsed(ar).some(x => x.endTime) ? 'correct' : 'incorrect', 'branch_checked');
  }
  const receipt = http.get(support + '/state/' + encodeURIComponent(id), { headers: supportHeaders, tags: { name: '/state/:key', phase: exec.scenario.name }, timeout: '10s' });
  const state = parsed(receipt);
  if (receipt.status !== 200 || !state) return terminal('unknown', 'missing_authoritative_state');
  const expectedOutcome = workload === 'kyc' ? 'approved' : workload === 'risk' ? 'accepted' : workload === 'settlement' ? 'settled' : 'ok';
  const correct = vars.workerResult === expectedOutcome && state.result && state.result.outcome === expectedOutcome && (workload !== 'settlement' || state.effect_count === 1);
  return terminal(correct ? 'correct' : 'incorrect', 'engine_and_ledger_checked', { safety_violation: state.effect_count > 1, violations: state.violations || [] });
}
export default function () {
  const index = exec.scenario.iterationInTest;
  const phase = exec.scenario.name;
  // Some k6/platform timer combinations dispatch the exact end-boundary slot.
  // The measurement cohort is half-open: [0, duration). Never start extra work.
  if (index >= rate * (phase === 'measurement' ? duration : warmup)) return;
  const id = `${runId}:${phase}:${workload}:${index}`;
  const scheduled = phase === 'measurement' ? index / rate : index / rate - measurementStart;
  const started = nowElapsed();
  if (phase === 'measurement') {
    scheduledCount.add(1); startedCount.add(1);
    historyPolls.add(0); historyPollErrors.add(0); engineRequests.add(0);
  }
  emit('scheduled', id, scheduled, 'unknown', { arrival_lag_s: Math.max(0, started - scheduled) });
  emit('started', id, scheduled, 'unknown', { arrival_lag_s: Math.max(0, started - scheduled) });
  const branch = index % 2 === 0;
  const body = { businessKey: id, variables: {
    anotherVariable: { value: branch, type: 'Boolean' },
    aVariable: { value: 'transient', type: 'String', valueInfo: { transient: true } },
    payload: { value: encoding.b64encode(payload), type: 'Bytes' },
    run_id: { value: runId, type: 'String' },
    scenario: { value: workload, type: 'String' },
    logical_request_id: { value: id, type: 'String' }
  } };
  const response = req('POST', '/process-definition/key/bench_' + workload + '/start', body);
  ackLatency.add(nowElapsed() - started, { phase });
  const instance = parsed(response);
  if (response.status !== 200 || !instance || !instance.id) {
    emit('error', id, scheduled, 'unknown', { reason: 'start_not_confirmed', http_status: response.status, safety_violation: false });
    return;
  }
  emit('accepted', id, scheduled, 'unknown', { process_instance_id: instance.id, ack_latency_s: nowElapsed() - started });
  let result = { terminal: false };
  while (nowElapsed() - started < drain) {
    result = verify(instance.id, branch, id);
    if (result.terminal) {
      const latency = result.observed_complete_elapsed_s - scheduled;
      completionLatency.add(latency, { phase, outcome: result.status });
      if (phase === 'measurement') completedCount.add(1);
      emit('completed', id, scheduled, result.status, Object.assign({ process_instance_id: instance.id, observation: 'first_durable_history_observation', poll_interval_ms: poll * 1000, late_poll_interval_ms: latePoll * 1000, validation_elapsed_s: Math.max(0, nowElapsed() - result.observed_complete_elapsed_s), verified_at: new Date().toISOString() }, result), latency);
      return;
    }
    // Once the declared deadline has passed, further rapid polling cannot
    // recover a deadline success and can amplify load behind slow workflows.
    const interval = nowElapsed() - scheduled >= deadline ? latePoll : poll;
    sleep(Math.min(interval, Math.max(0, drain - (nowElapsed() - started))));
  }
  emit('outcome', id, scheduled, 'unknown', { reason: 'completion_observation_timeout', process_instance_id: instance.id, last_observation: result.reason }, nowElapsed() - scheduled);
}
export function handleSummary(data) {
  data.polling_policy = { before_deadline_interval_ms: poll * 1000, after_deadline_interval_ms: latePoll * 1000 };
  data.warmup_guard = { abort_on_unknown: abortOnWarmupUnknown };
  data.measurement = { scheduled: rate * duration, started: data.metrics.measurement_started ? data.metrics.measurement_started.values.count : 0, dropped: data.metrics['dropped_iterations{scenario:measurement}'] ? data.metrics['dropped_iterations{scenario:measurement}'].values.count : 0 };
  data.benchmark = { run_id: runId, scenario: workload, scheduled: rate * duration, started: data.measurement.started, measurement_duration_s: duration, warmup_s: warmup, drain_s: drain, generator_validated: false, telemetry_complete: false, completion_observation: 'first durable history poll; includes polling delay but excludes subsequent oracle verification; verification timing retained separately', scheduled_source: 'fixed_arrival_plan', phase_schedule: { measurement_start_s: measurementStart, planned_max_duration_s: warmup + duration + drain }, warning: 'Warmup in-flight work may overlap measurement. Validate generator headroom, capture completeness and per-phase dropped iteration evidence before ranking.' };
  const path = (__ENV.BENCH_OUTPUT_DIR || '.') + '/k6-summary.json';
  return { [path]: JSON.stringify(data, null, 2) };
}
