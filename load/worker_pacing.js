// W01: open-arrival payment bursts. A late outcome remains a measured outcome.
import http from 'k6/http';
import { sleep } from 'k6';
import exec from 'k6/execution';
import encoding from 'k6/encoding';
import { Counter } from 'k6/metrics';

const base = (__ENV.W01_ENGINE_URL || '').replace(/\/$/,'');
const support = (__ENV.W01_SUPPORT_URL || '').replace(/\/$/,'');
const runId = __ENV.W01_RUN_ID;
const arm = __ENV.W01_ARM;
const warmupBursts = Number(__ENV.W01_WARMUP_BURSTS || 2);
const measuredBursts = Number(__ENV.W01_MEASURED_BURSTS || 6);
const burstRate = 20, burstSeconds = 1, period = 10, vus = 32, deadline = 2, drain = 20;
const warmup = warmupBursts * period, duration = measuredBursts * period;
if (!base || !support || !runId || !['success_delay_100ms','idle_only'].includes(arm)
    || !__ENV.W01_USERNAME || !__ENV.W01_PASSWORD || !__ENV.W01_SUPPORT_TOKEN
    || !Number.isInteger(warmupBursts) || warmupBursts < 1 || warmupBursts > 12
    || !Number.isInteger(measuredBursts) || measuredBursts < 1 || measuredBursts > 60) {
  throw Error('Invalid W01 settings');
}
const headers = {'Content-Type':'application/json',
  Authorization:'Basic '+encoding.b64encode(__ENV.W01_USERNAME+':'+__ENV.W01_PASSWORD)};
const supportHeaders = {Authorization:'Bearer '+__ENV.W01_SUPPORT_TOKEN};
const scenarios = {}, thresholds = {};
const scheduled = new Counter('w01_scheduled');
const scenarioPlan = {};
for (const [phase, count, offset] of [['warmup',warmupBursts,0],['measurement',measuredBursts,warmup]]) {
  for (let burst=0;burst<count;burst++) {
    const name=phase+'_'+burst;
    scenarios[name]={executor:'constant-arrival-rate', rate:burstRate,timeUnit:'1s',
      duration:burstSeconds+'s',startTime:(offset+burst*period)+'s',
      preAllocatedVUs:vus,maxVUs:vus,gracefulStop:drain+'s',exec:'payment'};
    scenarioPlan[name]={phase,burst,offset};
    thresholds['dropped_iterations{scenario:'+name+'}']=['count>=0'];
  }
}
// Keep the quiet part of the final cycle in the declared measurement interval.
scenarios.window={executor:'shared-iterations',vus:1,iterations:1,
  maxDuration:(warmup+duration+1)+'s',exec:'window'};
export const options={scenarios,thresholds,summaryTrendStats:['count','avg','p(95)','p(99)','max']};
export function window(){sleep(warmup+duration);}
function now(){return exec.instance.currentTestRunDuration/1000-warmup;}
function parsed(r){try{return r.json();}catch(_){return null;}}
function request(method,path,body,backend=false){
  const r=http.request(method,(backend?support:base)+path,body===undefined?null:JSON.stringify(body),
    {headers:backend?supportHeaders:headers,timeout:'5s',
      tags:{name:path.split('?')[0].replace(/[a-f0-9-]{30,}/g,':id'),phase:scenarioPlan[exec.scenario.name].phase}});
  console.log('W01_HTTP '+JSON.stringify({elapsed_s:now(),status:r.status}));
  return r;
}
function emit(event,key,planned,status,details={},observed){
  const elapsed=observed===undefined?now():observed;
  console.log('W01_EVENT '+JSON.stringify({event,run_id:runId,arm,
    phase:scenarioPlan[exec.scenario.name].phase,logical_request_id:key,
    scheduled_elapsed_s:planned,elapsed_s:elapsed,status,details,
    ...(event==='completed'?{latency_s:elapsed-planned}:{})}));
}
export function payment(){
  const plan=scenarioPlan[exec.scenario.name], index=exec.scenario.iterationInTest;
  // Protect the half-open burst cohort on timer platforms dispatching its boundary.
  if(index>=burstRate*burstSeconds)return;
  const key=runId+':'+plan.phase+':'+plan.burst+':'+index;
  const planned=plan.offset+plan.burst*period+index/burstRate-warmup;
  scheduled.add(1,{phase:plan.phase});
  emit('started',key,planned,'unknown');
  const variables={
    scenario:{value:'settlement',type:'String'},logical_request_id:{value:key,type:'String'},
    authorized:{value:true,type:'Boolean'},approved:{value:false,type:'Boolean'},
    sanctions_match:{value:false,type:'Boolean'},high_risk:{value:false,type:'Boolean'},
    amount_minor:{value:125,type:'Integer'},limit_minor:{value:1000,type:'Integer'},
    notional_minor:{value:100,type:'Integer'},stale_market:{value:false,type:'Boolean'},
    payload:{value:'w01-synthetic-payment',type:'String'},fault:{value:'none',type:'String'},
    delay_ms:{value:0,type:'Integer'}};
  const response=request('POST','/process-definition/key/bench_settlement/start',{businessKey:key,variables});
  const started=parsed(response);
  if(response.status!==200||!started?.id){emit('outcome',key,planned,'unknown',{reason:'start_not_confirmed',http_status:response.status});return;}
  emit('accepted',key,planned,'unknown',{process_id:started.id});
  const until=planned+drain;
  while(now()<until){
    const historyResponse=request('GET','/history/process-instance/'+started.id);
    const observed=now(), history=parsed(historyResponse);
    if(historyResponse.status===200&&history?.endTime){
      const vars=parsed(request('GET','/history/variable-instance?processInstanceId='+started.id+'&deserializeValues=false'));
      const activities=parsed(request('GET','/history/activity-instance?processInstanceId='+started.id));
      const state=parsed(request('GET','/state/'+encodeURIComponent(key),undefined,true));
      const correct=history.state==='COMPLETED'&&history.businessKey===key&&Array.isArray(vars)&&
        vars.some(v=>v.name==='workerResult'&&v.value==='settled')&&state?.result?.outcome==='settled'&&state.effect_count===1;
      emit('completed',key,planned,correct?'correct':'incorrect',
        {process_id:started.id,history,variables:vars,activities,backend:state,
          verification_elapsed_s:now()-observed},observed);
      return;
    }
    sleep(Math.min(.1,Math.max(0,until-now())));
  }
  emit('outcome',key,planned,'unknown',{reason:'completion_not_observed',process_id:started.id});
}
export function handleSummary(data){
  const counts={};
  for(const [name,plan] of Object.entries(scenarioPlan)){
    counts[name]={phase:plan.phase,burst:plan.burst,offered:burstRate*burstSeconds,
      dropped:data.metrics['dropped_iterations{scenario:'+name+'}']?.values?.count||0};
  }
  return {[__ENV.W01_OUTPUT_DIR+'/k6-summary.json']:JSON.stringify({metrics:data.metrics,w01:{
    run_id:runId,arm,warmup_bursts:warmupBursts,measurement_bursts:measuredBursts,
    warmup_s:warmup,duration_s:duration,burst_rate:burstRate,burst_duration_s:burstSeconds,
    period_s:period,fixed_vus_per_burst:vus,deadline_s:deadline,poll_interval_ms:100,
    drain_s:drain,scenarios:counts}})};
}
