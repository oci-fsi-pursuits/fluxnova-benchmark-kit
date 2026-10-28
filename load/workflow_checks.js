// W02 open-arrival driver. Local qualification is not an OCI performance result.
import http from 'k6/http';
import { sleep } from 'k6';
import exec from 'k6/execution';
import encoding from 'k6/encoding';
import crypto from 'k6/crypto';
import { Counter } from 'k6/metrics';

const base = (__ENV.W02_ENGINE_URL || '').replace(/\/$/, '');
const support = (__ENV.W02_SUPPORT_URL || '').replace(/\/$/, '');
const runId = __ENV.W02_RUN_ID;
const arm = __ENV.W02_ARM;
const rate = Number(__ENV.W02_RATE || 1);
const duration = Number(__ENV.W02_DURATION_S || 60);
const warmup = Number(__ENV.W02_WARMUP_S || 20);
const drain = Number(__ENV.W02_DRAIN_S || 15);
const deadline = 2;
const poll = 0.1;
const vus = Number(__ENV.W02_VUS || 16);
const corpusText = __ENV.W02_CORPUS_FILE ? open(__ENV.W02_CORPUS_FILE) : null;
const corpus = corpusText === null ? null : JSON.parse(corpusText);
const corpusHash = corpusText === null ? null : crypto.sha256(corpusText, 'hex');
if (corpus !== null && (!__ENV.W02_CORPUS_SHA256 || corpusHash !== __ENV.W02_CORPUS_SHA256 ||
    !Array.isArray(corpus.cases) || corpus.cases.length < 1 || corpus.cases.length > 200)) {
  throw new Error('Invalid or unpinned W02 mixed corpus');
}
if (!__ENV.W02_CORPUS_FILE && __ENV.W02_CORPUS_SHA256) throw new Error('W02 corpus path is required with its hash');
if (!['sequential', 'parallel'].includes(arm) || !runId || runId.length > 64 ||
    !base || !support || !__ENV.W02_REGISTRATION_TOKEN || !__ENV.W02_USERNAME || !__ENV.W02_PASSWORD ||
    ![0.5, 1].includes(rate) || !Number.isInteger(duration * rate) ||
    !Number.isInteger(warmup * rate) || duration < 2 || warmup < 0 || !Number.isFinite(drain) || drain < 1 || !Number.isInteger(vus) || vus < 1) {
  throw new Error('Invalid or incomplete W02 run settings');
}
const engineHeaders = { 'Content-Type': 'application/json', Authorization: 'Basic ' +
  encoding.b64encode(__ENV.W02_USERNAME + ':' + __ENV.W02_PASSWORD) };
