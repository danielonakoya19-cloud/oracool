// node tests/client41_dom_test.js — jsdom smoke test for patch41 client pieces (capture editor parsing, code runner, ask hook)
const assert = require('assert'); const fs = require('fs'); const path = require('path');
let JSDOM;
try { JSDOM = require('jsdom').JSDOM; } catch (e) { try { JSDOM = require('/tmp/domtest/node_modules/jsdom').JSDOM; } catch (e2) { console.log('jsdom not installed — client41 DOM test skipped'); process.exit(0); } }
const ROOT = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
const dom = new JSDOM('<!doctype html><body><div id="chat"></div></body>', { pretendToBeVisual: true, runScripts: 'outside-only' });
const w = dom.window;
// stubs for the chat globals the block touches
w.eval(`var sent=[], ais=[], notes=[]; function send(t){ sent.push(t); } function addAI(t){ ais.push(t); } function addSystemNote(h){ notes.push(h); const d=document.createElement('div'); return d; }
  function escapeHtml(s){ return String(s==null?'':s).replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function scrollChat(){} var chat=document.getElementById('chat'); async function post(){ return {text:'a calm face, even light'}; }`);
const start = html.indexOf('/* patch41: camera captures are remembered');
const end = html.indexOf('/* patch40: "record a video / clip of me"');
assert.ok(start > 0 && end > start, 'patch41 block present');
w.eval(html.slice(start, end).replace('let lastCapture=null;', 'var lastCapture=null;').replace('let _runSeq=0; const _runners={};', 'var _runSeq=0; var _runners={};').replace('let _pyodide=null, _pyLoading=null;', 'var _pyodide=null, _pyLoading=null;')); // indirect eval scopes let/const locally; the page's top-level lets are global
let n = 0; const ok = (m) => { n++; console.log('  ✓ ' + m); };

// ---- edit command parsing
let ops = w.parseEditOps('make it black and white and add caption "Hello Lagos"');
assert.ok(/grayscale/.test(ops.filter) && ops.caption === 'Hello Lagos', JSON.stringify(ops)); ok('B&W + caption parsed');
ops = w.parseEditOps('trim it to 5 seconds'); assert.strictEqual(JSON.stringify(ops.trim), '[0,5]'); ok('trim parsed');
ops = w.parseEditOps('slow it down and mute it'); assert.strictEqual(ops.speed, 0.5); assert.strictEqual(ops.mute, true); ok('slow-mo + mute parsed');
ops = w.parseEditOps('brighten the clip please'); assert.ok(/brightness\(1\.28\)/.test(ops.filter)); ok('brighten parsed');
ops = w.parseEditOps('edit it'); assert.ok(ops && ops.labels[0] === 'enhance'); ok('generic edit → enhance');
assert.strictEqual(w.parseEditOps('how do i look in it'), null); ok('question is not an edit');

// ---- capture context in the system prompt
assert.strictEqual(w.captureContext(), '');
w.eval(`lastCapture={kind:'video', at:Date.now(), seconds:10, desc:'Even lighting, centred framing.', edits:['black & white']};`);
const ctx = w.captureContext();
assert.ok(/10-second video clip/.test(ctx) && /Even lighting/.test(ctx) && /black & white/.test(ctx) && /Never say there is no record/.test(ctx)); ok('capture context feeds the prompt');

// ---- capture report goes through send() with the vision text in the hidden content
w.captureReport('video');
assert.strictEqual(w.sent.length, 1); assert.ok(/how do I look/.test(w.sent[0]));
assert.ok(w.__sendOverride && /Even lighting/.test(w.__sendOverride.content)); ok('capture report asks OraCool with the analysis attached');

// ---- runnable code detection
const mk = (lang, code) => { const pre = w.document.createElement('pre'); pre.className = 'md-pre'; if (lang) pre.dataset.lang = lang; const c = w.document.createElement('code'); c.textContent = code; pre.appendChild(c); return pre; };
assert.strictEqual(w.runnableKind(mk('python', 'print(1)')), 'py');
assert.strictEqual(w.runnableKind(mk('js', 'console.log(1)')), 'js');
assert.strictEqual(w.runnableKind(mk('html', '<h1>x</h1>')), 'html');
assert.strictEqual(w.runnableKind(mk('', '<!doctype html><html><body>hi</body></html>')), 'html');
assert.strictEqual(w.runnableKind(mk('', 'import math\nprint(math.pi)')), 'py');
assert.strictEqual(w.runnableKind(mk('bash', 'ls -la')), null); ok('runnable kinds detected (py/js/html, bash excluded)');

// ---- HTML preview box
const pre = mk('html', '<h1>Hello</h1>'); w.chat.appendChild(pre);
w.codeRun(pre, 'html');
const box = pre.nextElementSibling; assert.ok(box && box.classList.contains('run-box'));
const fr = box.querySelector('iframe'); assert.ok(fr && fr.getAttribute('sandbox').indexOf('allow-same-origin') < 0 && /Hello/.test(fr.getAttribute('srcdoc'))); ok('HTML preview renders in a sandboxed iframe');

// ---- JS runner builds a sandboxed iframe with console capture
const pj = mk('js', 'console.log("hi"); 2+2'); w.chat.appendChild(pj);
w.codeRun(pj, 'js');
const fj = w.document.body.querySelector('iframe[sandbox="allow-scripts"]'); assert.ok(fj && /postMessage/.test(fj.getAttribute('srcdoc')) && /console\.log/.test(fj.getAttribute('srcdoc'))); ok('JS runner iframe wired');
const jb = pj.nextElementSibling; assert.ok(jb && /node script\.js/.test(jb.textContent)); ok('terminal header like Arena');

// ---- ask/say hooks attach after apps.js
const hook = html.match(/<script src="\/apps\.js\?v=\d+"><\/script>\s*<script>([\s\S]*?)<\/script>/);
assert.ok(hook, 'post-apps hook script exists');
w.eval(fs.readFileSync(path.join(ROOT, 'apps.js'), 'utf8')); // no `module` in the window → attaches root.OraApps
w.eval(`function speak(){} var history=[];`);
w.eval(hook[1]);
assert.ok(typeof w.OraApps.onAsk === 'function' && typeof w.OraApps.onSay === 'function'); ok('OraApps hooks attached after apps.js');
const host = w.document.createElement('div'); w.chat.appendChild(host);
w.OraApps.render('{"app":"ask","question":"What should the video show?","options":["A lion","A city"]}', host);
host.querySelector('.oc-ask .opts button').click();
assert.strictEqual(w.sent[w.sent.length - 1], 'A lion'); ok('tapping an ask option sends it to OraCool');
console.log('client41 DOM OK (' + n + ' checks)'); process.exit(0);
