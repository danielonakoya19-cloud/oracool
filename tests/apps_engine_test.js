// node tests/apps_engine_test.js — pure-engine checks for apps.js (chess, ludo, visuals)
const assert = require('assert');
const A = require('../apps.js');
const C = A.chess, L = A.ludo, V = A.viz;
let n = 0;
function t(name, fn) { fn(); n++; console.log('  ✓ ' + name); }

// ---------- chess: move generation (perft) ----------
function perft(g, d) {
  if (d === 0) return 1;
  const ms = C.legalMoves(g); let s = 0;
  for (const m of ms) { C.applyMove(g, m); s += perft(g, d - 1); C.undoMove(g); }
  return s;
}
t('start position has 20 legal moves, perft(2)=400, perft(3)=8902', () => {
  const g = C.newChess();
  assert.strictEqual(C.legalMoves(g).length, 20);
  assert.strictEqual(perft(g, 2), 400);
  assert.strictEqual(perft(g, 3), 8902);
});
t('kiwipete perft(2)=2039 (castling, en passant, promotions, checks)', () => {
  const g = C.fromFen('r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq -');
  assert.strictEqual(C.legalMoves(g).length, 48);
  assert.strictEqual(perft(g, 2), 2039);
});
t('position 3 perft(3)=2812 (en passant pins)', () => {
  const g = C.fromFen('8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - -');
  assert.strictEqual(C.legalMoves(g).length, 14);
  assert.strictEqual(perft(g, 3), 2812);
});
t("fool's mate is detected as 0-1", () => {
  const g = C.newChess();
  for (const s of ['f3', 'e5', 'g4', 'Qh4']) { const m = C.parseMove(g, s); assert.ok(m, 'parse ' + s); C.applyMove(g, m); }
  assert.strictEqual(g.over, 'mate'); assert.strictEqual(g.result, '0-1');
});
t('stalemate detected', () => {
  const g = C.fromFen('7k/5Q2/6K1/8/8/8/8/8 b - -');
  assert.strictEqual(C.legalMoves(g).length, 0);
  const g2 = C.fromFen('7k/8/5QK1/8/8/8/8/8 w - -');
  C.applyMove(g2, C.parseMove(g2, 'Qf7'));
  assert.strictEqual(g2.over, 'stalemate');
});
t('castling both sides + rook rights update', () => {
  const g = C.fromFen('r3k2r/8/8/8/8/8/8/R3K2R w KQkq -');
  const ks = C.parseMove(g, 'castle kingside'), qs = C.parseMove(g, 'O-O-O');
  assert.ok(ks && ks.castle === 'K'); assert.ok(qs && qs.castle === 'Q');
  C.applyMove(g, ks);
  assert.strictEqual(g.sq[C.sqIndex('g1')], 6); assert.strictEqual(g.sq[C.sqIndex('f1')], 4);
  assert.strictEqual(g.cast.wk, false); assert.strictEqual(g.cast.wq, false);
  assert.ok(C.toFen(g).startsWith('r3k2r/8/8/8/8/8/8/R4RK1 b kq'));
});
t('no castling through check', () => {
  const g = C.fromFen('4k3/8/8/8/8/8/5r2/R3K2R w KQ -');
  assert.strictEqual(C.parseMove(g, 'O-O'), null);
  assert.ok(C.parseMove(g, 'O-O-O'));
});
t('en passant capture removes the pawn', () => {
  const g = C.fromFen('4k3/8/8/3pP3/8/8/8/4K3 w - d6');
  const m = C.parseMove(g, 'exd6'); assert.ok(m && m.ep);
  C.applyMove(g, m);
  assert.strictEqual(g.sq[C.sqIndex('d5')], 0); assert.strictEqual(g.sq[C.sqIndex('d6')], 1);
});
t('promotion auto-queens', () => {
  const g = C.fromFen('8/P6k/8/8/8/8/8/4K3 w - -');
  C.applyMove(g, C.parseMove(g, 'a8'));
  assert.strictEqual(g.sq[C.sqIndex('a8')], 5);
});
t('parseMove understands spoken forms', () => {
  const g = C.newChess();
  assert.strictEqual(C.sqName(C.parseMove(g, 'knight to f3').t), 'f3');
  assert.strictEqual(C.sqName(C.parseMove(g, 'e2 to e4').t), 'e4');
  assert.strictEqual(C.sqName(C.parseMove(g, 'e2e4').t), 'e4');
  assert.strictEqual(C.sqName(C.parseMove(g, 'Move pawn to d4.').t), 'd4');
  assert.strictEqual(C.parseMove(g, 'hello how are you'), null);
  assert.strictEqual(C.parseMove(g, 'be careful'), null);
  assert.strictEqual(C.parseMove(g, 'e5'), null); // illegal for white
});
t('SAN disambiguation (two knights)', () => {
  const g = C.fromFen('4k3/8/8/8/8/8/8/1N1NK3 w - -');
  const m = C.parseMove(g, 'Nbc3'); assert.ok(m && C.sqName(m.f) === 'b1');
  assert.strictEqual(C.moveToSan(g, m), 'Nbc3');
  assert.strictEqual(C.moveToSan(g, C.parseMove(g, 'knight b1 to c3')), 'Nbc3');
  assert.strictEqual(C.moveToSan(g, C.parseMove(g, 'Na3')), 'Na3'); // unambiguous stays short
});
t('engine grabs a hanging queen and never returns an illegal move', () => {
  const g = C.fromFen('4k3/8/8/3q4/4P3/8/8/4K3 w - -');
  const m = C.bestMove(g, 2);
  assert.strictEqual(C.sqName(m.t), 'd5'); assert.strictEqual(C.sqName(m.f), 'e4');
  const g2 = C.newChess();
  for (let i = 0; i < 12 && !g2.over; i++) {
    const legal = C.legalMoves(g2), mv = C.bestMove(g2, 2);
    assert.ok(legal.some(l => l.f === mv.f && l.t === mv.t), 'engine move must be legal');
    C.applyMove(g2, mv);
  }
});
t('engine finds mate in one', () => {
  const g = C.fromFen('6k1/5ppp/8/8/8/8/8/R5K1 w - -');
  const m = C.bestMove(g, 2); C.applyMove(g, m);
  assert.strictEqual(g.over, 'mate'); assert.strictEqual(g.result, '1-0');
});
t('engine plays black at depth 3 within the time cap', () => {
  const g = C.fromFen('rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq -');
  const t0 = Date.now(); const m = C.bestMove(g, 3);
  assert.ok(m); assert.ok(Date.now() - t0 < 2500, 'took ' + (Date.now() - t0) + 'ms');
});
t('undo restores position exactly', () => {
  const g = C.newChess(), f0 = C.toFen(g);
  C.applyMove(g, C.parseMove(g, 'e4')); C.applyMove(g, C.parseMove(g, 'c5'));
  C.undoMove(g); C.undoMove(g);
  assert.strictEqual(C.toFen(g), f0);
});

