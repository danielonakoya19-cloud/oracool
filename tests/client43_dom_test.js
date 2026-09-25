// node tests/client43_dom_test.js — composer mode picker (Auto / Fast / Build / Expert)
const assert = require('assert'); const fs = require('fs'); const path = require('path');
let JSDOM;
try { JSDOM = require('jsdom').JSDOM; } catch (e) { try { JSDOM = require('/tmp/domtest/node_modules/jsdom').JSDOM; } catch (e2) { console.log('jsdom not installed — client43 DOM test skipped'); process.exit(0); } }
const ROOT = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
const m1 = html.indexOf('<div class="gpt-input big">'); const m2 = html.indexOf('</div>\n      </div>\n    </div>\n      <div class="panels-stage"', m1);
assert.ok(m1 > 0 && m2 > m1, 'composer markup located');
const composer = html.slice(m1, m2 + '</div>'.length);
const dom = new JSDOM('<!doctype html><body>' + composer + '</body>', { pretendToBeVisual: true, runScripts: 'outside-only' });
const w = dom.window;
w.eval(`var settings={chatMode:'auto'}; var saved=null; function saveSettings(){ saved=JSON.parse(JSON.stringify(settings)); } var tier='free'; function myTier(){ return tier; } var opened=null; function showView(v){ opened=v; }`);
const c1 = html.indexOf('// patch43: composer modes'); const c2 = html.indexOf('function wantsEdit(q){');
assert.ok(c1 > 0 && c2 > c1); w.eval(html.slice(c1, c2));
let n = 0; const ok = (m) => { n++; console.log('  ✓ ' + m); };
const btn = w.document.getElementById('modeBtn'), menu = w.document.getElementById('modeMenu'), inp = w.document.getElementById('input');
assert.strictEqual(btn.querySelector('.mi-l').textContent, 'Auto'); assert.ok(menu.hidden); ok('defaults to Auto with the menu closed');
btn.click(); assert.ok(!menu.hidden); assert.strictEqual(btn.getAttribute('aria-expanded'), 'true'); assert.ok(menu.querySelector('.mi[data-mode="auto"]').classList.contains('on')); ok('opens the menu; Auto is ticked');
assert.strictEqual(menu.querySelectorAll('.mi').length, 4); assert.deepStrictEqual([...menu.querySelectorAll('.mi')].map(x => x.dataset.mode), ['fast', 'build', 'auto', 'expert']); ok('Fast / Build / Auto / Expert rows');
menu.querySelector('.mi[data-mode="build"]').click();
assert.ok(menu.hidden); assert.strictEqual(btn.querySelector('.mi-l').textContent, 'Build'); assert.ok(btn.classList.contains('build')); assert.strictEqual(w.eval('settings.chatMode'), 'build'); assert.strictEqual(w.eval('saved.chatMode'), 'build'); ok('picking Build updates the pill, persists the setting and closes the menu');
assert.ok(/website or app to build/.test(inp.placeholder)); ok('Build mode changes the composer placeholder');
assert.strictEqual(w.chatMode(), 'build');
menu.querySelector('.mi[data-mode="expert"]').click(); assert.strictEqual(w.chatMode(), 'expert'); assert.ok(btn.classList.contains('expert')); ok('Expert mode selectable');
assert.ok(!menu.querySelector('.mi-up').classList.contains('hide')); w.eval("tier='pro'"); w.renderModeBtn(); assert.ok(menu.querySelector('.mi-up').classList.contains('hide')); ok('upgrade row shows for free tier, hides for pro');
w.eval("tier='free'"); w.renderModeBtn(); menu.querySelector('.mi-upbtn').click(); assert.strictEqual(w.eval('opened'), 'plans'); ok('Upgrade opens the Plans view');
btn.click(); w.document.body.click(); assert.ok(menu.hidden); ok('clicking outside closes the menu');
w.setMode('nonsense'); assert.strictEqual(w.chatMode(), 'auto'); ok('unknown mode falls back to Auto');
console.log('client43 DOM OK (' + n + ' checks)'); process.exit(0);
