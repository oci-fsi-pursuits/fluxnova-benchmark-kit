// Negative control: no HTTP, credentials, or Fluxnova access.
// Run in a network-disabled container. Preserve raw counts; do not subtract one.
import { sleep } from 'k6';
import { Counter } from 'k6/metrics';

const started = new Counter('measurement_started');
export const options = {
  scenarios: {
    measurement: {
      executor: 'constant-arrival-rate', rate: 10, timeUnit: '1s', duration: '4s',
      preAllocatedVUs: 1, maxVUs: 1, gracefulStop: '2s',
    },
  },
  thresholds: { 'dropped_iterations{scenario:measurement}': ['count>=0'] },
};

export default function () {
  started.add(1);
  sleep(0.35); // Occupy the only VU to deliberately produce dropped iterations.
}

export function handleSummary(data) {
  return { [__ENV.RESULT || 'arrival-boundary.json']: JSON.stringify(data, null, 2) };
}
