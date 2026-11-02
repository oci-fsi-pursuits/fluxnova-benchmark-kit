'use strict';
const $ = id => document.getElementById(id);
let state = null, token = '', selected = '', taskSignature = '', closed = false, busy = false;
function element(tag, text, className) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (className) e.className = className; return e; }
function error(message) { $('error').textContent = message || ''; $('error').hidden = !message; }
async function post(path, data = {}) {
  const response = await fetch('/api/' + path, {method:'POST', headers:{'Content-Type':'application/json','X-Lab-Token':token}, body:JSON.stringify(data)});
  const body = await response.json(); if (!response.ok) throw new Error(body.error || 'Action failed.'); return body;
}
async function action(fn) { if (busy) return; busy = true; error(''); render(); try { await fn(); await refresh(); } catch(e) { error(e.message); } finally { busy = false; if(!closed)render(); } }
function chosen() { return state?.models.find(m => m.id === selected); }
function select(id) {
  selected = id; const m = chosen(); $('models').value = id; $('model-title').textContent = m?.name || 'Choose a workflow';
  $('model-help').textContent = m?.simulate ? 'Both checks use the configured delay. Two worker slots allow independent branches to overlap.' : 'The engine follows this model. Complete active user or external tasks below; external tasks do not call their real services.';
  $('delay').disabled = !m?.simulate;
  $('expected').replaceChildren(new Option('Observe only', ''));
  for (const n of m?.nodes || []) if (n.type === 'endEvent') $('expected').add(new Option(n.name, n.id));
  if (m?.example !== 'approval' && m?.nodes.filter(n => n.type === 'endEvent').length === 1) $('expected').value = m.nodes.find(n => n.type === 'endEvent').id;
  taskSignature = ''; render();
}
function svg(tag, attrs, text) { const e = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const [k,v] of Object.entries(attrs)) e.setAttribute(k,v); if (text !== undefined) e.textContent = text; return e; }
function draw(model, instance) {
  if (!model) return;
  const levels = new Map(), starts = model.nodes.filter(n => n.type === 'startEvent'); starts.forEach(n => levels.set(n.id,0));
  // Breadth-first layout stays bounded for uploaded cycles. It is a graph preview, not a BPMN modeler.
  const queue = starts.map(n => n.id); while (queue.length) { const id = queue.shift(); for (const e of model.edges.filter(e => e.source === id)) if (!levels.has(e.target)) { levels.set(e.target,levels.get(id)+1); queue.push(e.target); } }
  const groups = []; for (const n of model.nodes) { const level = levels.get(n.id) ?? 0; (groups[level] ||= []).push(n); }
  const rows = Math.max(...groups.map(g => g.length)), width = Math.max(620,groups.length*150+30), height = Math.max(250,rows*105+70), positions = new Map();
  const root = svg('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':model.name + ' workflow graph'});
  const defs = svg('defs',{}), marker = svg('marker',{id:'arrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:6,markerHeight:6,orient:'auto-start-reverse'}); marker.append(svg('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#99b6c3'})); defs.append(marker); root.append(defs);
  groups.forEach((group,col) => group.forEach((n,row) => positions.set(n.id,{x:30+col*150,y:height/2+(row-(group.length-1)/2)*105})));
  for (const e of model.edges) { const a=positions.get(e.source),b=positions.get(e.target); root.append(svg('path',{d:`M${a.x+110},${a.y} L${b.x-7},${b.y}`,class:'edge','marker-end':'url(#arrow)'})); }
  for (const n of model.nodes) { const p=positions.get(n.id), acts=(instance?.activities||[]).filter(a=>a.activityId===n.id); const status=acts.some(a=>!a.endTime)?'active':acts.some(a=>a.endTime)?'complete':'';
    const g=svg('g',{}); if (n.type.endsWith('Gateway')) g.append(svg('path',{d:`M${p.x+55},${p.y-30} L${p.x+100},${p.y} L${p.x+55},${p.y+30} L${p.x+10},${p.y} Z`,class:'node '+status}));
    else g.append(svg('rect',{x:p.x,y:p.y-27,width:110,height:54,rx:n.type.endsWith('Event')?27:7,class:'node '+status}));
    const words=n.name.split(' '); const lines=[]; let line=''; for(const word of words){if((line+' '+word).length>16 && line){lines.push(line);line=word;}else line+=(line?' ':'')+word;}lines.push(line);
    lines.slice(0,3).forEach((t,i)=>g.append(svg('text',{x:p.x+55,y:p.y+(i-(Math.min(lines.length,3)-1)/2)*14+4,'text-anchor':'middle'},t)));
    g.append(svg('title',{},`${n.name} · ${n.type} · ${status||'waiting'}`)); root.append(g);
  } $('diagram').replaceChildren(root);
}
function renderTasks(instance, model) {
  const tasks = (instance?.status === 'running' && !model?.simulate) ? [...(instance.tasks||[]).map(t=>({...t,kind:'user'})),...(instance.external_tasks||[]).map(t=>({...t,kind:'external'}))] : [];
  const signature=JSON.stringify(tasks.map(t=>[t.id,t.kind])); if(signature===taskSignature)return;taskSignature=signature;$('tasks').replaceChildren();
  for(const t of tasks){const box=element('div',undefined,'task');box.append(element('h3',t.name||t.activityId||'Complete task'));
    if(model.example==='approval'){box.append(element('p','You are the reviewer. Choose the outcome to continue the actual workflow.','small'));for(const [text,value] of [['Approve',true],['Reject',false]]){const b=element('button',text,value?'':'reject');b.onclick=()=>action(()=>post('complete',{instance:instance.id,task:t.id,kind:t.kind,variables:{approved:value}}));box.append(b);}}
    else{box.append(element('p',t.kind==='external'?'Manually simulate the external service response. No real service is called.':'Set output variables and complete this task.','small'));const input=element('textarea');input.value='{}';input.setAttribute('aria-label','Task output variables');const b=element('button','Complete task');b.onclick=()=>action(()=>post('complete',{instance:instance.id,task:t.id,kind:t.kind,variables:JSON.parse(input.value)}));box.append(input,b);} $('tasks').append(box);
  }
}
function render(){if(!state)return;const active=state.runs.some(r=>r.status==='running'),m=chosen();$('run').disabled=!m||!state.writable||active||busy;$('stop').disabled=!active||busy;
  document.querySelectorAll('[data-example]').forEach(b=>b.disabled=!state.writable||busy);$('upload').disabled=!state.writable||busy;
  $('connection').textContent=`${state.mode} · ${state.writable?'ready to run':'read-only'}`;
  const matching=[...state.runs].reverse().find(r=>r.model===selected), instance=matching?.instances.at(-1);draw(m,instance);renderTasks(instance,m);
  $('results').replaceChildren();if(!state.runs.length){$('results').append(element('p','No runs yet. Run sequential and parallel with the same settings, then compare their individual durations.'));return;}
  for(const r of [...state.runs].reverse()){const card=element('div',undefined,'run-card');card.append(element('h3',`${r.name} · ${r.status}`));const work=r.simulated_checks?`${r.parameters.delay_ms} ms simulated check delay`:'Manual tasks; delay setting does not apply';card.append(element('p',`${r.parameters.count} requested · ${work} · ${r.parameters.poll_ms} ms observer interval · ${r.parameters.deadline_s} s deadline`,'run-meta'));
    if(r.error)card.append(element('p',r.error));const table=element('table'),head=element('tr');['Instance','State','Observed time','Deadline','End event','Expected end'].forEach(t=>head.append(element('th',t)));table.append(head);
    for(const i of r.instances){const row=element('tr');[i.number,i.status,(i.elapsed_ms/1000).toFixed(3)+' s',i.deadline_met===null?'—':i.deadline_met?'Met':'Missed',i.end_events.join(', ')||'—',i.expected_end_matches===null?'Not asserted':i.expected_end_matches?'Matched':'Mismatch'].forEach(t=>row.append(element('td',t)));table.append(row);}card.append(table);$('results').append(card);
  }
}
async function refresh(){if(closed)return;const res=await fetch('/api/state');if(!res.ok)throw new Error('Lab connection unavailable.');state=await res.json();token=state.token;const value=selected;$('models').replaceChildren(new Option('Choose a loaded workflow',''));for(const m of state.models)$('models').add(new Option(m.name,m.id));$('models').value=value;render();}
document.querySelectorAll('[data-example]').forEach(b=>b.onclick=()=>action(async()=>{const m=await post('example',{name:b.dataset.example});await refresh();select(m.id);}));
$('models').onchange=()=>select($('models').value);
$('upload').onchange=()=>action(async()=>{const file=$('upload').files[0];if(!file)return;if(file.size>256000)throw new Error('Upload a BPMN file smaller than 256 KB.');const m=await post('upload',{xml:await file.text()});await refresh();select(m.id);$('upload').value='';});
$('run-form').onsubmit=e=>{e.preventDefault();action(()=>post('run',{model:selected,count:Number($('count').value),delay_ms:Number($('delay').value),poll_ms:Number($('poll').value),deadline_s:Number($('deadline').value),expected_end:$('expected').value,variables:JSON.parse($('variables').value)}));};
$('stop').onclick=()=>action(()=>post('stop'));
$('export').onclick=async()=>{const data=await fetch('/api/export').then(r=>r.json());const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=element('a');a.href=url;a.download='fluxnova-lab-session.json';a.click();URL.revokeObjectURL(url);};
$('shutdown').onclick=()=>action(async()=>{await post('shutdown');closed=true;$('connection').textContent='Lab closed';$('run').disabled=true;error('Lab closed. Your saved JSON remains in the local output folder.');});
async function poll(){try{await refresh();}catch(e){if(!closed)error(e.message);}if(!closed)setTimeout(poll,800);}poll();