// ---------- ludo ----------
t('ring is 52 unique cells, none inside the centre 3x3', () => {
  assert.strictEqual(L.LT, 52);
  const seen = new Set(L.TRACK.map(c => c.join(',')));
  assert.strictEqual(seen.size, 52);
  L.TRACK.forEach(([x, y]) => assert.ok(!(x >= 6 && x <= 8 && y >= 6 && y <= 8), 'centre cell on ring ' + x + ',' + y));
});
t('both players walk 51 ring steps, 5 home cells, finish at 57 — last ring cell is next to the home column', () => {
  assert.strictEqual(L.RING_STEPS, 51); assert.strictEqual(L.FINISH_STEP, 57);
  assert.deepStrictEqual(L.ludoCell(0, 0), [0, 6]); assert.deepStrictEqual(L.ludoCell(0, 51), [0, 7]);
  assert.deepStrictEqual(L.ludoCell(0, 52), [1, 7]); assert.deepStrictEqual(L.ludoCell(0, 56), [5, 7]); assert.deepStrictEqual(L.ludoCell(0, 57), [7, 7]);
  assert.deepStrictEqual(L.ludoCell(1, 0), [14, 8]); assert.deepStrictEqual(L.ludoCell(1, 51), [14, 7]);
  assert.deepStrictEqual(L.ludoCell(1, 52), [13, 7]); assert.deepStrictEqual(L.ludoCell(1, 56), [9, 7]);
});
t('need a 6 to leave base; exact count to finish', () => {
  const p = L.newLudo();
  L.ludoRoll(p, 3); assert.deepStrictEqual(L.ludoMoves(p, 3), []);
  L.ludoRoll(p, 6); assert.strictEqual(L.ludoMoves(p, 6).length, 4);
  p.pieces[0] = [55, 57, 57, 57];
  L.ludoRoll(p, 3); assert.deepStrictEqual(L.ludoMoves(p, 3), []); // 55+3 = 58 overshoots
  L.ludoRoll(p, 2); const mv = L.ludoMoves(p, 2)[0]; assert.strictEqual(mv.to, 57);
  const res = L.ludoApply(p, mv); assert.ok(res.finished && res.won); assert.strictEqual(p.winner, 0);
});
t('capture sends the enemy back to base, safe cells protect, extra turn granted', () => {
  const p = L.newLudo();
  // OraCool token on the cell that "you" reach at step 10 (not a safe cell)
  const cell = L.ludoCell(0, 10);
  let oraStep = -1; for (let s = 0; s <= 51; s++) { const c = L.ludoCell(1, s); if (c[0] === cell[0] && c[1] === cell[1]) oraStep = s; }
  assert.ok(oraStep >= 0); assert.ok(!L.isSafeCell(cell));
  p.pieces[1][0] = oraStep; p.pieces[0][0] = 6; L.ludoRoll(p, 4);
  const res = L.ludoApply(p, { tok: 0, from: 6, to: 10 });
  assert.strictEqual(res.captured, 1); assert.strictEqual(p.pieces[1][0], -1); assert.ok(res.extra);
  // safe star cell (start+8) cannot be captured on
  const p2 = L.newLudo(); const star = L.ludoCell(0, 8); assert.ok(L.isSafeCell(star));
  let os = -1; for (let s = 0; s <= 51; s++) { const c = L.ludoCell(1, s); if (c[0] === star[0] && c[1] === star[1]) os = s; }
  p2.pieces[1][0] = os; p2.pieces[0][0] = 5; L.ludoRoll(p2, 3);
  const r2 = L.ludoApply(p2, { tok: 0, from: 5, to: 8 });
  assert.strictEqual(r2.captured, 0); assert.strictEqual(p2.pieces[1][0], os);
});
t('OraCool prefers the capture', () => {
  const p = L.newLudo(); p.turn = 1;
  const cell = L.ludoCell(1, 12);
  let ys = -1; for (let s = 0; s <= 51; s++) { const c = L.ludoCell(0, s); if (c[0] === cell[0] && c[1] === cell[1]) ys = s; }
  p.pieces[0][2] = ys; p.pieces[1] = [8, 20, -1, -1]; L.ludoRoll(p, 4);
  assert.strictEqual(L.ludoThink(p).tok, 0);
});

