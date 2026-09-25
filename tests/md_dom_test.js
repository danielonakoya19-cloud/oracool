const assert=require('assert');
const {JSDOM}=(function(){try{return require('jsdom');}catch(e){try{return require('/tmp/domtest/node_modules/jsdom');}catch(e2){console.log('jsdom not installed — skipped');process.exit(0);}}})();
const fs=require('fs');
const dom=new JSDOM('<!doctype html><body><div id="chat"></div></body>',{pretendToBeVisual:true});
global.window=dom.window; global.document=dom.window.document; global.self=dom.window;
const src=fs.readFileSync(__dirname+'/../index.html','utf8');
window.OraMd=require(__dirname+'/../md.js');
window.OraApps=require(__dirname+'/../apps.js');
// pull the real renderRich + helpers out of index.html
function fn(name){ const i=src.indexOf('function '+name+'('); assert.ok(i>0,name); let d=0,j=src.indexOf('{',i); for(let k=j;k<src.length;k++){ if(src[k]==='{')d++; else if(src[k]==='}'){d--; if(!d) return src.slice(i,k+1);} } }
const code=[fn('escapeHtml'),fn('_cleanReply'),fn('renderRich')].join('\n');
const vm=require('vm'); const ctx={window,document,OraApps:window.OraApps,OraMd:window.OraMd,console}; vm.createContext(ctx); vm.runInContext(code+'\nthis.renderRich=renderRich;',ctx);
const el=document.createElement('div');
ctx.renderRich(el,"**What I CAN Do Instead:**\n\n| You Want | What I Actually Offer |\n|----------|------------------------|\n| Unlimited freedom | **Focused precision** |\n\n1. one\n2. two\n\nplain <b>x</b> line");
assert.ok(el.classList.contains('md'));
assert.strictEqual(el.querySelectorAll('table.md-table').length,1);
assert.strictEqual(el.querySelectorAll('th').length,2);
assert.strictEqual(el.querySelector('td b').textContent,'Focused precision');
assert.strictEqual(el.querySelectorAll('ol.md-list li').length,2);
assert.ok(!el.textContent.includes('|'),'no raw pipes');
assert.ok(el.innerHTML.includes('&lt;b&gt;x&lt;/b&gt;'),'model html is escaped');
// apps still work through the markdown path
const el2=document.createElement('div'); document.getElementById('chat').appendChild(el2);
ctx.renderRich(el2,'Board is up, sir.\n```oracool-app\n{"app":"chess"}\n```\nYour move.');
assert.strictEqual(el2.querySelectorAll('.oc-sq').length,64,'chess board renders via OraMd path');
assert.ok(el2.textContent.includes('Board is up') && el2.textContent.includes('Your move'));
// wantsPreview regex (extracted)
const wp=src.slice(src.indexOf('function wantsPreview(q){'), src.indexOf('if(wantsPreview(t))'));
vm.runInContext(wp+'\nthis.wantsPreview=wantsPreview;',ctx);
for(const q of ['show me the preview','can you show the preview of the website you built','preview is not loading','show me the website you built','open the site you made','let me see the page']) assert.ok(ctx.wantsPreview(q),q);
for(const q of ['open youtube','show me the preview of the code','build a website for my shop','open the website of nike','preview my document']) assert.ok(!ctx.wantsPreview(q),'neg: '+q);
console.log('DOM OK');
