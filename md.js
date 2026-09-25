/* OraMd — small, safe markdown renderer for chat bubbles (tables, code, lists, headings, quotes, rules).
   Pure string → HTML, no DOM needed (unit-tested in node). Input is RAW text; output is escaped HTML. */
(function (root, factory) {
  var api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.OraMd = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  var IMG = /\.(png|jpe?g|gif|webp)(\?[^\s)]*)?$/i;
  function inline(raw) { // inline markdown on ONE escaped line
    var s = esc(raw);
    // images ![alt](url)
    s = s.replace(/!\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g, '<img src="$2" alt="$1" class="md-img" onerror="this.style.display=\'none\'">');
    // links [text](url) — image urls inline as pictures
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, function (m, txt, url) {
      if (IMG.test(url)) return '<a href="' + url + '" target="_blank" rel="noopener"><img src="' + url + '" alt="' + txt + '" class="md-img"></a>';
      return '<a href="' + url + '" target="_blank" rel="noopener">' + txt + '</a>';
    });
    // bare media urls
    s = s.replace(/(^|[^"'>=])(https?:\/\/[^\s<]+?\.(?:png|jpe?g|gif|webp))(\?[^\s<]*)?(?=$|[\s<])/gi, '$1<a href="$2$3" target="_blank" rel="noopener"><img src="$2$3" class="md-img" onerror="this.style.display=\'none\'"></a>');
    s = s.replace(/(^|[^"'>=])(https?:\/\/[^\s<]+?\.(?:mp4|webm|mov))(\?[^\s<]*)?(?=$|[\s<])/gi, '$1<video controls src="$2$3" class="md-video"></video>');
    s = s.replace(/(^|[^"'>=])(https?:\/\/[^\s<]+?\.(?:mp3|wav|ogg|m4a))(\?[^\s<]*)?(?=$|[\s<])/gi, '$1<audio controls src="$2$3" class="md-audio"></audio>');
    // remaining bare links
    s = s.replace(/(^|[^"'>=\/])(https?:\/\/[^\s<]+[^\s<.,;:!?)\]])/g, function (m, pre, url) { return pre + '<a href="' + url + '" target="_blank" rel="noopener">' + url + '</a>'; });
    // inline code first so its content is protected from emphasis rules
    var codes = [];
    s = s.replace(/`([^`\n]+)`/g, function (m, c) { codes.push('<code class="md-code">' + c + '</code>'); return '\u0000' + (codes.length - 1) + '\u0000'; });
    s = s.replace(/\*\*\*([^*\n]+)\*\*\*/g, '<b><i>$1</i></b>');
    s = s.replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>');
    s = s.replace(/__([^_\n]+)__/g, '<b>$1</b>');
    s = s.replace(/(^|[^\w*])\*([^*\n]+)\*(?![\w*])/g, '$1<i>$2</i>');
    s = s.replace(/(^|[^\w_])_([^_\n]+)_(?![\w_])/g, '$1<i>$2</i>');
    s = s.replace(/~~([^~\n]+)~~/g, '<s>$1</s>');
    s = s.replace(/\*{2,}/g, '').replace(/(^|[^\w*])\*(?![\w*])/g, '$1'); // stray marks
    s = s.replace(/\u0000(\d+)\u0000/g, function (m, i) { return codes[+i]; });
    return s;
  }
  function isTableRow(l) { return /^\s*\|.*\|\s*$/.test(l); }
  function isSepRow(l) { return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l) && l.indexOf('-') >= 0; }
  function splitRow(l) {
    var t = l.trim(); if (t[0] === '|') t = t.slice(1); if (t[t.length - 1] === '|') t = t.slice(0, -1);
    return t.split(/(?<!\\)\|/).map(function (c) { return c.trim().replace(/\\\|/g, '|'); });
  }
  function render(text) {
    var lines = String(text || '').replace(/\r\n?/g, '\n').split('\n'), out = [], i = 0, n = lines.length;
    var para = [];
    function flushPara() { if (para.length) { out.push('<p class="md-p">' + para.map(inline).join('<br>') + '</p>'); para = []; } }
    while (i < n) {
      var l = lines[i];
      // fenced code
      var f = l.match(/^\s*```\s*([\w+-]*)([ \t][^`]*)?$/);
      if (f) {
        flushPara();
        var buf = [], lang = f[1], info = (f[2] || '').trim(); i++;
        while (i < n && !/^\s*```\s*$/.test(lines[i])) { buf.push(lines[i]); i++; }
        i++; // closing fence (or EOF)
        out.push('<pre class="md-pre"' + (lang ? ' data-lang="' + esc(lang) + '"' : '') + (info ? ' data-info="' + esc(info) + '"' : '') + '><code>' + esc(buf.join('\n')) + '</code></pre>');
        continue;
      }
      // table: header row + separator row
      if (isTableRow(l) && i + 1 < n && isSepRow(lines[i + 1]) && lines[i + 1].indexOf('|') >= 0) {
        flushPara();
        var head = splitRow(l), aligns = splitRow(lines[i + 1]).map(function (c) { return /^:-+:$/.test(c) ? 'center' : /^-+:$/.test(c) ? 'right' : ''; });
        i += 2; var rows = [];
        while (i < n && isTableRow(lines[i]) && !isSepRow(lines[i])) { rows.push(splitRow(lines[i])); i++; }
        var h = '<div class="md-tablewrap"><table class="md-table"><thead><tr>' + head.map(function (c, k) { return '<th' + (aligns[k] ? ' style="text-align:' + aligns[k] + '"' : '') + '>' + inline(c) + '</th>'; }).join('') + '</tr></thead>';
        if (rows.length) h += '<tbody>' + rows.map(function (r) { return '<tr>' + head.map(function (_, k) { return '<td' + (aligns[k] ? ' style="text-align:' + aligns[k] + '"' : '') + '>' + inline(r[k] == null ? '' : r[k]) + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody>';
        out.push(h + '</table></div>');
        continue;
      }
      // stray separator line without a header (models often emit these) → drop
      if (isSepRow(l) && l.indexOf('|') >= 0) { i++; continue; }
      // heading
      var hm = l.match(/^\s*(#{1,6})\s+(.+?)\s*#*\s*$/);
      if (hm) { flushPara(); var lvl = Math.min(4, hm[1].length + 1); out.push('<h' + lvl + ' class="md-h">' + inline(hm[2]) + '</h' + lvl + '>'); i++; continue; }
      // horizontal rule
      if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(l)) { flushPara(); out.push('<hr class="md-hr">'); i++; continue; }
      // blockquote
      if (/^\s*>\s?/.test(l)) {
        flushPara(); var q = [];
        while (i < n && /^\s*>\s?/.test(lines[i])) { q.push(lines[i].replace(/^\s*>\s?/, '')); i++; }
        out.push('<blockquote class="md-q">' + render(q.join('\n')) + '</blockquote>');
        continue;
      }
      // lists (bulleted / numbered, one nesting level by indentation)
      var lm = l.match(/^(\s*)([-•*+]|\d{1,3}[.)])\s+(.*)$/);
      if (lm) {
        flushPara();
        var ordered = /\d/.test(lm[2]), items = [], start = ordered ? parseInt(lm[2], 10) : 1;
        while (i < n) {
          var m2 = lines[i].match(/^(\s*)([-•*+]|\d{1,3}[.)])\s+(.*)$/);
          if (!m2 || (/\d/.test(m2[2]) !== ordered)) break;
          var item = { text: m2[3], sub: [] }; i++;
          while (i < n && /^\s{2,}(?:[-•*+]|\d{1,3}[.)])\s+/.test(lines[i])) { item.sub.push(lines[i].replace(/^\s+(?:[-•*+]|\d{1,3}[.)])\s+/, '')); i++; }
          while (i < n && /^\s{2,}\S/.test(lines[i]) && !/^\s*(?:[-•*+]|\d{1,3}[.)])\s+/.test(lines[i])) { item.text += ' ' + lines[i].trim(); i++; } // wrapped continuation
          items.push(item);
        }
        var tag = ordered ? 'ol' : 'ul';
        out.push('<' + tag + ' class="md-list"' + (ordered && start > 1 ? ' start="' + start + '"' : '') + '>' + items.map(function (it) {
          return '<li>' + inline(it.text) + (it.sub.length ? '<ul class="md-list">' + it.sub.map(function (s2) { return '<li>' + inline(s2) + '</li>'; }).join('') + '</ul>' : '') + '</li>';
        }).join('') + '</' + tag + '>');
        continue;
      }
      // blank line → paragraph break
      if (!l.trim()) { flushPara(); i++; continue; }
      // app placeholders from OraApps sit on their own block
      if (/^\s*\u2063APP\d+\u2063\s*$/.test(l)) { flushPara(); out.push(l.trim()); i++; continue; }
      para.push(l); i++;
    }
    flushPara();
    return out.join('');
  }
  return { render: render, inline: inline, esc: esc };
});