const registrarHeaders = { 'Content-Type': 'application/json', Authorization: 'Bearer ' + __ENV.W02_REGISTRATION_TOKEN };
const counts = { warmup: new Counter('warmup_started'), measurement: new Counter('measurement_started') };
const completed = new Counter('measurement_completed');
const engineRequests = new Counter('measurement_engine_requests');
const historyPolls = new Counter('measurement_history_polls');
function scenario(seconds, start) {
  return { executor: 'constant-arrival-rate', rate: rate * 2, timeUnit: '2s',
    duration: `${seconds}s`, startTime: `${start}s`, preAllocatedVUs: vus, maxVUs: vus,
    gracefulStop: `${drain}s` };
}
const scenarios = { measurement: scenario(duration, warmup) };
const thresholds = { 'dropped_iterations{scenario:measurement}': ['count>=0'] };
if (warmup) {
  scenarios.warmup = scenario(warmup, 0);
  thresholds['dropped_iterations{scenario:warmup}'] = ['count>=0'];
}
export const options = { scenarios, thresholds, summaryTrendStats: ['count', 'avg', 'p(95)', 'p(99)', 'max'] };
function now() { return exec.instance.currentTestRunDuration / 1000 - warmup; }
function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value !== null && typeof value === 'object') return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
  return JSON.stringify(value);
}
function parsed(response) { try { return response.json(); } catch (_) { return null; } }
function request(method, path, body, backend = false, until = duration + drain) {
  if (!backend && exec.scenario.name === 'measurement') engineRequests.add(1);
  const remaining = Math.max(1, Math.min(10000, Math.floor((until - now()) * 1000)));
  return http.request(method, (backend ? support : base) + path,
    body === undefined ? null : JSON.stringify(body), {
      headers: backend ? registrarHeaders : engineHeaders, timeout: `${remaining}ms`,
      tags: { name: path.split('?')[0].replace(/[a-f0-9-]{30,}/g, ':id').replace(/\/state\/.*/, '/state/:key'), phase: exec.scenario.name }
    });
}
function emit(event, key, scheduled, status = 'unknown', details = {}, observed) {
  const elapsed = observed === undefined ? now() : observed;
  console.log('BENCH_EVENT ' + JSON.stringify({ event, run_id: runId,
    logical_request_id: key, attempt_id: key + ':1', scenario: corpus ? 'w02_mixed_outcomes' : 'w02_independent_checks',
    phase: exec.scenario.name, at: event === 'completed' ? details.observed_complete_at : new Date().toISOString(), elapsed_s: elapsed,
    status, deadline_s: deadline, details: Object.assign({ scheduled_elapsed_s: scheduled, arm }, details),
    ...(event === 'completed' ? { latency_s: elapsed - scheduled } : {}) }));
}
function problem(key, scheduled, reason, extra = {}) {
  emit('outcome', key, scheduled, 'unknown', Object.assign({ reason }, extra));
  if (exec.scenario.name === 'warmup') exec.test.abort('W02 warmup outcome is unknown; evidence retained.');
}
function makeCase(index, phase) {
  if (corpus) {
    const original = JSON.parse(JSON.stringify(corpus.cases[index % corpus.cases.length]));
    original.template_case_id = original.case_id;
    original.case_id = `mixed-${phase}-${index}`;
    original.payment.payment_id = `synthetic-mixed-${phase}-${index}`;
    return original;
  }
  return { case_id: `approved-${phase}-${index}`, payment: {
    payment_id: `synthetic-${phase}-${index}`, beneficiary: 'synthetic-beneficiary',
    amount_minor: 125, limit_minor: 1000, authorized: true, sanctions_match: false,
    high_risk: false, stale_market: false },
    schedule: { kyc: { delay_ms: 400, fault: 'none' }, risk: { delay_ms: 600, fault: 'none' } },
    expected_outcome: 'settled', expected_checks: { kyc: 'approved', risk: 'accepted' } };
}
export default function () {
  const phase = exec.scenario.name;
  const index = exec.scenario.iterationInTest;
  if (index >= rate * (phase === 'measurement' ? duration : warmup)) return;
  const scheduled = index / rate - (phase === 'warmup' ? warmup : 0);
  const key = `${runId}:${phase}:${index}`;
  const sent = now();
  const until = Math.min(sent + drain, duration + drain);
  counts[phase].add(1);
  if (phase === 'measurement') { completed.add(0); historyPolls.add(0); engineRequests.add(0); }
  emit('scheduled', key, scheduled, 'unknown', { arrival_lag_s: Math.max(0, sent - scheduled) }, scheduled);
  emit('started', key, scheduled, 'unknown', { arrival_lag_s: Math.max(0, sent - scheduled) });
  const original = makeCase(index, phase);
  const inputHash = crypto.sha256(canonical(original), 'hex');
  const start = request('POST', '/process-definition/key/bench_w02_' + arm + '/start', {
    businessKey: key, variables: { studyCaseKey: { value: key, type: 'String' } }
  }, false, until);
  const instance = parsed(start);
  if (start.status !== 200 || !instance || !instance.id) return problem(key, scheduled, 'start_not_confirmed', { http_status: start.status });
  const pid = instance.id;
  emit('accepted', key, scheduled, 'unknown', { process_instance_id: pid, ack_latency_s: now() - sent });
  const registration = request('POST', '/cases/register', { case_key: key, process_id: pid, case: original }, true, until);
  const registered = parsed(registration);
  if (registration.status !== 200 || !registered || registered.input_sha256 !== inputHash ||
      registered.process_id !== pid || registered.case_key !== key) {
    return problem(key, scheduled, 'registration_not_confirmed', { process_instance_id: pid });
  }
  while (now() < until) {
    if (phase === 'measurement') historyPolls.add(1);
    const response = request('GET', '/history/process-instance/' + encodeURIComponent(pid), undefined, false, until);
    const history = parsed(response);
    if (response.status === 200 && history && history.endTime) {
      const observed = now();
      const observedAt = new Date().toISOString();
      const vr = request('GET', '/history/variable-instance?processInstanceId=' + encodeURIComponent(pid) + '&deserializeValues=false', undefined, false, until);
      const ar = request('GET', '/history/activity-instance?processInstanceId=' + encodeURIComponent(pid), undefined, false, until);
      const sr = request('GET', '/state/' + encodeURIComponent(key), undefined, true, until);
      const variables = parsed(vr), activities = parsed(ar), state = parsed(sr);
      let status = 'unknown';
      let reason = 'verification_unavailable';
      let safety = false;
      const values = {};
      if (vr.status === 200 && ar.status === 200 && sr.status === 200 && Array.isArray(variables) &&
          Array.isArray(activities) && state && state.receipts && Array.isArray(state.effects)) {
        variables.forEach(v => { values[v.name] = v.value; });
        const wanted = original.expected_outcome === 'settled' ? [{ payment_id: original.payment.payment_id,
          amount_minor: original.payment.amount_minor, beneficiary: original.payment.beneficiary }] : [];
        const effectsCorrect = canonical(state.effects) === canonical(wanted);
        safety = state.effects.length > 0 && !effectsCorrect;
        const ends = activities.filter(a => a.activityType.toLowerCase().endsWith('endevent') && a.endTime);
        const receiptsCorrect = ['kyc', 'risk', 'settle'].every(op => {
          const r = state.receipts[op];
          return r && r.case_key === key && r.process_id === pid && r.operation === op && r.input_sha256 === inputHash &&
            r.outcome === ({ kyc: original.expected_checks.kyc, risk: original.expected_checks.risk, settle: original.expected_outcome })[op];
        });
        const correct = history.id === pid && history.businessKey === key && history.state === 'COMPLETED' &&
          values.w02Kyc === original.expected_checks.kyc && values.w02Risk === original.expected_checks.risk && values.w02Outcome === original.expected_outcome &&
          state.case_key === key && state.process_id === pid && state.input_sha256 === inputHash &&
          ends.length === 1 && ends[0].activityId === original.expected_outcome && receiptsCorrect && effectsCorrect;
        status = correct ? 'correct' : 'incorrect'; reason = correct ? (corpus ? 'expected_original_outcome_and_effects' : 'exact_original_effect_and_history') : 'original_outcome_mismatch';
      }
      if (phase === 'measurement') completed.add(1);
      emit('completed', key, scheduled, status, { reason, process_instance_id: pid,
        observed_complete_at: observedAt, observed_complete_elapsed_s: observed,
        observation: 'first_durable_history_poll', poll_interval_ms: 100,
        validation_elapsed_s: Math.max(0, now() - observed), verified_at: new Date().toISOString(),
        safety_violation: safety, expected_outcome: original.expected_outcome,
        original, history: { instance: history, variables: values },
        activities, backend: state }, observed);
      if (phase === 'warmup' && status !== 'correct') exec.test.abort('W02 warmup verification failed; evidence retained.');
      return;
    }
    sleep(Math.min(poll, Math.max(0, until - now())));
  }
  problem(key, scheduled, 'completion_observation_timeout', { process_instance_id: pid });
}
export function handleSummary(data) {
  const count = name => data.metrics[name] ? data.metrics[name].values.count : 0;
  data.w02 = { run_id: runId, arm, rate, warmup_s: warmup, duration_s: duration, drain_s: drain,
    case_mode: corpus ? 'mixed_sensitivity' : 'approved', corpus_sha256: corpusHash,
    corpus_fingerprint: corpus ? crypto.sha256(canonical(corpus), 'hex') : null,
    corpus_size: corpus ? corpus.cases.length : 1,
    deadline_s: deadline, poll_interval_ms: 100, fixed_vus: vus,
    measurement: { scheduled: rate * duration, started: count('measurement_started'),
      completed: count('measurement_completed'), dropped: count('dropped_iterations{scenario:measurement}') },
    warmup: { scheduled: rate * warmup, started: count('warmup_started'), dropped: count('dropped_iterations{scenario:warmup}') },
    warning: 'Qualification only unless matched environment/reset/telemetry/headroom evidence is supplied. After any dropped arrival, index-derived scheduling times are approximate and latency is not rankable.' };
  return { [__ENV.W02_OUTPUT_DIR + '/k6-summary.json']: JSON.stringify(data, null, 2) };
}