// ---------- visuals ----------
t('chart/plot/compare/timeline/flow/table build SVG/HTML and escape text', () => {
  const bar = V.chart({ type: 'bar', title: 'Sales <b>x</b>', labels: ['Jan', 'Feb'], values: [10, 20], unit: '₦' });
  assert.ok(bar.startsWith('<svg') && bar.includes('&lt;b&gt;') && !bar.includes('<b>'));
  assert.ok(V.chart({ type: 'pie', labels: ['a', 'b', 'c'], values: [1, 1, 2] }).includes('<path'));
  assert.ok(V.chart({ type: 'line', labels: ['1', '2', '3'], values: [1, 3, 2] }).includes('<path'));
  assert.ok(V.plot({ fn: 'sin(x)*x', range: [-10, 10] }).includes('<path'));
  assert.ok(V.plot({ fns: ['x^2', '2x+1'], range: [-3, 3] }).split('<path').length >= 3);
  assert.ok(V.compare({ left: 'Lagos', right: 'Abuja', rows: [{ label: 'Rent', a: 500, b: 300 }] }).includes('Lagos'));
  assert.ok(V.timeline({ events: [{ date: '1960', text: 'Independence' }] }).includes('Independence'));
  assert.ok(V.flow({ nodes: [{ id: 'a', label: 'Start' }, { id: 'b', label: 'End' }], edges: [{ from: 'a', to: 'b' }] }).includes('marker-end'));
  assert.ok(V.table({ columns: ['Name', 'Age'], rows: [['Ada', 30]] }).includes('<td>Ada</td>'));
});
t('plot rejects code injection and unknown symbols', () => {
  assert.throws(() => V.compileFn('alert(1)'));
  assert.throws(() => V.compileFn('x;process.exit(1)'));
  assert.throws(() => V.compileFn('window.location'));
  assert.throws(() => V.compileFn('constructor("return 1")()'));
  assert.strictEqual(V.compileFn('2x+1')(3), 7);
  assert.strictEqual(V.compileFn('x^2')(4), 16);
  assert.ok(Math.abs(V.compileFn('sin(pi/2)')(0) - 1) < 1e-9);
});
t('extract pulls fenced app specs out of a reply; stripForSpeech drops even unterminated fences', () => {
  const r = A.extract('Let us play!\n```oracool-app\n{"app":"chess"}\n```\nYour move.');
  assert.strictEqual(r.specs.length, 1); assert.ok(r.text.includes('\u2063APP0\u2063')); assert.ok(!r.text.includes('{"app"'));
  assert.ok(!A.stripForSpeech('Sure.\n```oracool-app\n{"app":"chart","lab').includes('{"app"'));
  assert.strictEqual(A.stripForSpeech('plain text'), 'plain text');
  // tolerant of the ways models really write it: no tag, tag on next line, ```json, inline, bare
  for (const v of ['```\n{"app":"chess"}\n```', '```\noracool-app\n{"app":"chess"}\n```', '```json\n{"app":"chess"}\n```', '``` {"app":"chess"} ```', 'go {"app":"chess"} now'])
    assert.strictEqual(A.extract(v).specs.length, 1, v);
  assert.strictEqual(A.extract('```js\nconst x={app:1};\n```').specs.length, 0);
  assert.strictEqual(A.extract('{"app":"nuke"}').specs.length, 0);
  // escaped / 4-backtick fences leave no residue
  assert.strictEqual(A.extract('Go.\n\n\\```oracool-app\n{"app":"chess"}\n\\```').text.replace(/\u2063APP0\u2063/, '[APP]'), 'Go.\n\n[APP]');
  assert.strictEqual(A.extract('Hi!\n\n````\noracool-app\n{"app":"ludo"}\n````\n\nEnjoy.').text.replace(/\u2063APP0\u2063/, '[APP]'), 'Hi!\n\n[APP]\n\nEnjoy.');
  // model typos are repaired: key stutter, trailing comma, unquoted keys
  assert.strictEqual(A.parseSpec('{"app":"timeline","events":[{"date":"date":"Step 1","text":"x"},]}').events[0].date, 'Step 1');
  assert.strictEqual(A.parseSpec('{app:"chart",labels:["a"],values:[1],}').app, 'chart');
  assert.strictEqual(A.parseSpec('{"app":"chart","labels":[1,2'), null);
  const broken = A.extract('```oracool-app\n{"app":"chart","labels":["a"],"values":[1}\n```'); // unbalanced → left as text (never crashes)
  assert.ok(broken.specs.length === 0 || broken.specs.length === 1);
  assert.strictEqual(A.stripForSpeech('code: ```python\nprint(1)'), 'code: ```python\nprint(1)'); // real code is never hidden
});
t('command() is a no-op without an active board', () => { assert.strictEqual(A.command('e4'), null); });

console.log('\n' + n + ' engine checks passed');
