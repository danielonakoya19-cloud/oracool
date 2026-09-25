// node tests/client42_dom_test.js — the live build feed (Arena-style activity rows + "Working for Ns")
const assert = require('assert'); const fs = require('fs'); const path = require('path');
let JSDOM;
try { JSDOM = require('jsdom').JSDOM; } catch (e) { try { JSDOM = require('/tmp/domtest/node_modules/jsdom').JSDOM; } catch (e2) { console.log('jsdom not installed — client42 DOM test skipped'); process.exit(0); } }
const ROOT = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
const dom = new JSDOM('<!doctype html><body><div id="chat"></div></body>', { pretendToBeVisual: true, runScripts: 'outside-only' });
const w = dom.window;
w.eval(`var chat=document.getElementById('chat'); function scrollChat(){} function escapeHtml(s){ return String(s==null?'':s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  var progress={ok:true,active:true,done:false,name:'Sweet Crumbs',steps:[]}; async function post(u,b){ return JSON.parse(JSON.stringify(progress)); }`);
const start = html.indexOf('function wantsBuild(q){'); const end = html.indexOf('function buildCard(x){');
assert.ok(start > 0 && end > start); w.eval(html.slice(start, end));
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  let n = 0; const ok = (m) => { n++; console.log('  ✓ ' + m); };
  assert.ok(w.wantsBuild('build me a website for my gym') && !w.wantsBuild('how do I build a website')); ok('build intent');
  assert.ok(w.wantsEdit('change the hero background to navy') && w.wantsEdit('add a pricing section to my site') && !w.wantsEdit('what is the weather')); ok('edit intent');
  const anchor = w.document.createElement('div'); w.chat.appendChild(anchor);
  const card = w.stepsCard(anchor);
  const el = w.chat.querySelector('.steps'); assert.ok(el && el.style.display === 'none'); ok('feed hidden until the first step arrives');
  w.eval(`progress.steps=[{title:'Reading the brief',detail:'a bakery in Lagos',kind:'skill',ms:4,running:false},{title:'Collecting licensed photos',detail:'Pexels',kind:'explore',ms:null,running:true}];`);
  await sleep(900);
  assert.ok(el.style.display !== 'none'); assert.strictEqual(el.querySelectorAll('.st').length, 2);
  assert.ok(/Building Sweet Crumbs/.test(el.querySelector('.sh b').textContent)); ok('header names the build');
  assert.strictEqual(el.querySelector('.st .ki').textContent, '⊞'); assert.ok(el.querySelector('.st.run')); ok('kind icons + running row');
  assert.ok(/Working for/.test(el.querySelector('.live .lt').textContent)); ok('"Working for Ns" live row');
  w.eval(`progress.steps.push({title:'Writing the hero section',detail:'3.2 KB written',kind:'write',ms:null,running:true}); progress.steps[1].running=false; progress.steps[1].ms=1300;`);
  await sleep(1000);
  assert.strictEqual(el.querySelectorAll('.st').length, 3); assert.ok(/3\.2 KB written/.test(el.textContent)); ok('new section rows appear with live size');
  w.eval(`progress.done=true; progress.active=false; progress.ok_build=true; progress.total_ms=112000; progress.steps[2].running=false; progress.steps[2].ms=90000;`);
  await sleep(1000);
  assert.ok(el.classList.contains('done') && !el.classList.contains('open')); assert.ok(/Worked for 1m 52s · 3 steps · Sweet Crumbs/.test(el.querySelector('.sh b').textContent)); assert.ok(!el.querySelector('.live')); ok('collapses to "Worked for 1m 52s" when done');
  // a previous build's finished record must not be mistaken for the new build
  const anchor2 = w.document.createElement('div'); w.chat.appendChild(anchor2);
  w.eval(`progress={ok:true,active:false,done:true,ok_build:true,total_ms:5000,age_ms:90000,name:'Old',steps:[{title:'Preview ready',detail:'',kind:'ready',ms:1,running:false}]};`);
  const c3 = w.stepsCard(anchor2); await sleep(900);
  const el3 = w.chat.querySelectorAll('.steps')[1]; assert.ok(el3 && el3.style.display === 'none'); ok('stale finished record is ignored (keeps waiting for the new build)');
  w.eval(`progress={ok:true,active:true,done:false,age_ms:400,name:'New',steps:[{title:'Reading the brief',detail:'',kind:'skill',ms:null,running:true}]};`); await sleep(1000);
  assert.ok(el3.style.display !== 'none' && /Building New/.test(el3.querySelector('.sh b').textContent)); c3.stop(); ok('picks up the new build when it starts');
  // parent mode (Websites panel)
  const host = w.document.createElement('div'); w.document.body.appendChild(host);
  w.eval(`progress={ok:true,active:true,done:false,name:'Gym',steps:[{title:'Reading the brief',detail:'',kind:'skill',ms:2,running:false}]};`);
  const f2 = w.stepsCard(null, { parent: host }); await sleep(900);
  assert.ok(host.querySelector('.steps .st')); f2.stop(); ok('panel mode renders inside the given container');
  // patch45: push mode — frames arrive inside the chat stream, no polling at all
  w.eval(`var postCalls=0; post=async function(u,b){ postCalls++; return {ok:true,steps:[]}; };`);
  const anchor3 = w.document.createElement('div'); w.chat.appendChild(anchor3);
  const c4 = w.stepsCard(anchor3, { push: true });
  const el4 = w.chat.querySelectorAll('.steps')[w.chat.querySelectorAll('.steps').length - 1];
  c4.push({ ok: true, done: false, name: 'Fashion Designer', steps: [{ title: 'Reading the brief', detail: 'create a website for a fashion designer', kind: 'skill', ms: 3, running: false }, { title: 'Building Fashion Designer', detail: 'thinking…', kind: 'build', ms: null, running: true }] });
  assert.ok(el4.style.display !== 'none' && el4.querySelectorAll('.st').length === 2 && /Building Fashion Designer/.test(el4.querySelector('.sh b').textContent)); ok('push mode paints frames immediately');
  c4.push({ ok: true, done: true, ok_build: true, total_ms: 26000, name: 'Fashion Designer', steps: [{ title: 'Reading the brief', detail: '', kind: 'skill', ms: 3, running: false }, { title: 'Building Fashion Designer', detail: '', kind: 'build', ms: 20000, running: false }, { title: 'Preview ready', detail: '/builds/fashion-designer/', kind: 'ready', ms: 1, running: false }] });
  assert.ok(el4.classList.contains('done') && /Worked for 26s · 3 steps · Fashion Designer/.test(el4.querySelector('.sh b').textContent)); ok('push mode finishes with the collapsed summary');
  await sleep(1500); assert.strictEqual(w.eval('postCalls'), 0); c4.finish(); await sleep(200); assert.strictEqual(w.eval('postCalls'), 0); ok('push mode never polls the progress endpoint');
  console.log('client42 DOM OK (' + n + ' checks)'); process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
