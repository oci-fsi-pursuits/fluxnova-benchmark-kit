// Fixed finite-burst driver. Correctness is decided by the saved-evidence auditor.
import http from 'k6/http';
import exec from 'k6/execution';
import crypto from 'k6/crypto';
import { sleep } from 'k6';

const raw = open(__ENV.FNV06_CASE_FILE);
const fixture = JSON.parse(raw);
const hash = crypto.sha256(raw, 'hex');
const base = (__ENV.FNV06_ENGINE_URL || '').replace(/\/$/, '');
if (fixture.study_id !== 'FNV-06-LQ' || hash !== __ENV.FNV06_CASE_SHA256 || !base || !__ENV.FNV06_AUTH ||
    !['message', 'signal', 'batch'].includes(fixture.kind) || ![1, 4, 16].includes(fixture.population) ||
    fixture.requests.length !== (fixture.kind === 'message' ? fixture.population : 1) ||
    fixture.vus !== (fixture.kind === 'message' ? Math.min(4, fixture.population) : 1) || !Number.isFinite(fixture.release_epoch_ms) ||
    fixture.requests.some(r => !['/message', '/signal', '/process-instance/message-async'].includes(r.path))) {
  throw new Error('Invalid or unpinned FNV-06 qualification fixture');
}
export const options = {
  scenarios: { qualification: { executor: 'shared-iterations', vus: fixture.vus,
    iterations: fixture.requests.length, maxDuration: '45s', gracefulStop: '0s' } },
  maxRedirects: 0,
};
export default function () {
  const index = exec.scenario.iterationInTest;
  const original = fixture.requests[index];
  const wait = (fixture.release_epoch_ms - Date.now()) / 1000;
  if (wait > 0) sleep(wait);
  const started = Date.now();
  const clock = exec.instance.currentTestRunDuration;
  const response = http.post(base + original.path, JSON.stringify(original.body), {
    headers: { 'Content-Type': 'application/json', Authorization: __ENV.FNV06_AUTH }, timeout: '30s',
    tags: { name: original.path },
  });
  const finished = Date.now();
  const finishedClock = exec.instance.currentTestRunDuration;
  let body = null;
  try { body = response.json(); } catch (_) { /* Empty/error bodies remain explicit. */ }
  console.log('FNV06_ACTION ' + JSON.stringify({ study_id: fixture.study_id, run_id: fixture.run_id,
    observation_id: fixture.observation_id, index, vu: exec.vu.idInTest,
    request: original, status: response.status, response: body, error_code: response.error_code || 0,
    started_epoch_ms: started, finished_epoch_ms: finished,
    elapsed_ms: finishedClock - clock,
    scheduled_epoch_ms: fixture.release_epoch_ms }));
}
export function handleSummary(data) {
  return { [__ENV.FNV06_SUMMARY]: JSON.stringify({ fixture_sha256: hash, engine_url: base,
    run_id: fixture.run_id, observation_id: fixture.observation_id, fixed_vus: fixture.vus,
    planned_actions: fixture.requests.length, metrics: data.metrics }, null, 2) };
}
