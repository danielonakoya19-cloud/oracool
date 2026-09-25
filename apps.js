/* OraCool apps — playable games + live visuals rendered inside the chat.
   The AI emits ```oracool-app fenced JSON; renderRich mounts it through OraApps.render().
   Engines are pure (no DOM) so node can unit-test them; DOM code only runs in the browser.
   Voice/chat control: OraApps.command(text) lets "knight f3" / "roll" drive the active board. */
(function (root, factory) {
  var api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.OraApps = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function num(v, d) { return (typeof v === 'number' && isFinite(v)) ? v : (typeof v === 'string' && v.trim() !== '' && isFinite(+v) ? +v : (d || 0)); }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function fmtN(v) {
    if (Math.abs(v) >= 1e9) return (v / 1e9).toFixed(1) + 'B';
    if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(1) + 'M';
    if (Math.abs(v) >= 1e3) return (v / 1e3).toFixed(1) + 'k';
    return (Math.round(v * 100) / 100).toString();
  }

  /* =====================================================================
     CHESS ENGINE — 64-square Int8Array (a1 = 0 … h8 = 63), white > 0, black < 0
     ===================================================================== */
  var P = 1, N = 2, B = 3, R = 4, Q = 5, K = 6, W = 1, BL = -1;
  var START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -';
  var KNIGHT_D = [[1, 2], [2, 1], [2, -1], [1, -2], [-1, -2], [-2, -1], [-2, 1], [-1, 2]];
  var KING_D = [[1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1]];
  var ROOK_D = [[1, 0], [-1, 0], [0, 1], [0, -1]];
  var BISH_D = [[1, 1], [1, -1], [-1, 1], [-1, -1]];
  var FILES = 'abcdefgh';

  function fenToBoard(fen) {
    var sq = new Int8Array(64), rows = fen.split(' ')[0].split('/');
    if (rows.length !== 8) throw new Error('bad fen');
    for (var i = 0; i < 8; i++) {
      var c = 0;
      for (var k = 0; k < rows[i].length; k++) {
        var ch = rows[i][k];
        if (ch >= '1' && ch <= '8') { c += +ch; continue; }
        var t = { p: P, n: N, b: B, r: R, q: Q, k: K }[ch.toLowerCase()];
        if (!t || c > 7) throw new Error('bad fen');
        sq[(7 - i) * 8 + c] = (ch === ch.toLowerCase()) ? -t : t;
        c++;
      }
    }
    return sq;
  }
  function sqName(i) { return FILES[i % 8] + (Math.floor(i / 8) + 1); }
  function sqIndex(name) {
    var f = FILES.indexOf(name[0]), r = +name[1] - 1;
    return (f < 0 || !(r >= 0 && r < 8)) ? -1 : r * 8 + f;
  }
  function idx(c, r) { return (c >= 0 && c < 8 && r >= 0 && r < 8) ? r * 8 + c : -1; }

  function fromFen(fen) {
    var p = String(fen || START_FEN).trim().split(/\s+/);
    var g = { sq: fenToBoard(p[0]), turn: p[1] === 'b' ? BL : W,
      cast: { wk: false, wq: false, bk: false, bq: false }, ep: -1, half: 0, full: 1, hist: [], over: null, result: '' };
    var cs = p[2] || 'KQkq';
    g.cast = { wk: /K/.test(cs), wq: /Q/.test(cs), bk: /k/.test(cs), bq: /q/.test(cs) };
    if (p[3] && p[3] !== '-') g.ep = sqIndex(p[3]);
    if (p[4]) g.half = +p[4] || 0;
    if (p[5]) g.full = +p[5] || 1;
    return g;
  }
  function newChess() { return fromFen(START_FEN); }
  function toFen(g) {
    var out = [];
    for (var r = 7; r >= 0; r--) {
      var line = '', empty = 0;
      for (var c = 0; c < 8; c++) {
        var v = g.sq[r * 8 + c];
        if (!v) { empty++; continue; }
        if (empty) { line += empty; empty = 0; }
        line += (v > 0 ? 'PNBRQK' : 'pnbrqk').charAt(Math.abs(v) - 1);
      }
      if (empty) line += empty;
      out.push(line);
    }
    var cast = (g.cast.wk ? 'K' : '') + (g.cast.wq ? 'Q' : '') + (g.cast.bk ? 'k' : '') + (g.cast.bq ? 'q' : '');
    return out.join('/') + ' ' + (g.turn === W ? 'w' : 'b') + ' ' + (cast || '-') + ' ' + (g.ep >= 0 ? sqName(g.ep) : '-') + ' ' + g.half + ' ' + g.full;
  }
  function snap(g) {
    return { sq: Int8Array.from(g.sq), turn: g.turn, cast: { wk: g.cast.wk, wq: g.cast.wq, bk: g.cast.bk, bq: g.cast.bq },
      ep: g.ep, half: g.half, full: g.full, over: g.over, result: g.result };
  }
  function restore(g, s) {
    g.sq = Int8Array.from(s.sq); g.turn = s.turn; g.cast = { wk: s.cast.wk, wq: s.cast.wq, bk: s.cast.bk, bq: s.cast.bq };
    g.ep = s.ep; g.half = s.half; g.full = s.full; g.over = s.over; g.result = s.result;
  }

  function attackedBy(g, sqI, by) {
    var c = sqI % 8, r = Math.floor(sqI / 8), i, k, cc, rr, p;
    var pr = r - by; // pawn that attacks sqI sits one rank behind (from attacker's view)
    for (var d = -1; d <= 1; d += 2) { i = idx(c + d, pr); if (i >= 0 && g.sq[i] === by * P) return true; }
    for (k = 0; k < 8; k++) { i = idx(c + KNIGHT_D[k][0], r + KNIGHT_D[k][1]); if (i >= 0 && g.sq[i] === by * N) return true; }
    for (k = 0; k < 8; k++) { i = idx(c + KING_D[k][0], r + KING_D[k][1]); if (i >= 0 && g.sq[i] === by * K) return true; }
    for (k = 0; k < 4; k++) {
      cc = c + ROOK_D[k][0]; rr = r + ROOK_D[k][1];
      while ((i = idx(cc, rr)) >= 0) { p = g.sq[i]; if (p) { if (p === by * R || p === by * Q) return true; break; } cc += ROOK_D[k][0]; rr += ROOK_D[k][1]; }
    }
    for (k = 0; k < 4; k++) {
      cc = c + BISH_D[k][0]; rr = r + BISH_D[k][1];
      while ((i = idx(cc, rr)) >= 0) { p = g.sq[i]; if (p) { if (p === by * B || p === by * Q) return true; break; } cc += BISH_D[k][0]; rr += BISH_D[k][1]; }
    }
    return false;
  }
  function kingSq(g, side) { for (var i = 0; i < 64; i++) if (g.sq[i] === side * K) return i; return -1; }
  function inCheck(g, side) { var k = kingSq(g, side); return k >= 0 && attackedBy(g, k, -side); }

  function pseudoMoves(g, side, capturesOnly) {
    var out = [];
    function push(f, t, tgt, cap, extra) {
      var m = { f: f, t: tgt, promo: 0, castle: 0, ep: false, cap: cap || 0 };
      if (extra) for (var e in extra) m[e] = extra[e];
      if (t === P && (Math.floor(tgt / 8) === 7 || Math.floor(tgt / 8) === 0)) m.promo = Q;
      out.push(m);
    }
    for (var i = 0; i < 64; i++) {
      var v = g.sq[i];
      if (!v || Math.sign(v) !== side) continue;
      var t = Math.abs(v), c = i % 8, r = Math.floor(i / 8), d, j, cc, rr;
      if (t === P) {
        var one = idx(c, r + side);
        if (!capturesOnly && one >= 0 && !g.sq[one]) {
          push(i, t, one, 0);
          var two = idx(c, r + 2 * side);
          if (r === (side === W ? 1 : 6) && two >= 0 && !g.sq[two]) push(i, t, two, 0);
        }
        for (d = -1; d <= 1; d += 2) {
          var dc = idx(c + d, r + side);
          if (dc < 0) continue;
          if (g.sq[dc] && Math.sign(g.sq[dc]) === -side) push(i, t, dc, g.sq[dc]);
          else if (dc === g.ep && g.ep >= 0) push(i, t, dc, -side * P, { ep: true });
        }
      } else if (t === N || t === K) {
        var D = t === N ? KNIGHT_D : KING_D;
        for (d = 0; d < 8; d++) {
          j = idx(c + D[d][0], r + D[d][1]);
          if (j < 0) continue;
          if (g.sq[j]) { if (Math.sign(g.sq[j]) === -side) push(i, t, j, g.sq[j]); }
          else if (!capturesOnly) push(i, t, j, 0);
        }
      } else {
        var dirs = t === R ? ROOK_D : t === B ? BISH_D : ROOK_D.concat(BISH_D);
        for (d = 0; d < dirs.length; d++) {
          cc = c + dirs[d][0]; rr = r + dirs[d][1];
          while ((j = idx(cc, rr)) >= 0) {
            if (g.sq[j]) { if (Math.sign(g.sq[j]) === -side) push(i, t, j, g.sq[j]); break; }
            if (!capturesOnly) push(i, t, j, 0);
            cc += dirs[d][0]; rr += dirs[d][1];
          }
        }
      }
    }
    if (!capturesOnly) {
      var home = side === W ? 4 : 60;
      if (g.sq[home] === side * K && !inCheck(g, side)) {
        if ((side === W ? g.cast.wk : g.cast.bk) && !g.sq[home + 1] && !g.sq[home + 2] && g.sq[home + 3] === side * R &&
          !attackedBy(g, home + 1, -side) && !attackedBy(g, home + 2, -side)) push(home, K, home + 2, 0, { castle: 'K' });
        if ((side === W ? g.cast.wq : g.cast.bq) && !g.sq[home - 1] && !g.sq[home - 2] && !g.sq[home - 3] && g.sq[home - 4] === side * R &&
          !attackedBy(g, home - 1, -side) && !attackedBy(g, home - 2, -side)) push(home, K, home - 2, 0, { castle: 'Q' });
      }
    }
    return out;
  }
  function applyRaw(g, m) {
    var v = g.sq[m.f], side = Math.sign(v), t = Math.abs(v);
    g.sq[m.t] = m.promo ? side * m.promo : v;
    g.sq[m.f] = 0;
    if (m.ep) g.sq[m.t - side * 8] = 0;
    var rank = Math.floor(m.t / 8) * 8;
    if (m.castle === 'K') { g.sq[rank + 5] = g.sq[rank + 7]; g.sq[rank + 7] = 0; }
    if (m.castle === 'Q') { g.sq[rank + 3] = g.sq[rank]; g.sq[rank] = 0; }
    g.ep = (t === P && Math.abs(m.t - m.f) === 16) ? (m.f + m.t) / 2 : -1;
    if (t === K) { if (side === W) { g.cast.wk = g.cast.wq = false; } else { g.cast.bk = g.cast.bq = false; } }
    [m.f, m.t].forEach(function (s) {
      if (s === 7) g.cast.wk = false; if (s === 0) g.cast.wq = false;
      if (s === 63) g.cast.bk = false; if (s === 56) g.cast.bq = false;
    });
    if (t === P || m.cap) g.half = 0; else g.half++;
    if (side === BL) g.full++;
    g.turn = -side;
  }
  function legalMoves(g, side, capturesOnly) {
    side = side || g.turn;
    var s = snap(g), raw = pseudoMoves(g, side, capturesOnly), legal = [];
    for (var i = 0; i < raw.length; i++) {
      applyRaw(g, raw[i]);
      if (!inCheck(g, side)) legal.push(raw[i]);
      restore(g, s);
    }
    return legal;
  }
  function insufficient(g) {
    var minors = 0;
    for (var i = 0; i < 64; i++) {
      var t = Math.abs(g.sq[i]);
      if (t === P || t === R || t === Q) return false;
      if (t === N || t === B) minors++;
    }
    return minors <= 1;
  }
  function applyMove(g, m) {
    g.hist.push(snap(g));
    applyRaw(g, m);
    var legal = legalMoves(g);
    if (!legal.length) { g.over = inCheck(g, g.turn) ? 'mate' : 'stalemate'; g.result = g.over === 'mate' ? (g.turn === W ? '0-1' : '1-0') : '½-½'; }
    else if (g.half >= 100) { g.over = 'draw50'; g.result = '½-½'; }
    else if (insufficient(g)) { g.over = 'material'; g.result = '½-½'; }
    return legal;
  }
  function undoMove(g) { var s = g.hist.pop(); if (s) restore(g, s); return !!s; }

  /* evaluation: material + piece-square tables (white's view; mirrored for black) */
  var VAL = [0, 100, 320, 330, 500, 900, 20000];
  var PST = [null,
    [0, 0, 0, 0, 0, 0, 0, 0, 5, 10, 10, -20, -20, 10, 10, 5, 5, -5, -10, 0, 0, -10, -5, 5, 0, 0, 0, 20, 20, 0, 0, 0, 5, 5, 10, 25, 25, 10, 5, 5, 10, 10, 20, 30, 30, 20, 10, 10, 50, 50, 50, 50, 50, 50, 50, 50, 0, 0, 0, 0, 0, 0, 0, 0],
    [-50, -40, -30, -30, -30, -30, -40, -50, -40, -20, 0, 5, 5, 0, -20, -40, -30, 5, 10, 15, 15, 10, 5, -30, -30, 0, 15, 20, 20, 15, 0, -30, -30, 5, 15, 20, 20, 15, 5, -30, -30, 0, 10, 15, 15, 10, 0, -30, -40, -20, 0, 0, 0, 0, -20, -40, -50, -40, -30, -30, -30, -30, -40, -50],
    [-20, -10, -10, -10, -10, -10, -10, -20, -10, 5, 0, 0, 0, 0, 5, -10, -10, 10, 10, 10, 10, 10, 10, -10, -10, 0, 10, 10, 10, 10, 0, -10, -10, 5, 5, 10, 10, 5, 5, -10, -10, 0, 5, 10, 10, 5, 0, -10, -10, 0, 0, 0, 0, 0, 0, -10, -20, -10, -10, -10, -10, -10, -10, -20],
    [0, 0, 0, 5, 5, 0, 0, 0, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, 5, 10, 10, 10, 10, 10, 10, 5, 0, 0, 0, 0, 0, 0, 0, 0],
    [-20, -10, -10, -5, -5, -10, -10, -20, -10, 0, 5, 0, 0, 0, 0, -10, -10, 5, 5, 5, 5, 5, 0, -10, 0, 0, 5, 5, 5, 5, 0, -5, -5, 0, 5, 5, 5, 5, 0, -5, -10, 0, 5, 5, 5, 5, 0, -10, -10, 0, 0, 0, 0, 0, 0, -10, -20, -10, -10, -5, -5, -10, -10, -20],
    [20, 30, 10, 0, 0, 10, 30, 20, 20, 20, 0, 0, 0, 0, 20, 20, -10, -20, -20, -20, -20, -20, -20, -10, -20, -30, -30, -40, -40, -30, -30, -20, -30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30]];
  function mirror(i) { return (7 - (i >> 3)) * 8 + (i & 7); }
  function evaluate(g) { // from the side-to-move's point of view
    var s = 0;
    for (var i = 0; i < 64; i++) {
      var v = g.sq[i]; if (!v) continue;
      var t = Math.abs(v), sc = VAL[t] + PST[t][v > 0 ? i : mirror(i)];
      s += v > 0 ? sc : -sc;
    }
    return g.turn === W ? s : -s;
  }
  function orderMoves(ms) { ms.sort(function (a, b) { return (b.cap ? VAL[Math.abs(b.cap)] * 10 - VAL[Math.abs(b.promo || 0)] : 0) - (a.cap ? VAL[Math.abs(a.cap)] * 10 : 0); }); return ms; }
  var _nodes = 0, _deadline = 0;
  function quiesce(g, alpha, beta, depth) {
    _nodes++;
    var stand = evaluate(g), best = stand; // fail-soft: always return a real value, never the bound
    if (stand >= beta || depth <= 0) return stand;
    if (stand > alpha) alpha = stand;
    var caps = orderMoves(legalMoves(g, 0, true)), s = snap(g);
    for (var i = 0; i < caps.length; i++) {
      applyRaw(g, caps[i]);
      var v = -quiesce(g, -beta, -alpha, depth - 1);
      restore(g, s);
      if (v > best) best = v;
      if (best > alpha) alpha = best;
      if (alpha >= beta) break;
    }
    return best;
  }
  function negamax(g, depth, alpha, beta, ply) {
    _nodes++;
    var legal = legalMoves(g);
    if (!legal.length) return inCheck(g, g.turn) ? -99999 + ply : 0;
    if (depth <= 0) return quiesce(g, alpha, beta, 3);
    orderMoves(legal);
    var best = -1e9, s = snap(g);
    for (var i = 0; i < legal.length; i++) {
      applyRaw(g, legal[i]);
      var v = -negamax(g, depth - 1, -beta, -alpha, ply + 1);
      restore(g, s);
      if (v > best) best = v;
      if (best > alpha) alpha = best;
      if (alpha >= beta) break;
      if (Date.now() > _deadline) break;
    }
    return best;
  }
  function searchDepth(g, depth) {
    var legal = orderMoves(legalMoves(g)), s = snap(g), best = null, bestV = -1e9;
    for (var i = 0; i < legal.length; i++) {
      applyRaw(g, legal[i]);
      var v = -negamax(g, depth - 1, -1e9, -bestV + 1, 1) + Math.random() * 3;
      restore(g, s);
      if (v > bestV) { bestV = v; best = legal[i]; }
      if (Date.now() > _deadline) break;
    }
    return { move: best, score: bestV, complete: Date.now() <= _deadline };
  }
  function bestMove(g, level) { // level 1..3 ⇒ depth; time-capped so the UI never freezes
    var legal = legalMoves(g);
    if (!legal.length) return null;
    if (legal.length === 1) return legal[0];
    _nodes = 0; _deadline = Date.now() + 1400;
    var maxD = clamp(level || 2, 1, 4), best = legal[0];
    for (var d = 1; d <= maxD; d++) {
      var r = searchDepth(g, d);
      if (r.move && (r.complete || d === 1)) best = r.move;
      if (!r.complete) break;
    }
    return best;
  }
  function moveToSan(g, m) {
    if (m.castle === 'K') return 'O-O';
    if (m.castle === 'Q') return 'O-O-O';
    var v = g.sq[m.f], t = Math.abs(v), s = '';
    if (t === P) { if (m.cap) s = FILES[m.f % 8] + 'x'; }
    else {
      s = 'PNBRQK'[t - 1];
      var others = legalMoves(g, Math.sign(v)).filter(function (o) { return o.t === m.t && o.f !== m.f && Math.abs(g.sq[o.f]) === t; });
      if (others.length) s += (others.some(function (o) { return o.f % 8 === m.f % 8; }) ? String(Math.floor(m.f / 8) + 1) : FILES[m.f % 8]);
      if (m.cap) s += 'x';
    }
    s += sqName(m.t);
    if (m.promo) s += '=Q';
    return s;
  }
  var PIECE_WORDS = { knight: 'n', horse: 'n', bishop: 'b', rook: 'r', castle: 'r', queen: 'q', king: 'k', pawn: '' };
  function parseMove(g, text) { // returns a legal move or null; accepts SAN, "e2e4", "knight to f3", "castle kingside"
    var t = String(text || '').toLowerCase().replace(/[.,!?]+$/g, '').trim();
    if (!t || t.length > 40) return null;
    var legal = legalMoves(g);
    if (/castle|o-o|0-0/.test(t)) {
      var longC = /queen|long|o-o-o|0-0-0/.test(t);
      return legal.filter(function (m) { return m.castle === (longC ? 'Q' : 'K'); })[0] || null;
    }
    var co = t.match(/^(?:move\s+)?(?:[a-z]+\s+)?([a-h][1-8])\s*(?:-|to|>|x|takes)?\s*([a-h][1-8])$/);
    if (co) { var f = sqIndex(co[1]), to = sqIndex(co[2]); return legal.filter(function (m) { return m.f === f && m.t === to; })[0] || null; }
    var n = t.replace(/\b(move|the|my|play|to|on|and|a)\b/g, ' ').replace(/\b(takes|captures|take|capture)\b/g, 'x');
    Object.keys(PIECE_WORDS).forEach(function (w) { n = n.replace(new RegExp('\\b' + w + '\\b', 'g'), PIECE_WORDS[w] || ''); });
    n = n.replace(/\s+/g, '').replace(/[+#]/g, '').replace(/=q$/, '');
    var mm = n.match(/^([nbrqk]?)([a-h]?)([1-8]?)x?([a-h][1-8])$/);
    if (!mm) return null;
    var piece = mm[1] ? 'pnbrqk'.indexOf(mm[1]) + 1 : P, tgt = sqIndex(mm[4]);
    var cands = legal.filter(function (m) {
      return m.t === tgt && Math.abs(g.sq[m.f]) === piece && !m.castle &&
        (!mm[2] || FILES[m.f % 8] === mm[2]) && (!mm[3] || Math.floor(m.f / 8) + 1 === +mm[3]);
    });
    return cands.length === 1 ? cands[0] : (cands[0] || null);
  }

  /* =====================================================================
     LUDO ENGINE — 15×15 board, 52-cell ring, 5 home cells + centre finish
     ===================================================================== */
  var TRACK = (function () {
    var t = [], i;
    for (i = 0; i <= 5; i++) t.push([i, 6]);      // left arm, top row →
    for (i = 5; i >= 0; i--) t.push([6, i]);      // up the top arm's left column
    t.push([7, 0]);
    for (i = 0; i <= 5; i++) t.push([8, i]);      // down the top arm's right column
    for (i = 9; i <= 14; i++) t.push([i, 6]);     // right arm, top row →
    t.push([14, 7]);
    for (i = 14; i >= 9; i--) t.push([i, 8]);     // right arm, bottom row ←
    for (i = 9; i <= 14; i++) t.push([8, i]);     // down the bottom arm's right column
    t.push([7, 14]);
    for (i = 14; i >= 9; i--) t.push([6, i]);     // up the bottom arm's left column
    for (i = 5; i >= 0; i--) t.push([i, 8]);      // left arm, bottom row ←
    t.push([0, 7]);
    return t;
  })();
  var LT = TRACK.length; // 52
  var HOME_LEN = 5, RING_STEPS = LT - 1; // a token walks 51 ring cells, then 5 home cells, then the centre
  var FINISH_STEP = RING_STEPS + HOME_LEN + 1; // 57
  function trackIndex(x, y) { for (var i = 0; i < LT; i++) if (TRACK[i][0] === x && TRACK[i][1] === y) return i; return -1; }
  var LUDO_PLAYERS = [
    { name: 'you', label: 'You', color: '#22c55e', start: trackIndex(0, 6), home: [[1, 7], [2, 7], [3, 7], [4, 7], [5, 7]], base: [[1, 1], [4, 1], [1, 4], [4, 4]] },
    { name: 'ora', label: 'OraCool', color: '#fb923c', start: trackIndex(14, 8), home: [[13, 7], [12, 7], [11, 7], [10, 7], [9, 7]], base: [[10, 10], [13, 10], [10, 13], [13, 13]] }
  ];
  function newLudo() {
    return { turn: 0, dice: 0, phase: 'roll', winner: -1, sixes: 0,
      pieces: [[-1, -1, -1, -1], [-1, -1, -1, -1]], // -1 base, 0..51 ring step, 52..56 home column, 57 finished
      log: [] };
  }
  function ludoCell(pi, step) { // board coordinate of a token at `step`
    if (step < 0) return null;
    if (step <= RING_STEPS) return TRACK[(LUDO_PLAYERS[pi].start + step) % LT];
    if (step < FINISH_STEP) return LUDO_PLAYERS[pi].home[step - RING_STEPS - 1];
    return [7, 7];
  }
  function isSafeCell(cell) {
    for (var i = 0; i < 2; i++) {
      var st = LUDO_PLAYERS[i].start, star = TRACK[(st + 8) % LT], sc = TRACK[st];
      if ((cell[0] === sc[0] && cell[1] === sc[1]) || (cell[0] === star[0] && cell[1] === star[1])) return true;
    }
    return false;
  }
  function ludoMoves(p, dice) {
    var pi = p.turn, out = [];
    for (var t = 0; t < 4; t++) {
      var step = p.pieces[pi][t];
      if (step === FINISH_STEP) continue;
      if (step === -1) { if (dice === 6) out.push({ tok: t, from: -1, to: 0 }); continue; }
      if (step + dice <= FINISH_STEP) out.push({ tok: t, from: step, to: step + dice });
    }
    return out;
  }
  function ludoApply(p, mv) {
    var pi = p.turn, pk = 1 - pi, captured = 0;
    p.pieces[pi][mv.tok] = mv.to;
    var cell = ludoCell(pi, mv.to);
    if (mv.to <= RING_STEPS && cell && !isSafeCell(cell)) {
      for (var o = 0; o < 4; o++) {
        var os = p.pieces[pk][o];
        if (os >= 0 && os <= RING_STEPS) {
          var oc = ludoCell(pk, os);
          if (oc[0] === cell[0] && oc[1] === cell[1]) { p.pieces[pk][o] = -1; captured++; }
        }
      }
    }
    var finished = mv.to === FINISH_STEP;
    var won = p.pieces[pi].every(function (s) { return s === FINISH_STEP; });
    if (won) { p.winner = pi; p.phase = 'over'; }
    return { captured: captured, finished: finished, won: won, extra: !won && (p.dice === 6 || captured > 0 || finished) };
  }
  function ludoRoll(p, forced) { p.dice = forced || (1 + Math.floor(Math.random() * 6)); p.phase = 'move'; return p.dice; }
  function ludoThink(p) { // OraCool's move choice
    var mv = ludoMoves(p, p.dice);
    if (!mv.length) return null;
    var pi = p.turn, pk = 1 - pi;
    function danger(cell) { // an enemy token within 6 behind this cell on the ring
      if (!cell || isSafeCell(cell)) return false;
      for (var o = 0; o < 4; o++) {
        var os = p.pieces[pk][o];
        if (os < 0 || os > RING_STEPS) continue;
        for (var d = 1; d <= 6; d++) { var c2 = ludoCell(pk, os + d); if (c2 && c2[0] === cell[0] && c2[1] === cell[1]) return true; }
      }
      return false;
    }
    function score(m) {
      var s = m.to * 0.25, cell = ludoCell(pi, m.to);
      if (m.from === -1) s += 12;
      if (m.to === FINISH_STEP) s += 80;
      else if (m.to > RING_STEPS) s += 20;
      if (m.to <= RING_STEPS && cell && !isSafeCell(cell)) {
        for (var o = 0; o < 4; o++) {
          var os = p.pieces[pk][o];
          if (os >= 0 && os <= RING_STEPS) { var oc = ludoCell(pk, os); if (oc[0] === cell[0] && oc[1] === cell[1]) s += 50; }
        }
      }
      if (cell && isSafeCell(cell)) s += 6;
      if (danger(cell)) s -= 15;
      if (m.from >= 0 && danger(ludoCell(pi, m.from))) s += 10; // escaping a threat
      return s;
    }
    mv.sort(function (a, b) { return score(b) - score(a); });
    return mv[0];
  }

  /* =====================================================================
     VISUALS — pure SVG string builders
     ===================================================================== */
  var COLORS = ['#00e5ff', '#ffb300', '#22c55e', '#ff5c7a', '#a78bfa', '#38bdf8', '#f472b6', '#facc15'];
  function svgOpen(w, h, title) {
    return '<svg viewBox="0 0 ' + w + ' ' + h + '" style="width:100%;height:auto;font-family:inherit" role="img">' +
      (title ? '<text x="' + w / 2 + '" y="16" text-anchor="middle" fill="#dbeafe" font-size="13" font-weight="700">' + esc(title) + '</text>' : '');
  }
  function vizChart(d) {
    var labels = (d.labels || []).map(String), vals = (d.values || []).map(function (v) { return num(v); });
    if (!labels.length || !vals.length) throw new Error('chart needs labels and values');
    var type = String(d.type || 'bar').toLowerCase(), unit = d.unit || '', W2 = 480, H2 = 260, pad = 34, i;
    var s = svgOpen(W2, H2, d.title);
    if (type === 'pie' || type === 'donut') {
      var total = vals.reduce(function (a, b) { return a + Math.abs(b); }, 0) || 1, ang = -Math.PI / 2;
      var cx = 120, cy = 142, rad = 82, rin = type === 'donut' ? 42 : 0;
      for (i = 0; i < vals.length; i++) {
        var frac = Math.abs(vals[i]) / total, a2 = ang + frac * Math.PI * 2, big = frac > 0.5 ? 1 : 0;
        if (frac >= 0.999) s += '<circle cx="' + cx + '" cy="' + cy + '" r="' + rad + '" fill="' + COLORS[i % 8] + '"/>';
        else if (frac > 0) s += '<path d="M' + cx + ' ' + cy + ' L' + (cx + rad * Math.cos(ang)).toFixed(1) + ' ' + (cy + rad * Math.sin(ang)).toFixed(1) +
          ' A' + rad + ' ' + rad + ' 0 ' + big + ' 1 ' + (cx + rad * Math.cos(a2)).toFixed(1) + ' ' + (cy + rad * Math.sin(a2)).toFixed(1) + ' Z" fill="' + COLORS[i % 8] + '" opacity=".92"/>';
        ang = a2;
      }
      if (rin) s += '<circle cx="' + cx + '" cy="' + cy + '" r="' + rin + '" fill="#0b1220"/>';
      for (i = 0; i < labels.length && i < 10; i++) {
        var ly = 50 + i * 20, pc = (Math.abs(vals[i]) / total * 100).toFixed(0);
        s += '<rect x="230" y="' + (ly - 9) + '" width="10" height="10" rx="2" fill="' + COLORS[i % 8] + '"/>';
        s += '<text x="246" y="' + ly + '" fill="#cbd5e1" font-size="11">' + esc(labels[i].slice(0, 22)) + ' — ' + esc(unit) + fmtN(vals[i]) + ' (' + pc + '%)</text>';
      }
    } else {
      var maxV = Math.max.apply(null, vals.map(Math.abs)) || 1, n = vals.length, plotH = H2 - 70;
      for (var gl = 1; gl <= 4; gl++) { var gy = H2 - 34 - plotH * gl / 4; s += '<line x1="' + pad + '" y1="' + gy.toFixed(1) + '" x2="' + (W2 - 8) + '" y2="' + gy.toFixed(1) + '" stroke="#1e293b"/><text x="4" y="' + (gy + 3).toFixed(1) + '" fill="#64748b" font-size="8.5">' + fmtN(maxV * gl / 4) + '</text>'; }
      s += '<line x1="' + pad + '" y1="' + (H2 - 34) + '" x2="' + (W2 - 8) + '" y2="' + (H2 - 34) + '" stroke="#334155"/>';
      if (type === 'line' || type === 'area') {
        var step = (W2 - pad - 12) / Math.max(1, n - 1), pts = '', area = '';
        for (i = 0; i < n; i++) {
          var px = pad + i * step, py = H2 - 34 - Math.abs(vals[i]) / maxV * plotH;
          pts += (i ? 'L' : 'M') + px.toFixed(1) + ' ' + py.toFixed(1) + ' ';
        }
        if (type === 'area' && n > 1) { area = pts + 'L' + (pad + (n - 1) * step).toFixed(1) + ' ' + (H2 - 34) + ' L' + pad + ' ' + (H2 - 34) + ' Z'; s += '<path d="' + area + '" fill="rgba(0,229,255,.16)"/>'; }
        s += '<path d="' + pts + '" fill="none" stroke="#00e5ff" stroke-width="2.2" stroke-linejoin="round"/>';
        for (i = 0; i < n; i++) {
          px = pad + i * step; py = H2 - 34 - Math.abs(vals[i]) / maxV * plotH;
          s += '<circle cx="' + px.toFixed(1) + '" cy="' + py.toFixed(1) + '" r="3.4" fill="#00e5ff"/>';
          if (n <= 12 || i % Math.ceil(n / 12) === 0) s += '<text x="' + px.toFixed(1) + '" y="' + (H2 - 20) + '" text-anchor="middle" fill="#94a3b8" font-size="9.5">' + esc(labels[i].slice(0, 10)) + '</text>';
          if (n <= 10) s += '<text x="' + px.toFixed(1) + '" y="' + (py - 8).toFixed(1) + '" text-anchor="middle" fill="#e2e8f0" font-size="9.5">' + esc(unit) + fmtN(vals[i]) + '</text>';
        }
      } else {
        var bw = (W2 - pad - 8) / n;
        for (i = 0; i < n; i++) {
          var h = Math.abs(vals[i]) / maxV * plotH, x = pad + i * bw + bw * 0.16, y = H2 - 34 - h;
          s += '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + (bw * 0.68).toFixed(1) + '" height="' + Math.max(2, h).toFixed(1) + '" rx="3" fill="' + COLORS[i % 8] + '" opacity=".88"/>';
          if (n <= 14) s += '<text x="' + (x + bw * 0.34).toFixed(1) + '" y="' + (y - 4).toFixed(1) + '" text-anchor="middle" fill="#e2e8f0" font-size="9.5">' + esc(unit) + fmtN(vals[i]) + '</text>';
          if (n <= 16 || i % Math.ceil(n / 16) === 0) s += '<text x="' + (x + bw * 0.34).toFixed(1) + '" y="' + (H2 - 20) + '" text-anchor="middle" fill="#94a3b8" font-size="9.5">' + esc(labels[i].slice(0, n > 8 ? 6 : 12)) + '</text>';
        }
      }
    }
    return s + '</svg>';
  }
  function compileFn(expr) { // whitelisted mini-math → Function(x); anything unknown is rejected
    var e2 = String(expr || '').replace(/^\s*(?:y|f\(x\))\s*=\s*/i, '').replace(/\bMath\./gi, '').toLowerCase().replace(/\s+/g, '');
    if (!e2 || e2.length > 120 || !/^[-+*/^().x0-9a-z,]+$/.test(e2)) throw new Error('unsupported expression');
    e2 = e2.replace(/\^/g, '**').replace(/(\d)(x|\()/g, '$1*$2').replace(/x(\d|\()/g, 'x*$1').replace(/\)(x|\d|\()/g, ')*$1');
    e2 = e2.replace(/\b(sin|cos|tan|asin|acos|atan|sinh|cosh|tanh|sqrt|cbrt|abs|log2|log10|log|exp|floor|ceil|round|min|max|pow)\(/g, 'Math.$1(')
      .replace(/\bln\(/g, 'Math.log(').replace(/\bpi\b/g, 'Math.PI').replace(/\be\b/g, 'Math.E');
    var stripped = e2.replace(/Math\.[A-Za-z0-9]+/g, '').replace(/x/g, '');
    if (/[a-zA-Z_$]/.test(stripped)) throw new Error('unknown symbol');
    /* eslint-disable no-new-func */
    var fn = new Function('x', '"use strict"; return (' + e2 + ');');
    fn(1); // smoke test
    return fn;
  }
  function vizPlot(d) {
    var fns = d.fn ? [d.fn] : (d.fns || []);
    if (!fns.length) throw new Error('plot needs fn');
    var rng = Array.isArray(d.range) && d.range.length === 2 ? d.range.map(Number) : [-10, 10];
    var xs = rng[0], xe = rng[1]; if (!(xe > xs)) { xs = -10; xe = 10; }
    var comp = [];
    fns.slice(0, 3).forEach(function (f) { try { comp.push({ f: compileFn(f), label: f }); } catch (e) { } });
    if (!comp.length) throw new Error('could not read that function');
    var N2 = 200, W2 = 480, H2 = 260, series = [], flat = [];
    comp.forEach(function (c) {
      var arr = [];
      for (var i = 0; i <= N2; i++) { var y; try { y = c.f(xs + (xe - xs) * i / N2); } catch (e) { y = NaN; } y = (typeof y === 'number' && isFinite(y)) ? y : NaN; arr.push(y); if (!isNaN(y)) flat.push(y); }
      series.push(arr);
    });
    if (!flat.length) throw new Error('function is empty on that range');
    flat.sort(function (a, b) { return a - b; });
    var mn = flat[Math.floor(flat.length * 0.02)], mx = flat[Math.ceil(flat.length * 0.98) - 1]; // trim asymptote spikes
    if (!(mx - mn > 1e-9)) { mx = mn + 1; mn = mn - 1; }
    var y0 = H2 - 30, y1 = 28, x0 = 40, x1 = W2 - 10;
    function X(i) { return x0 + (x1 - x0) * i / N2; }
    function Y(v) { return y0 - (v - mn) / (mx - mn) * (y0 - y1); }
    var s = svgOpen(W2, H2, d.title || ('f(x) = ' + fns.join(', ')));
    s += '<line x1="' + x0 + '" y1="' + y0 + '" x2="' + x1 + '" y2="' + y0 + '" stroke="#334155"/><line x1="' + x0 + '" y1="' + y1 + '" x2="' + x0 + '" y2="' + y0 + '" stroke="#334155"/>';
    if (mn < 0 && mx > 0) s += '<line x1="' + x0 + '" y1="' + Y(0).toFixed(1) + '" x2="' + x1 + '" y2="' + Y(0).toFixed(1) + '" stroke="#475569" stroke-dasharray="3 3"/>';
    if (xs < 0 && xe > 0) { var zx = x0 + (x1 - x0) * (0 - xs) / (xe - xs); s += '<line x1="' + zx.toFixed(1) + '" y1="' + y1 + '" x2="' + zx.toFixed(1) + '" y2="' + y0 + '" stroke="#475569" stroke-dasharray="3 3"/>'; }
    s += '<text x="4" y="' + (y1 + 4) + '" fill="#94a3b8" font-size="9">' + fmtN(mx) + '</text><text x="4" y="' + y0 + '" fill="#94a3b8" font-size="9">' + fmtN(mn) + '</text>';
    s += '<text x="' + x0 + '" y="' + (y0 + 14) + '" fill="#94a3b8" font-size="9">' + fmtN(xs) + '</text><text x="' + x1 + '" y="' + (y0 + 14) + '" text-anchor="end" fill="#94a3b8" font-size="9">' + fmtN(xe) + '</text>';
    series.forEach(function (arr, j) {
      var path = '', pen = false, prev = null;
      for (var i = 0; i <= N2; i++) {
        var v = arr[i];
        if (isNaN(v) || v < mn - (mx - mn) || v > mx + (mx - mn)) { pen = false; prev = null; continue; }
        var py = clamp(Y(v), y1 - 20, y0 + 20);
        if (prev != null && Math.abs(py - prev) > (y0 - y1) * 0.9) pen = false; // asymptote jump
        path += (pen ? 'L' : 'M') + X(i).toFixed(1) + ' ' + py.toFixed(1) + ' ';
        pen = true; prev = py;
      }
      s += '<path d="' + path + '" fill="none" stroke="' + COLORS[j] + '" stroke-width="2.2" stroke-linejoin="round"/>';
      if (series.length > 1) s += '<text x="' + (x0 + 8 + j * 130) + '" y="' + (y1 - 6) + '" fill="' + COLORS[j] + '" font-size="10">' + esc(comp[j].label.slice(0, 20)) + '</text>';
    });
    return s + '</svg>';
  }
  function vizCompare(d) {
    var rows = d.rows || [];
    if (!rows.length) throw new Error('compare needs rows');
    var left = d.left || 'A', right = d.right || 'B', W2 = 480, rh = 36, H2 = 46 + rows.length * rh + 6, unit = d.unit || '';
    var s = svgOpen(W2, H2, d.title || 'Compare');
    s += '<text x="' + (W2 * 0.46) + '" y="34" text-anchor="end" fill="#00e5ff" font-size="11" font-weight="700">' + esc(left) + '</text>';
    s += '<text x="' + (W2 * 0.54) + '" y="34" text-anchor="start" fill="#ffb300" font-size="11" font-weight="700">' + esc(right) + '</text>';
    for (var i = 0; i < rows.length && i < 20; i++) {
      var r = rows[i], y = 46 + i * rh;
      var numeric = (typeof r.a === 'number' || (typeof r.a === 'string' && r.a.trim() !== '' && isFinite(+r.a))) &&
        (typeof r.b === 'number' || (typeof r.b === 'string' && r.b.trim() !== '' && isFinite(+r.b)));
      if (!numeric) { // text comparison row
        s += '<text x="' + (W2 / 2) + '" y="' + (y + 10) + '" text-anchor="middle" fill="#94a3b8" font-size="9.5">' + esc(String(r.label || '').slice(0, 40)) + '</text>';
        s += '<text x="' + (W2 * 0.47).toFixed(1) + '" y="' + (y + 27) + '" text-anchor="end" fill="#e2e8f0" font-size="10">' + esc(String(r.a == null ? '' : r.a).slice(0, 30)) + '</text>';
        s += '<text x="' + (W2 * 0.53).toFixed(1) + '" y="' + (y + 27) + '" text-anchor="start" fill="#e2e8f0" font-size="10">' + esc(String(r.b == null ? '' : r.b).slice(0, 30)) + '</text>';
        continue;
      }
      var a = num(r.a), b = num(r.b), mx = Math.max(Math.abs(a), Math.abs(b), 1e-9);
      var la = W2 * 0.42 * Math.abs(a) / mx, lb = W2 * 0.42 * Math.abs(b) / mx;
      s += '<text x="' + (W2 / 2) + '" y="' + (y + 10) + '" text-anchor="middle" fill="#94a3b8" font-size="9.5">' + esc(String(r.label || '').slice(0, 40)) + '</text>';
      s += '<rect x="' + (W2 * 0.47 - la).toFixed(1) + '" y="' + (y + 15) + '" width="' + Math.max(1.5, la).toFixed(1) + '" height="9" rx="3" fill="#00e5ff" opacity=".85"/>';
      s += '<rect x="' + (W2 * 0.53) + '" y="' + (y + 15) + '" width="' + Math.max(1.5, lb).toFixed(1) + '" height="9" rx="3" fill="#ffb300" opacity=".85"/>';
      s += '<text x="' + (W2 * 0.465).toFixed(1) + '" y="' + (y + 32) + '" text-anchor="end" fill="#e2e8f0" font-size="9.5">' + esc(unit) + fmtN(a) + '</text>';
      s += '<text x="' + (W2 * 0.535).toFixed(1) + '" y="' + (y + 32) + '" text-anchor="start" fill="#e2e8f0" font-size="9.5">' + esc(unit) + fmtN(b) + '</text>';
    }
    return s + '</svg>';
  }
  function vizTimeline(d) {
    var ev = (d.events || []).slice(0, 40);
    if (!ev.length) throw new Error('timeline needs events');
    var rh = 32, W2 = 480, H2 = 30 + ev.length * rh + 8, cx = 70;
    var s = svgOpen(W2, H2, d.title || 'Timeline');
    s += '<line x1="' + cx + '" y1="26" x2="' + cx + '" y2="' + (H2 - 6) + '" stroke="#334155" stroke-width="2"/>';
    ev.forEach(function (e, i) {
      var y = 36 + i * rh;
      s += '<circle cx="' + cx + '" cy="' + y + '" r="4.5" fill="' + COLORS[i % 8] + '"/>';
      s += '<text x="' + (cx - 12) + '" y="' + (y + 3) + '" text-anchor="end" fill="#ffb300" font-size="9.5" font-weight="700">' + esc(String(e.date || '').slice(0, 12)) + '</text>';
      s += '<text x="' + (cx + 14) + '" y="' + (y + 3) + '" fill="#cbd5e1" font-size="10.5">' + esc(String(e.text || '').slice(0, 64)) + '</text>';
    });
    return s + '</svg>';
  }
  function vizFlow(d) {
    var nodes = (d.nodes || []).slice(0, 24), edges = d.edges || [];
    if (!nodes.length) throw new Error('flow needs nodes');
    var byId = {}, depth = {};
    nodes.forEach(function (n) { byId[n.id] = n; depth[n.id] = 0; });
    for (var it = 0; it < nodes.length; it++) {
      var changed = false;
      edges.forEach(function (e) { if (byId[e.from] && byId[e.to] && depth[e.to] <= depth[e.from] && depth[e.from] < 12) { depth[e.to] = depth[e.from] + 1; changed = true; } });
      if (!changed) break;
    }
    var rows = {};
    nodes.forEach(function (n) { (rows[depth[n.id]] = rows[depth[n.id]] || []).push(n); });
    var levels = Object.keys(rows).map(Number).sort(function (a, b) { return a - b; });
    var nw = 128, nh = 36, gx = 22, gy = 46, maxRow = 1;
    levels.forEach(function (l) { maxRow = Math.max(maxRow, rows[l].length); });
    var W2 = clamp(maxRow * (nw + gx) + gx, 320, 620), H2 = 30 + levels.length * (nh + gy);
    var s = svgOpen(W2, H2, d.title);
    s += '<defs><marker id="oc-arw" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L8 4L0 8z" fill="#64748b"/></marker></defs>';
    var pos = {};
    levels.forEach(function (l, li) {
      var arr = rows[l], rowW = arr.length * (nw + gx) - gx, xStart = (W2 - rowW) / 2;
      arr.forEach(function (n, i) { pos[n.id] = { x: xStart + i * (nw + gx) + nw / 2, y: 28 + li * (nh + gy) + nh / 2 }; });
    });
    edges.forEach(function (e) {
      var a = pos[e.from], b = pos[e.to];
      if (!a || !b) return;
      var ay = a.y + nh / 2, by = b.y - nh / 2, my = (ay + by) / 2;
      if (b.y <= a.y) { ay = a.y; by = b.y; my = (ay + by) / 2; }
      s += '<path d="M' + a.x.toFixed(1) + ' ' + ay.toFixed(1) + ' C' + a.x.toFixed(1) + ' ' + my.toFixed(1) + ' ' + b.x.toFixed(1) + ' ' + my.toFixed(1) + ' ' + b.x.toFixed(1) + ' ' + (by - 1).toFixed(1) + '" fill="none" stroke="#64748b" stroke-width="1.6" marker-end="url(#oc-arw)"/>';
      if (e.label) s += '<text x="' + ((a.x + b.x) / 2 + 5).toFixed(1) + '" y="' + (my - 2).toFixed(1) + '" fill="#94a3b8" font-size="9">' + esc(String(e.label).slice(0, 18)) + '</text>';
    });
    nodes.forEach(function (n) {
      var p2 = pos[n.id]; if (!p2) return;
      var lab = String(n.label || n.id);
      s += '<rect x="' + (p2.x - nw / 2) + '" y="' + (p2.y - nh / 2) + '" width="' + nw + '" height="' + nh + '" rx="10" fill="rgba(0,229,255,.07)" stroke="rgba(0,229,255,.45)"/>';
      if (lab.length <= 18) s += '<text x="' + p2.x + '" y="' + (p2.y + 4) + '" text-anchor="middle" fill="#e2e8f0" font-size="10.5">' + esc(lab) + '</text>';
      else s += '<text x="' + p2.x + '" y="' + (p2.y - 2) + '" text-anchor="middle" fill="#e2e8f0" font-size="10">' + esc(lab.slice(0, 18)) + '</text><text x="' + p2.x + '" y="' + (p2.y + 10) + '" text-anchor="middle" fill="#e2e8f0" font-size="10">' + esc(lab.slice(18, 36)) + '</text>';
    });
    return s + '</svg>';
  }
  function vizTable(d) {
    var cols = (d.columns || []).map(String), rows = (d.rows || []).slice(0, 40);
    if (!cols.length || !rows.length) throw new Error('table needs columns and rows');
    var h = '<div style="overflow:auto"><table class="oc-tbl"><thead><tr>' + cols.map(function (c) { return '<th>' + esc(c) + '</th>'; }).join('') + '</tr></thead><tbody>';
    rows.forEach(function (r) { h += '<tr>' + cols.map(function (c, i) { return '<td>' + esc(Array.isArray(r) ? r[i] : r[c]) + '</td>'; }).join('') + '</tr>'; });
    return h + '</tbody></table></div>' + (d.title ? '<div class="oc-cap">' + esc(d.title) + '</div>' : '');
  }
  // patch39: clarifying question → tappable options (Arena-style ask_user)
  function vizAsk(d) {
    var q = String(d.question || d.q || d.title || 'Which one?').slice(0, 240);
    var opts = (Array.isArray(d.options) ? d.options : []).map(function (o) { return typeof o === 'string' ? o : (o && (o.label || o.text || o.id)) || ''; })
      .filter(function (o) { return o && String(o).trim(); }).slice(0, 6);
    if (!opts.length) throw new Error('ask needs options');
    return '<div class="oc-ask" role="group" aria-label="' + esc(q) + '"><div class="q">' + esc(q) + '</div><div class="opts">' +
      opts.map(function (o) { return '<button type="button" data-opt="' + esc(String(o)) + '">' + esc(String(o)) + '</button>'; }).join('') + '</div></div>';
  }
  function wireAsk(host) {
    var box = host.querySelector('.oc-ask'); if (!box) return;
    var btns = box.querySelectorAll('.opts button');
    for (var i = 0; i < btns.length; i++) {
      btns[i].onclick = function () {
        if (box.classList.contains('done')) return;
        box.classList.add('done'); this.classList.add('picked');
        try { if (api.onAsk) api.onAsk(this.getAttribute('data-opt')); } catch (e) { }
      };
    }
  }
  function vizCountdown(d) {
    return '<div class="oc-cd" data-target="' + esc(d.target || '') + '"><div class="oc-cdl">' + esc(d.label || 'Countdown') + '</div><div class="oc-cdr">' +
      ['days', 'hrs', 'min', 'sec'].map(function (u) { return '<div><div class="oc-cdn" data-u="' + u + '">–</div><div class="oc-cdu">' + u + '</div></div>'; }).join('') + '</div></div>';
  }
  function tickCountdowns(rootEl) {
    if (typeof document === 'undefined') return;
    (rootEl || document).querySelectorAll('.oc-cd').forEach(function (el) {
      if (el._iv) return;
      function upd() {
        var t = Date.parse(el.dataset.target || '');
        if (!isFinite(t)) { el.querySelector('[data-u=days]').textContent = '?'; return; }
        var ms = t - Date.now(), neg = ms < 0; ms = Math.abs(ms);
        var v = { days: Math.floor(ms / 864e5), hrs: Math.floor(ms / 36e5) % 24, min: Math.floor(ms / 6e4) % 60, sec: Math.floor(ms / 1e3) % 60 };
        el.querySelectorAll('[data-u]').forEach(function (b) { b.textContent = (neg && b.dataset.u === 'days' ? '-' : '') + (b.dataset.u === 'days' ? v.days : String(v[b.dataset.u]).padStart(2, '0')); });
      }
      upd(); el._iv = setInterval(upd, 1000);
    });
  }

  /* =====================================================================
     DOM: chess board
     ===================================================================== */
  var GLYPH = { 1: '♟', 2: '♞', 3: '♝', 4: '♜', 5: '♛', 6: '♚' };
  var active = null; // the board that chat/voice commands drive
  function say(text) { try { if (api.onSay) api.onSay(text); } catch (e) { } }

  function mountChess(host, spec) {
    var g; try { g = fromFen(spec && spec.fen); } catch (e) { g = newChess(); }
    var level = clamp(num(spec && spec.level, 2), 1, 4), sel = -1, flipped = g.turn === BL, thinking = false;
    host.innerHTML = '<div class="oc-game"><div class="oc-bar"><b class="oc-cap"></b><span><button class="oc-btn" data-a="undo">↶ undo</button><button class="oc-btn" data-a="flip">⇅ flip</button><button class="oc-btn" data-a="reset">⟲ new</button></span></div>' +
      '<div class="oc-board"></div><div class="oc-moves">You play white. Tap a piece then a glowing square — or just say the move: "knight f3", "e4", "castle".</div></div>';
    var board = host.querySelector('.oc-board'), note = host.querySelector('.oc-moves'), cap = host.querySelector('.oc-cap'), cells = [];
    for (var i = 0; i < 64; i++) { var c = document.createElement('div'); c.className = 'oc-sq'; board.appendChild(c); cells.push(c); }
    function draw() {
      var legal = g.over ? [] : legalMoves(g), targets = {};
      legal.forEach(function (m) { if (m.f === sel) targets[m.t] = m; });
      var ks = kingSq(g, g.turn), chk = !g.over && inCheck(g, g.turn);
      for (var k = 0; k < 64; k++) {
        var i2 = flipped ? k : (7 - Math.floor(k / 8)) * 8 + (k % 8); // top-left cell shows a8 (or h1 flipped)
        if (flipped) i2 = Math.floor(k / 8) * 8 + (7 - k % 8);
        var v = g.sq[i2], el = cells[k], dark = ((i2 % 8) + Math.floor(i2 / 8)) % 2 === 0;
        el.className = 'oc-sq' + (dark ? ' d' : '') + (sel === i2 ? ' sel' : '') + (targets[i2] ? (targets[i2].cap ? ' cap' : ' mv') : '') + (chk && i2 === ks ? ' chk' : '');
        el.textContent = v ? GLYPH[Math.abs(v)] : '';
        el.classList.toggle('w', v > 0);
        el.dataset.i = i2;
      }
      cap.textContent = '♟ Chess — ' + (g.over ? ('game over ' + g.result) : (g.turn === W ? 'your move (white)' : 'OraCool is thinking…'));
    }
    function setNote(t) { note.textContent = t; }
    function engineReply() {
      thinking = true; draw();
      setTimeout(function () {
        var em = bestMove(g, level), txt = '';
        if (em) { txt = moveToSan(g, em); applyMove(g, em); }
        thinking = false; sel = -1; draw();
        var line = txt ? ('OraCool plays ' + txt + (g.over ? '' : (inCheck(g, W) ? ' — check!' : ''))) : '';
        if (g.over) line += (line ? ' ' : '') + gameOverText();
        if (line) { setNote(line); say(line); }
      }, 220);
    }
    function gameOverText() {
      if (g.over === 'mate') return g.result === '1-0' ? 'Checkmate — you win! ⟲ new for a rematch.' : 'Checkmate — OraCool wins this one. ⟲ new for a rematch.';
      return 'Draw (' + g.over + '). ⟲ new to play again.';
    }
    function userMove(m) {
      var txt = moveToSan(g, m);
      applyMove(g, m); sel = -1; draw();
      setNote('You: ' + txt);
      if (g.over) { var t = gameOverText(); setNote('You: ' + txt + ' — ' + t); say(t); return 'You played ' + txt + '. ' + t; }
      engineReply();
      return 'You played ' + txt + '.';
    }
    board.addEventListener('click', function (ev) {
      var el = ev.target.closest ? ev.target.closest('.oc-sq') : null;
      if (!el || g.over || thinking || g.turn !== W) return;
      var i2 = +el.dataset.i;
      var mv = sel >= 0 ? legalMoves(g).filter(function (m) { return m.f === sel && m.t === i2; })[0] : null;
      if (mv) { userMove(mv); return; }
      sel = (g.sq[i2] > 0) ? i2 : -1; draw();
    });
    host.querySelector('.oc-bar').addEventListener('click', function (ev) {
      var a = ev.target.dataset && ev.target.dataset.a;
      if (a === 'flip') { flipped = !flipped; draw(); }
      else if (a === 'reset') { g = newChess(); sel = -1; thinking = false; setNote('New game — you are white. Your move.'); draw(); }
      else if (a === 'undo') { if (thinking) return; undoMove(g); if (g.turn !== W) undoMove(g); sel = -1; setNote('Move taken back.'); draw(); }
    });
    active = { type: 'chess', host: host,
      command: function (text) {
        if (g.over || thinking || g.turn !== W) return null;
        var m = parseMove(g, text);
        return m ? userMove(m) : null;
      },
      state: function () { return toFen(g); } };
    if (g.turn === BL && !g.over) engineReply(); else draw();
  }

  /* =====================================================================
     DOM: ludo board
     ===================================================================== */
  function mountLudo(host) {
    var p = newLudo(), busy = false, tokEls = [];
    host.innerHTML = '<div class="oc-game"><div class="oc-bar"><b class="oc-cap">🎲 Ludo — you (green) vs OraCool (orange)</b><span><button class="oc-btn oc-dice" data-a="roll">🎲 roll</button><button class="oc-btn" data-a="reset">⟲ new</button></span></div>' +
      '<div class="oc-ludo"></div><div class="oc-moves">Tap 🎲 (or say "roll"). A 6 leaves base; captures and 6s roll again; exact count to finish.</div></div>';
    var board = host.querySelector('.oc-ludo'), note = host.querySelector('.oc-moves'), dice = host.querySelector('.oc-dice'), cells = {};
    for (var y = 0; y < 15; y++) for (var x = 0; x < 15; x++) {
      var c = document.createElement('div');
      c.className = 'oc-lc' + ((x < 6 && y < 6) ? ' b0' : (x > 8 && y > 8) ? ' b1' : (x > 8 && y < 6) ? ' bx' : (x < 6 && y > 8) ? ' by' : (x >= 6 && x <= 8 && y >= 6 && y <= 8) ? ' mid' : '');
      board.appendChild(c); cells[x + ',' + y] = c;
    }
    TRACK.forEach(function (t, i) { var el = cells[t[0] + ',' + t[1]]; el.classList.add('trk'); if (isSafeCell(t)) el.classList.add('safe'); LUDO_PLAYERS.forEach(function (pl, pi) { if (i === pl.start) el.classList.add('st' + pi); }); });
    LUDO_PLAYERS.forEach(function (pl, pi) { pl.home.forEach(function (h) { cells[h[0] + ',' + h[1]].classList.add('hm' + pi); }); });
    cells['7,7'].textContent = '🏁';
    for (var t = 0; t < 8; t++) { var tk = document.createElement('div'); tk.className = 'oc-tok p' + (t < 4 ? 0 : 1); tk.textContent = (t % 4) + 1; tk.dataset.t = t; board.appendChild(tk); tokEls.push(tk); }
    function place(movable) {
      tokEls.forEach(function (tk) {
        var t2 = +tk.dataset.t, pi = t2 < 4 ? 0 : 1, ti = t2 % 4, step = p.pieces[pi][ti], xy;
        if (step === -1) xy = LUDO_PLAYERS[pi].base[ti];
        else if (step === FINISH_STEP) xy = pi === 0 ? [6.1 + ti * 0.25, 6.6] : [7.9 - ti * 0.25, 7.6];
        else { xy = ludoCell(pi, step).slice(); var mates = p.pieces[pi].filter(function (s, k) { return k < ti && s === step; }).length; xy = [xy[0] + mates * 0.2, xy[1] - mates * 0.2]; }
        tk.style.left = 'calc(' + (xy[0] + 0.07) + ' * 100% / 15)'; tk.style.top = 'calc(' + (xy[1] + 0.07) + ' * 100% / 15)';
        tk.classList.toggle('can', !!(movable && pi === p.turn && movable.some(function (m) { return m.tok === ti; })));
        tk.classList.toggle('done', step === FINISH_STEP);
      });
    }
    var DF = '⚀⚁⚂⚃⚄⚅';
    function setNote(t) { note.textContent = t; }
    function options() { return (busy || p.phase !== 'move' || p.winner >= 0) ? [] : ludoMoves(p, p.dice); }
    function roll(pi) {
      busy = true;
      var d = ludoRoll(p), opts = ludoMoves(p, d), who = pi === 0 ? 'You rolled ' : 'OraCool rolled ';
      dice.textContent = DF[d - 1] + ' ' + d;
      if (!opts.length) {
        var msg = who + d + ' — no legal move' + (d === 6 ? ', but a 6 rolls again' : '') + '.';
        setNote(msg); if (pi === 1) say(msg);
        setTimeout(function () { busy = false; if (d === 6) roll(pi); else nextTurn(); }, pi === 1 ? 900 : 600);
        return msg;
      }
      if (pi === 1) { setNote(who + d + '…'); setTimeout(function () { move(1, ludoThink(p) || opts[0]); }, 800); return ''; }
      busy = false;
      var m2 = 'You rolled ' + d + (opts.length > 1 ? ' — tap a glowing token or say "move 1/2/3/4".' : '.');
      setNote(m2); place(opts);
      if (opts.length === 1 && p.pieces[0].filter(function (s) { return s >= 0 && s < FINISH_STEP; }).length <= 1) setTimeout(function () { if (!busy && p.phase === 'move') move(0, opts[0]); }, 350); // only one token in play: auto-move
      return m2;
    }
    function describe(pi, mv, res) {
      var who = pi === 0 ? 'You' : 'OraCool rolled ' + p.dice + ' and', t = 'token ' + (mv.tok + 1);
      var s = who + (mv.from === -1 ? ' brought ' + t + ' out of base' : res.finished ? ' brought ' + t + ' home' : ' moved ' + t + ' ' + (mv.to - mv.from) + ' steps');
      if (res.captured) s += ' and CAPTURED ' + (pi === 0 ? "OraCool's" : 'your') + ' token — back to base';
      if (res.won) s += '. ' + (pi === 0 ? '🏆 You win the match!' : 'OraCool wins the match — ⟲ new for a rematch.');
      else if (res.extra) s += (pi === 0 ? '. Roll again!' : ' — rolls again.');
      return s + (res.won ? '' : '.');
    }
    function move(pi, mv) {
      busy = true; p.phase = 'roll';
      var res = ludoApply(p, mv), msg = describe(pi, mv, res);
      place([]); setNote(msg); if (pi === 1 || res.won) say(msg);
      if (res.won) { dice.textContent = '🏁'; busy = false; return msg; }
      setTimeout(function () { busy = false; if (res.extra) { if (pi === 1) roll(1); } else nextTurn(); }, 500);
      return msg;
    }
    function nextTurn() {
      p.turn = 1 - p.turn; p.phase = 'roll'; place([]);
      if (p.turn === 1) { setNote('OraCool rolls…'); setTimeout(function () { roll(1); }, 600); } else setNote('Your roll — tap 🎲 or say "roll".');
    }
    board.addEventListener('click', function (ev) {
      if (!ev.target.classList.contains('oc-tok')) return;
      var t2 = +ev.target.dataset.t; if (t2 > 3 || p.turn !== 0) return;
      var mv = options().filter(function (m) { return m.tok === t2 % 4; })[0];
      if (mv) move(0, mv);
    });
    host.querySelector('.oc-bar').addEventListener('click', function (ev) {
      var a = ev.target.dataset && ev.target.dataset.a;
      if (a === 'roll') { if (!busy && p.turn === 0 && p.phase === 'roll' && p.winner < 0) roll(0); }
      else if (a === 'reset') { p = newLudo(); busy = false; dice.textContent = '🎲 roll'; setNote('New match — your roll first.'); place([]); }
    });
    active = { type: 'ludo', host: host,
      command: function (text) {
        var t = String(text || '').toLowerCase();
        if (p.winner >= 0 || busy || p.turn !== 0) return null;
        if (p.phase === 'roll' && /\b(roll|throw|dice|die|shake)\b/.test(t)) return roll(0);
        if (p.phase === 'move') {
          var opts = options(), mm = t.match(/\b(?:token|piece|seed|move|number|no\.?)?\s*([1-4]|one|two|three|four|first|second|third|fourth)\b/);
          var idxMap = { one: 1, first: 1, two: 2, second: 2, three: 3, third: 3, four: 4, fourth: 4 };
          var n = mm ? (idxMap[mm[1]] || +mm[1]) : (opts.length === 1 && /\b(move|go|play|advance|any)\b/.test(t) ? opts[0].tok + 1 : 0);
          var mv = opts.filter(function (m) { return m.tok === n - 1; })[0];
          return mv ? move(0, mv) : null;
        }
        return null;
      },
      state: function () { return JSON.stringify(p.pieces); } };
    place([]);
  }

  /* =====================================================================
     dispatch + chat/voice command hook
     ===================================================================== */
  function buildStatic(d) {
    var app = String(d.app || '').toLowerCase();
    if (app === 'chart') return vizChart(d);
    if (app === 'plot' || app === 'graph' || app === 'function') return vizPlot(d);
    if (app === 'compare') return vizCompare(d);
    if (app === 'timeline') return vizTimeline(d);
    if (app === 'flow' || app === 'diagram' || app === 'flowchart') return vizFlow(d);
    if (app === 'table') return vizTable(d);
    if (app === 'countdown') return vizCountdown(d);
    if (app === 'ask' || app === 'question' || app === 'choice') return vizAsk(d);
    throw new Error('unknown app "' + app + '"');
  }
  function render(spec, host) {
    var d = typeof spec === 'string' ? parseSpec(spec) : spec;
    try {
      if (!d || typeof d !== 'object') throw new Error('the spec was not valid JSON');
      var app = String(d.app || '').toLowerCase();
      if (app === 'chess') return mountChess(host, d);
      if (app === 'ludo') return mountLudo(host, d);
      host.innerHTML = '<div class="oc-app">' + buildStatic(d) + '</div>';
      if (app === 'countdown') tickCountdowns(host);
      if (app === 'ask' || app === 'question' || app === 'choice') wireAsk(host);
    } catch (e) {
      host.innerHTML = '<div class="oc-err">⚠️ That visual could not be built (' + esc((e && e.message) || 'error').slice(0, 80) + ') — ask OraCool to redo it.</div>';
    }
  }
  var KNOWN = { chess: 1, ludo: 1, chart: 1, plot: 1, graph: 1, 'function': 1, compare: 1, timeline: 1, flow: 1, diagram: 1, flowchart: 1, table: 1, countdown: 1, ask: 1, question: 1, choice: 1 };
  var APP_RE = /\{\s*"app"\s*:\s*"([a-z_-]+)"/gi;
  function matchBrace(s, start) { // index of the brace closing the object at `start`, or -1
    var depth = 0, inStr = false;
    for (var i = start; i < s.length; i++) {
      var ch = s[i];
      if (inStr) { if (ch === '\\') i++; else if (ch === '"') inStr = false; continue; }
      if (ch === '"') inStr = true;
      else if (ch === '{') depth++;
      else if (ch === '}') { depth--; if (depth === 0) return i; }
    }
    return -1;
  }
  function parseSpec(body) { // strict JSON first, then gentle repairs for the typos models make
    var tries = [body,
      body.replace(/,\s*([}\]])/g, '$1'),                                   // trailing commas
      body.replace(/"([A-Za-z_]+)"\s*:\s*"\1"\s*:/g, '"$1":'),                // "date":"date":"…" stutter
      body.replace(/,\s*([}\]])/g, '$1').replace(/"([A-Za-z_]+)"\s*:\s*"\1"\s*:/g, '"$1":').replace(/([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:/g, '$1"$2":')]; // unquoted keys
    for (var i = 0; i < tries.length; i++) { try { var d = JSON.parse(tries[i]); if (d && typeof d === 'object') return d; } catch (e) { } }
    return null;
  }
  function extract(text) { // → { text: prose with \u2063APPn\u2063 placeholders, specs: [json strings] }
    var s = String(text || ''), specs = [], out = '', last = 0, m;
    APP_RE.lastIndex = 0;
    while ((m = APP_RE.exec(s))) {
      if (m.index < last) continue;
      var end = matchBrace(s, m.index);
      if (end < 0 || !KNOWN[m[1].toLowerCase()]) continue;
      var body = s.slice(m.index, end + 1); // kept even if unparseable: render() shows a friendly error instead of raw JSON
      var pre = s.slice(last, m.index), closeLen = 0;
      var fo = pre.match(/\\?`{3,4}[^`{}]{0,40}$/); // "```oracool-app\n", "```\noracool-app\n", "```json\n", "``` ", "\```", "````" …
      if (fo) {
        pre = pre.slice(0, pre.length - fo[0].length);
        var fc = s.slice(end + 1).match(/^\s*\\?`{3,4}[ \t]*/);
        if (fc) closeLen = fc[0].length;
      }
      specs.push(body);
      out += pre + '\u2063APP' + (specs.length - 1) + '\u2063';
      last = end + 1 + closeLen;
      APP_RE.lastIndex = last;
    }
    return { text: out + s.slice(last), specs: specs };
  }
  function stripForSpeech(text) { // no spec JSON in speech or in the streaming preview — even while a fence is still open
    var t = extract(text).text.replace(/\u2063APP\d+\u2063/g, ' ');
    var i = t.lastIndexOf('```');
    if (i >= 0) {
      var tail = t.slice(i + 3);
      if (tail.indexOf('```') < 0 && /(oracool[-_]app|oraapp|\{\s*"app"\s*:)/i.test(tail.slice(0, 80))) t = t.slice(0, i);
    }
    return t;
  }
  function command(text) { // called by the chat before contacting the AI; returns reply text or null
    if (!active || !active.host || (typeof document !== 'undefined' && !document.body.contains(active.host))) { active = null; return null; }
    try { return active.command(text); } catch (e) { return null; }
  }

  var api = {
    render: render, extract: extract, parseSpec: parseSpec, stripForSpeech: stripForSpeech, command: command, tickCountdowns: tickCountdowns, buildStatic: buildStatic, esc: esc,
    onSay: null, onAsk: null, get active() { return active; },
    chess: { newChess: newChess, fromFen: fromFen, toFen: toFen, legalMoves: legalMoves, applyMove: applyMove, undoMove: undoMove, bestMove: bestMove,
      inCheck: inCheck, moveToSan: moveToSan, parseMove: parseMove, attackedBy: attackedBy, sqIndex: sqIndex, sqName: sqName, evaluate: evaluate },
    ludo: { TRACK: TRACK, LT: LT, RING_STEPS: RING_STEPS, FINISH_STEP: FINISH_STEP, PLAYERS: LUDO_PLAYERS, newLudo: newLudo, ludoRoll: ludoRoll,
      ludoMoves: ludoMoves, ludoApply: ludoApply, ludoThink: ludoThink, ludoCell: ludoCell, isSafeCell: isSafeCell },
    viz: { chart: vizChart, plot: vizPlot, compare: vizCompare, timeline: vizTimeline, flow: vizFlow, table: vizTable, countdown: vizCountdown, compileFn: compileFn }
  };
  return api;
});
