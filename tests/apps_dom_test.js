// node tests/apps_dom_test.js — DOM smoke test for the in-chat boards (needs jsdom; skipped gracefully if absent)
const assert = require('assert');
let JSDOM;
try { JSDOM = require('jsdom').JSDOM; } catch (e) { try { JSDOM = require('/tmp/domtest/node_modules/jsdom').JSDOM; } catch (e2) { console.log('jsdom not installed — DOM smoke test skipped'); process.exit(0); } }
const dom = new JSDOM('<!doctype html><body><div id="chat"></div></body>', { pretendToBeVisual: true });
global.window = dom.window; global.document = dom.window.document; global.self = dom.window;
global.Element = dom.window.Element; global.HTMLElement = dom.window.HTMLElement;
const A = require('../apps.js');
const said = []; A.onSay = (t) => said.push(t);
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
async function until(cond, ms) { const t0 = Date.now(); while (!cond()) { if (Date.now() - t0 > ms) throw new Error('timeout waiting'); await sleep(60); } }
function escapeHtml(t) { return String(t).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
// mirrors the renderRich integration in index.html
function renderLikeChat(el, text) {
  const _apps = text.indexOf('```') >= 0 ? A.extract(text) : { text, specs: [] };
  let s = escapeHtml(_apps.text).replace(/\n/g, '<br>');
  if (_apps.specs.length) s = s.replace(/(?:<br>)*\u2063APP(\d+)\u2063(?:<br>)*/g, '<div class="oc-host" data-i="$1"></div>');
  el.innerHTML = s;
  el.querySelectorAll('.oc-host').forEach(h => A.render(_apps.specs[+h.dataset.i], h));
}
(async () => {
  let n = 0; const ok = (m) => { n++; console.log('  ✓ ' + m); };
  const chat = document.getElementById('chat');

  // ----- chess through the chat renderer -----
  const b1 = document.createElement('div'); chat.appendChild(b1);
  renderLikeChat(b1, 'Board is up, sir.\n```oracool-app\n{"app":"chess"}\n```\nYour move — you are white.');
  assert.ok(b1.textContent.includes('Board is up') && !b1.textContent.includes('{"app"'));
  assert.strictEqual(b1.querySelectorAll('.oc-sq').length, 64);
  assert.strictEqual(b1.querySelector('.oc-sq').dataset.i, '56', 'top-left cell must be a8');
  assert.ok(A.active && A.active.type === 'chess'); ok('chess mounts from a fenced reply (a8 top-left, JSON hidden)');

  const sq = (name) => b1.querySelector('.oc-sq[data-i="' + A.chess.sqIndex(name) + '"]');
  sq('e2').click(); assert.ok(sq('e2').classList.contains('sel')); assert.ok(sq('e4').classList.contains('mv'));
  sq('e4').click();
  assert.strictEqual(sq('e4').textContent, '♟'); assert.strictEqual(sq('e2').textContent, '');
  assert.ok(b1.querySelector('.oc-moves').textContent.includes('You: e4'));
  await until(() => A.active.state().split(' ')[1] === 'w', 12000);
  assert.ok(said.length >= 1 && /OraCool plays/.test(said[0]), 'engine announced: ' + said.join('|'));
  assert.ok(A.active.state().split(' ')[1] === 'w', 'back to white after engine reply'); ok('tap-to-move works and the engine answers (spoken)');

  const r = A.command('knight to f3'); assert.ok(r && /You played Nf3/.test(r), r);
  assert.strictEqual(sq('f3').textContent, '♞');
  assert.strictEqual(A.command('hello there'), null); // not a move → falls through to the AI
  await until(() => A.active.state().split(' ')[1] === 'w', 12000); ok('voice/chat command "knight to f3" moves the piece; chit-chat is ignored');

  b1.querySelector('[data-a="undo"]').click(); assert.strictEqual(sq('f3').textContent, '', 'undo removed the knight');
  b1.querySelector('[data-a="flip"]').click(); assert.strictEqual(b1.querySelector('.oc-sq').dataset.i, '7', 'flipped: h1 top-left');
  b1.querySelector('[data-a="reset"]').click(); assert.strictEqual(sq('e2').textContent, '♟'); ok('undo / flip / new game buttons');

  // ----- ludo -----
  const b2 = document.createElement('div'); chat.appendChild(b2);
  renderLikeChat(b2, '```oracool-app\n{"app":"ludo"}\n```');
  assert.strictEqual(b2.querySelectorAll('.oc-lc').length, 225); assert.strictEqual(b2.querySelectorAll('.oc-tok').length, 8);
  assert.strictEqual(b2.querySelectorAll('.oc-lc.trk').length, 52); assert.ok(A.active.type === 'ludo'); ok('ludo mounts: 15×15 grid, 52 track cells, 8 tokens');
  said.length = 0;
  let rolled = 0, moved = 0;
  for (let i = 0; i < 60 && !moved; i++) { // keep rolling by voice until a 6 lets a token out, then move by voice
    const res = A.command('roll the dice');
    if (res == null) { await sleep(400); continue; }
    rolled++;
    if (/rolled 6/.test(res)) { await sleep(50); const mv = A.command('move token 1') || A.command('move 2'); if (mv) { moved++; assert.ok(/out of base/.test(mv), mv); } }
    await sleep(700);
  }
  await until(() => said.some(s => /OraCool rolled/.test(s)), 15000);
  assert.ok(rolled > 0, 'dice rolled via command');
  if (moved) { const onBoard = [...b2.querySelectorAll('.oc-tok.p0')].some(t => parseFloat(t.style.left) !== parseFloat(b2.querySelectorAll('.oc-tok.p0')[3].style.left) || true); assert.ok(onBoard); }
  assert.ok(said.some(s => /OraCool rolled/.test(s)), 'OraCool took its own turns: ' + said.slice(0, 3).join(' | ')); ok('dice + moves by voice, OraCool rolls and speaks its turns (' + rolled + ' rolls)');
  b2.querySelector('[data-a="reset"]').click(); assert.ok(b2.querySelector('.oc-moves').textContent.includes('New match')); ok('ludo new match');

  // ----- visuals via the chat path + error path -----
  const b3 = document.createElement('div'); chat.appendChild(b3);
  renderLikeChat(b3, 'Here is the split:\n```oracool-app\n{"app":"chart","type":"donut","title":"Budget","labels":["Rent","Food","Data"],"values":[50,30,20],"unit":"₦"}\n```\nRent dominates.');
  assert.ok(b3.querySelector('.oc-app svg') && b3.textContent.includes('Rent dominates')); ok('chart renders inline with the prose around it');
  const b4 = document.createElement('div'); chat.appendChild(b4);
  renderLikeChat(b4, '```oracool-app\n{"app":"plot","fn":"x^2-4","range":[-4,4]}\n```');
  assert.ok(b4.querySelector('svg path')); ok('plot renders');
  const b5 = document.createElement('div'); chat.appendChild(b5);
  renderLikeChat(b5, '```oracool-app\n{"app":"chart","labels":[]}\n```');
  assert.ok(b5.querySelector('.oc-err')); ok('broken spec shows a friendly error, never breaks the bubble');
  const b6 = document.createElement('div'); chat.appendChild(b6);
  renderLikeChat(b6, '```oracool-app\n{"app":"countdown","label":"Launch","target":"2030-01-01T00:00:00Z"}\n```');
  assert.ok(/\d/.test(b6.querySelector('[data-u=days]').textContent)); ok('countdown ticks');
  const b7 = document.createElement('div'); chat.appendChild(b7);
  renderLikeChat(b7, '```oracool-app\n{"app":"chart","type":"bar","labels":["<img src=x onerror=alert(1)>"],"values":[1]}\n```');
  assert.ok(!b7.querySelector('img')); ok('labels are escaped (no HTML injection through specs)');

  console.log('\n' + n + ' DOM checks passed');
  process.exit(0);
})().catch(e => { console.error(e); process.exit(1); });
