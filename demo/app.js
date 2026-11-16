'use strict';
const $ = id => document.getElementById(id);
let timers = [], running = false;
const patterns = {
  sequential: ['Sequential checks', 'Both checks are required. Sequential execution completes one before starting the other.'],
  parallel: ['Parallel checks', 'Independent checks overlap. Both must finish before this illustration reaches its end.'],
  approval: ['Required approval', 'Checks finish first. A required review then pauses the flow; a person must choose its outcome.']
};
function mark(id, state) { $(id).classList.remove('active', 'done'); if (state) $(id).classList.add(state); }
function reset() {
  timers.forEach(clearTimeout); timers = []; running = false;
  ['start', 'kyc', 'risk', 'review', 'end'].forEach(id => mark(id, ''));
  ['pattern', 'delay', 'deadline', 'run'].forEach(id => $(id).disabled = false);
  $('approval').hidden = true; $('state').textContent = 'Ready';
  $('outcome').textContent = 'Choose a pattern, then play the walkthrough.';
  const key = $('pattern').value, info = patterns[key];
  $('flow-title').textContent = info[0]; $('explanation').textContent = info[1];
  $('checks').classList.toggle('parallel', key === 'parallel');
  $('between').hidden = key === 'parallel'; $('review').hidden = key !== 'approval'; $('review-arrow').hidden = key !== 'approval';
  const ms = Number($('delay').value) * (key === 'parallel' ? 1 : 2);
  $('duration').textContent = ms.toLocaleString('en-US') + ' ms' + (key === 'approval' ? ' + review wait' : '');
  $('deadline-result').textContent = key === 'approval' ? 'Review wait excluded; no deadline verdict' : ms <= Number($('deadline').value) ? 'Within selected deadline' : 'Exceeds selected deadline';
}
function later(fn, ms) { timers.push(setTimeout(fn, ms)); }
function finish(decision) {
  if (!running) return;
  $('approval').hidden = true; mark('review', 'done'); mark('end', 'done'); running = false;
  $('state').textContent = decision === 'rejected' ? 'Illustrated refusal' : 'Illustration complete';
  $('outcome').textContent = decision === 'rejected' ? 'Illustrated rejection: no payment or external action occurred.' : 'Illustrated approved path complete. No payment or external action occurred.';
  ['pattern', 'delay', 'deadline', 'run'].forEach(id => $(id).disabled = false);
}
$('run').addEventListener('click', () => {
  reset(); running = true; ['pattern', 'delay', 'deadline', 'run'].forEach(id => $(id).disabled = true);
  const key = $('pattern').value, delay = Number($('delay').value);
  $('state').textContent = 'Illustrating'; $('outcome').textContent = 'Showing synthetic check steps…';
  mark('start', 'done'); mark('kyc', 'active');
  if (key === 'parallel') mark('risk', 'active');
  later(() => { mark('kyc', 'done'); if (key !== 'parallel') mark('risk', 'active'); }, delay);
  later(() => {
    mark('risk', 'done');
    if (key === 'approval') {
      mark('review', 'active'); $('approval').hidden = false; $('state').textContent = 'Awaiting illustrated review';
      $('outcome').textContent = 'Required review remains in place. Choose approval or rejection to continue.';
    } else finish('approved');
  }, key === 'parallel' ? delay : delay * 2);
});
$('reset').addEventListener('click', reset);
['pattern', 'delay', 'deadline'].forEach(id => $(id).addEventListener('change', reset));
$('approve').addEventListener('click', () => finish('approved'));
$('reject').addEventListener('click', () => finish('rejected'));
reset();
