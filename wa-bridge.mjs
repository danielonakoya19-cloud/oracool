#!/usr/bin/env node
/* OraCool WhatsApp link bridge — REAL WhatsApp multi-device sessions (the same
 * pairing handshake WhatsApp Web uses). One Node process serves every OraCool
 * user session; the Python server talks to 127.0.0.1 only and proxies to the
 * app. The QR shown to users is ALWAYS the live pairing payload from WhatsApp —
 * never drawn, never faked. Sessions persist as auth folders that the Python
 * server stores in its key-value vault and restores on boot.
 *
 * TOTAL ACCESS: send text and media, read chats and messages, react, delete my
 * messages, mute / archive / mark-read chats, set profile about, and run an
 * auto-reply bot ON THE USER'S COMMAND ONLY (bot.json rides inside the session
 * folder so it survives redeploys). */
import makeWASocket, {
  fetchLatestBaileysVersion, DisconnectReason, useMultiFileAuthState, jidNormalizedUser
} from '@whiskeysockets/baileys'
import http from 'http'
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

const __dir = path.dirname(fileURLToPath(import.meta.url))
const SESS_DIR = process.env.WA_SESS_DIR || path.join(__dir, 'wa-sessions')
const PORT = parseInt(process.env.WA_BRIDGE_PORT || '8788', 10)
fs.mkdirSync(SESS_DIR, { recursive: true })

const silent = { level: 'silent', child: () => silent, trace() {}, debug() {}, info() {}, warn() {}, error() {} }
const BOOT_TS = Date.now()
const sessions = new Map() // id -> { sock, qr, state, user, chats, msgs, bot, lastReply }

// name persistence (survives redeploys) + one-time app-state resync
function persistNames(rec) {
  try {
    if (!rec.dir || rec._pt) return
    rec._pt = setTimeout(() => {
      rec._pt = null
      try {
        fs.writeFileSync(path.join(rec.dir, 'wa-names.json'), JSON.stringify({
          contacts: [...rec.contacts.entries()], chats: [...rec.chats.entries()],
        }))
      } catch (e) {}
    }, 3000)
  } catch (e) {}
}
function loadNames(rec) {
  try {
    const o = JSON.parse(fs.readFileSync(path.join(rec.dir, 'wa-names.json'), 'utf8'))
    for (const kv of (o.contacts || [])) rec.contacts.set(kv[0], kv[1])
    for (const kv of (o.chats || [])) rec.chats.set(kv[0], kv[1])
    return true
  } catch (e) { return false }
}
function maybeWipeSyncKeys(dir) {
  try {
    if (fs.existsSync(path.join(dir, 'wa-names.json'))) return
    for (const f of fs.readdirSync(dir)) {
      if (/app-state-sync-key/i.test(f)) fs.unlinkSync(path.join(dir, f))
    }
  } catch (e) {}
}

function safeId(id) { return String(id || '').replace(/[^a-zA-Z0-9._-]/g, '').slice(0, 40) }

function toJid(rec, to) {
  const raw = String(to || '').trim()
  if (!raw) return null
  if (raw.includes('@g.us') || raw.includes('@s.whatsapp.net') || raw.includes('@lid')) return raw
  const digits = raw.replace(/[^\d]/g, '')
  if (digits.length >= 7) return digits + '@s.whatsapp.net'
  // name resolution against synced chats AND saved contacts — "Mum", "John"
  if (rec) {
    const want = raw.toLowerCase()
    for (const [jid, ch] of rec.chats) {
      const nm = String(ch.name || '').toLowerCase()
      if (nm && (nm.includes(want) || want.includes(nm))) return jid
    }
    for (const [jid, nm] of (rec.contacts || new Map())) {
      const n2 = String(nm || '').toLowerCase()
      if (n2 && (n2.includes(want) || want.includes(n2))) return jid
    }
    for (const jid of rec.msgs.keys()) {
      const who = jid.split('@')[0]
      if (who.includes(digits) && digits) return jid
    }
  }
  return null
}

function botFile(id) { return path.join(SESS_DIR, id, 'bot.json') }

function loadBot(id) {
  try { return JSON.parse(fs.readFileSync(botFile(id), 'utf8')) } catch (e) {
    return { enabled: false, reply: '', rules: [], skipGroups: true, gapSeconds: 90 }
  }
}
function saveBot(id, bot) {
  try {
    fs.mkdirSync(path.join(SESS_DIR, id), { recursive: true })
    fs.writeFileSync(botFile(id), JSON.stringify(bot))
  } catch (e) {}
}

async function openSession(id) {
  if (sessions.has(id)) return sessions.get(id)
  const dir = path.join(SESS_DIR, id)
  fs.mkdirSync(dir, { recursive: true })
  maybeWipeSyncKeys(dir)
  const { state, saveCreds } = await useMultiFileAuthState(dir)
  const { version } = await fetchLatestBaileysVersion()
  const rec = {
    sock: null, qr: '', state: 'connecting', user: null,
    chats: new Map(), msgs: new Map(), contacts: new Map(),
    bot: loadBot(id), lastReply: new Map(), saveCreds, dir
  }
  sessions.set(id, rec)
  loadNames(rec)
  const sock = makeWASocket({
    version, auth: state, printQRInTerminal: false, logger: silent,
    markOnlineOnConnect: false, syncFullHistory: true,
    generateHighQualityLinkPreview: false
  })
  rec.sock = sock
  sock.ev.on('creds.update', saveCreds)
  sock.ev.on('connection.update', (u) => {
    if (u.qr) { rec.qr = u.qr; rec.state = 'qr' }
    if (u.connection === 'open') {
      rec.state = 'connected'; rec.qr = ''
      try { rec.user = { id: sock.user?.id || '', name: sock.user?.name || '' } } catch (e) {}
      // fill group/newsletter display names so chat lists are readable
      setTimeout(async () => {
        for (const [jid, ch] of rec.chats) {
          if (ch.name) continue
          try {
            if (String(jid).endsWith('@g.us')) {
              const meta = await sock.groupMetadata(jid)
              if (meta && meta.subject) { ch.name = meta.subject; rec.chats.set(jid, ch) }
            }
          } catch (e) {}
        }
      }, 2500)
    }
    if (u.connection === 'close') {
      const code = u.lastDisconnect?.error?.output?.statusCode
      rec.state = code === DisconnectReason.loggedOut ? 'logged_out' : 'disconnected'
      if (code !== DisconnectReason.loggedOut) {
        sessions.delete(id)
        setTimeout(() => { try { openSession(id) } catch (e) {} }, 1500)
      } else {
        try { fs.rmSync(path.join(SESS_DIR, id), { recursive: true, force: true }) } catch (e) {}
        sessions.delete(id)
      }
    }
  })
  sock.ev.on('messaging-history.set', ({ chats, contacts, messages }) => {
    try {
      for (const c of contacts || []) {
        const nm = String(c.name || c.notify || c.verifiedName || '').trim()
        if (c.id && nm) rec.contacts.set(c.id, nm)
      }
      for (const ch of chats || []) {
        const nm = String(ch.name || rec.contacts.get(ch.id) || '').trim()
        rec.chats.set(ch.id, { id: ch.id, name: nm, ts: ch.conversationTimestamp || 0 })
      }
      for (const m of messages || []) cacheMsg(rec, m)
      persistNames(rec)
    } catch (e) {}
  })
  sock.ev.on('contacts.upsert', (cs) => {
    try {
      for (const c of (cs || [])) if (c && c.id) rec.contacts.set(c.id, c.name || c.notify || c.verifiedName || rec.contacts.get(c.id) || '')
      persistNames(rec)
    } catch (e) {}
  })
  sock.ev.on('contacts.update', (cs) => {
    try {
      for (const c of (cs || [])) if (c && c.id) rec.contacts.set(c.id, c.name || c.notify || c.verifiedName || rec.contacts.get(c.id) || '')
    } catch (e) {}
  })
  sock.ev.on('chats.upsert', (chs) => {
    try {
      for (const c of (chs || [])) if (c && c.id) rec.chats.set(c.id, {
        id: c.id, name: c.name || (rec.chats.get(c.id) || {}).name || '',
        ts: +(c.conversationTimestamp || Date.now()),
      })
      persistNames(rec)
    } catch (e) {}
  })
  sock.ev.on('chats.update', (chs) => {
    try {
      for (const c of (chs || [])) {
        if (!c || !c.id) continue
        const prev = rec.chats.get(c.id) || {}
        rec.chats.set(c.id, { id: c.id, name: c.name || prev.name || '', ts: +(c.conversationTimestamp || prev.ts || Date.now()) })
      }
    } catch (e) {}
  })
  sock.ev.on('messages.upsert', ({ messages }) => {
    for (const m of messages || []) {
      try { cacheMsg(rec, m) } catch (e) {}
      maybeBotReply(rec, id, m).catch(() => {})
    }
  })
  return rec
}

function cacheMsg(rec, m) {
  const j = m.key?.remoteJid
  try {
    const pn = String(m.pushName || '').trim()
    const who = (!m.key?.fromMe && pn && j && !String(j).endsWith('@g.us')) ? j : ''
    if (who && pn && !rec.contacts.has(who)) { rec.contacts.set(who, pn); persistNames(rec) }
  } catch (e) {}
  if (!j || j === 'status@broadcast') return
  const list = rec.msgs.get(j) || []
  list.push({ fromMe: !!m.key?.fromMe, ts: Number(m.messageTimestamp || 0), text: msgText(m.message), id: m.key?.id || '' })
  if (list.length > 50) list.shift()
  rec.msgs.set(j, list)
  const nm = rec.chats.get(j) || { id: j, name: '', ts: 0 }
  nm.ts = Math.max(nm.ts, Number(m.messageTimestamp || 0))
  rec.chats.set(j, nm)
}

async function maybeBotReply(rec, id, m) {
  const bot = rec.bot || {}
  if (!bot.enabled) return
  if (m.key?.fromMe) return
  const j = m.key?.remoteJid
  if (!j || j === 'status@broadcast') return
  if (bot.skipGroups !== false && String(j).endsWith('@g.us')) return
  const text = msgText(m.message)
  if (!text) return
  let reply = ''
  for (const r of bot.rules || []) {
    if (r.match && text.toLowerCase().includes(String(r.match).toLowerCase())) { reply = r.reply || ''; break }
  }
  if (!reply) reply = bot.reply || ''
  if (!reply) return
  const gap = (Number(bot.gapSeconds) || 90) * 1000
  const last = rec.lastReply.get(j) || 0
  if (Date.now() - last < gap) return
  rec.lastReply.set(j, Date.now())
  const wait = 1500 + Math.floor(Math.random() * 3500)  // human-ish pause
  setTimeout(async () => {
    try {
      if (!rec.sock || rec.state !== 'connected') return
      try { await rec.sock.sendPresenceUpdate('composing', j) } catch (e) {}
      await rec.sock.sendMessage(j, { text: String(reply).slice(0, 2000) })
      try { await rec.sock.sendPresenceUpdate('paused', j) } catch (e) {}
    } catch (e) {}
  }, wait)
}

function msgText(m) {
  try {
    return String(m?.conversation || m?.extendedTextMessage?.text || m?.imageMessage?.caption ||
      m?.videoMessage?.caption || m?.documentMessage?.caption ||
      (m?.imageMessage ? '[image]' : '') + (m?.videoMessage ? '[video]' : '') +
      (m?.audioMessage ? '[voice note]' : '') + (m?.documentMessage ? '[document]' : '') || '')
  } catch (e) { return '' }
}

function sendJson(res, code, obj) {
  res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
  res.end(JSON.stringify(obj))
}

async function doAction(rec, j) {
  const kind = String(j.kind || '').toLowerCase()
  const to = toJid(rec, j.to)
  if (kind === 'about') {
    await rec.sock.updateProfileStatus(String(j.text || '').slice(0, 200))
    return { ok: true, message: 'WhatsApp profile status updated.' }
  }
  if (!to) return { error: 'recipient not found (give a number with country code, or a chat name that exists)' }
  if (kind === 'send') {
    if (j.image_url) {
      await rec.sock.sendMessage(to, { image: { url: String(j.image_url) }, caption: String(j.text || j.caption || '').slice(0, 2000) })
      return { ok: true, message: 'WhatsApp media sent to ' + String(j.to) + '.' }
    }
    const r = await rec.sock.sendMessage(to, { text: String(j.text || '').slice(0, 4000) })
    return { ok: true, id: r?.key?.id || '', message: 'WhatsApp message sent to ' + String(j.to) + '.' }
  }
  const msgs = rec.msgs.get(to) || []
  if (kind === 'react') {
    const mine = msgs[msgs.length - 1]
    if (!mine || !mine.id) return { error: 'no message found to react to in that chat yet' }
    await rec.sock.sendMessage(to, { react: { text: String(j.emoji || '👍').slice(0, 8), key: { remoteJid: to, id: mine.id, fromMe: !!mine.fromMe } } })
    return { ok: true, message: 'Reacted ' + String(j.emoji || '👍') + ' in ' + String(j.to) + '.' }
  }
  if (kind === 'delete') {
    const mine = [...msgs].reverse().find(x => x.fromMe && x.id)
    if (!mine) return { error: 'no message of yours found in that chat to delete' }
    await rec.sock.sendMessage(to, { delete: { remoteJid: to, id: mine.id, fromMe: true } })
    return { ok: true, message: 'Your last message in ' + String(j.to) + ' was deleted.' }
  }
  if (kind === 'mute') {
    await rec.sock.chatModify('mute', to, Number(j.minutes || 8 * 60))
    return { ok: true, message: String(j.to) + ' muted.' }
  }
  if (kind === 'archive') {
    await rec.sock.chatModify('archive', to, true)
    return { ok: true, message: String(j.to) + ' archived.' }
  }
  if (kind === 'markread') {
    await rec.sock.chatModify('markRead', to)
    return { ok: true, message: String(j.to) + ' marked read.' }
  }
  return { error: 'unknown action ' + kind }
}

async function handle(req, res) {
  const u = new URL(req.url, 'http://127.0.0.1')
  const p = u.pathname
  const id = safeId(u.searchParams.get('session') || '')
  try {
    if (p === '/health') return sendJson(res, 200, { ok: true, sessions: sessions.size, boot: BOOT_TS })
    if (!id) return sendJson(res, 400, { error: 'session required' })

    if (p === '/resync') {
      try {
        const old = sessions.get(id)
        if (old) { try { old.sock?.end(undefined) } catch (e) {} sessions.delete(id) }
        const dir = path.join(SESS_DIR, id)
        if (fs.existsSync(dir)) for (const f of fs.readdirSync(dir)) {
          if (/app-state-sync-key/i.test(f) || f === 'wa-names.json') fs.unlinkSync(path.join(dir, f))
        }
      } catch (e) {}
      const rec = await openSession(id)
      return sendJson(res, 200, { ok: true, state: rec.state })
    }
    if (p === '/new') {
      const rec = await openSession(id)
      return sendJson(res, 200, { state: rec.state, qr: rec.qr || '', connected: rec.state === 'connected', user: rec.user })
    }
    if (p === '/status') {
      if (!sessions.has(id)) {
        try { if (fs.existsSync(path.join(SESS_DIR, id, 'creds.json'))) await openSession(id) } catch (e) {}
      }
      const rec = sessions.get(id)
      return sendJson(res, 200, rec ? {
        state: rec.state, connected: rec.state === 'connected', user: rec.user,
        chats: rec.chats.size, contacts: rec.contacts.size, boot: BOOT_TS,
        bot: { enabled: !!(rec.bot && rec.bot.enabled), rules: (rec.bot && rec.bot.rules || []).length }
      } : { state: 'none', connected: false, bot: { enabled: false, rules: 0 } })
    }
    if (p === '/bot' && req.method === 'POST') {
      let body = ''
      req.on('data', (c) => { body += c; if (body.length > 40000) req.destroy() })
      req.on('end', () => {
        try {
          const j = JSON.parse(body || '{}')
          const cur = loadBot(id)
          const bot = {
            enabled: typeof j.enabled === 'boolean' ? j.enabled : cur.enabled,
            reply: (j.reply !== undefined && j.reply !== null) ? String(j.reply).slice(0, 1500) : cur.reply,
            rules: Array.isArray(j.rules) ? j.rules.slice(0, 20).map(r => ({
              match: String(r.match || '').slice(0, 80), reply: String(r.reply || '').slice(0, 800) })) : cur.rules,
            skipGroups: (j.skipGroups !== undefined && j.skipGroups !== null) ? !!j.skipGroups : cur.skipGroups,
            gapSeconds: Number(j.gapSeconds || cur.gapSeconds || 90)
          }
          saveBot(id, bot)
          const rec = sessions.get(id)
          if (rec) rec.bot = bot
          return sendJson(res, 200, { ok: true, bot })
        } catch (e) { return sendJson(res, 500, { error: String(e).slice(0, 160) }) }
      })
      return
    }
    if (p === '/bot') {
      return sendJson(res, 200, { ok: true, bot: loadBot(id) })
    }
    if (p === '/action' && req.method === 'POST') {
      let body = ''
      req.on('data', (c) => { body += c; if (body.length > 200000) req.destroy() })
      req.on('end', async () => {
        try {
          const j = JSON.parse(body || '{}')
          const rec = sessions.get(id)
          if (!rec || rec.state !== 'connected') return sendJson(res, 409, { error: 'not connected' })
          const r = await doAction(rec, j)
          return sendJson(res, r.error ? 400 : 200, r)
        } catch (e) { return sendJson(res, 500, { error: String(e).slice(0, 160) }) }
      })
      return
    }
    if (p === '/send' && req.method === 'POST') {
      let body = ''
      req.on('data', (c) => { body += c; if (body.length > 200000) req.destroy() })
      req.on('end', async () => {
        try {
          const j = JSON.parse(body || '{}')
          const rec = sessions.get(id)
          if (!rec || rec.state !== 'connected') return sendJson(res, 409, { error: 'not connected' })
          const r = await doAction(rec, { kind: 'send', to: j.to, text: j.text, image_url: j.image_url, caption: j.caption })
          return sendJson(res, r.error ? 400 : 200, r)
        } catch (e) { return sendJson(res, 500, { error: String(e).slice(0, 160) }) }
      })
      return
    }
    if (p === '/chats') {
      const rec = sessions.get(id)
      if (!rec) return sendJson(res, 200, { chats: [] })
      const list = [...rec.chats.values()].sort((a, b) => (b.ts || 0) - (a.ts || 0)).slice(0, 30)
      let metaFetches = 0
      for (const ch of list) {
        if (!ch.name) ch.name = rec.contacts.get(ch.id) || ''
        if (!ch.name && String(ch.id).endsWith('@g.us') && metaFetches < 5) {
          metaFetches++
          try {
            const meta = await Promise.race([
              rec.sock.groupMetadata(ch.id),
              new Promise((res) => setTimeout(() => res(null), 3500)),
            ])
            if (meta && meta.subject) { ch.name = meta.subject; rec.chats.get(ch.id).name = meta.subject }
          } catch (e) {}
        }
      }
      for (const ch of list) {
        try {
          const msgs = rec.msgs.get(ch.id) || []
          const last = msgs[msgs.length - 1]
          if (last && last.text) ch.preview = String(last.text).slice(0, 90)
        } catch (e) {}
      }
      return sendJson(res, 200, { chats: list })
    }
    if (p === '/contacts') {
      const rec = sessions.get(id)
      const q = String(u.searchParams.get('q') || '').toLowerCase()
      const out = []
      if (rec) {
        for (const [jid, nm] of rec.contacts) {
          if (q && !String(nm).toLowerCase().includes(q) && !jid.includes(q)) continue
          out.push({ id: jid, name: nm })
          if (out.length >= 50) break
        }
      }
      out.sort((a, b) => String(a.name).localeCompare(String(b.name)))
      return sendJson(res, 200, { contacts: out })
    }
    if (p === '/messages') {
      const rec = sessions.get(id)
      const to = u.searchParams.get('to') || ''
      const jj = rec ? toJid(rec, to) : null
      const list = rec ? (rec.msgs.get(jj || to) || []) : []
      return sendJson(res, 200, { messages: list.slice(-20) })
    }
    if (p === '/logout' && req.method === 'POST') {
      const rec = sessions.get(id)
      try { if (rec?.sock) await rec.sock.logout() } catch (e) {}
      try { fs.rmSync(path.join(SESS_DIR, id), { recursive: true, force: true }) } catch (e) {}
      sessions.delete(id)
      return sendJson(res, 200, { ok: true })
    }
    if (p === '/credsfiles') {
      const dir = path.join(SESS_DIR, id)
      const out = {}
      try {
        for (const f of fs.readdirSync(dir)) out[f] = fs.readFileSync(path.join(dir, f), 'utf8')
      } catch (e) {}
      return sendJson(res, 200, { files: out })
    }
    if (p === '/restore' && req.method === 'POST') {
      let body = ''
      req.on('data', (c) => { body += c; if (body.length > 5000000) req.destroy() })
      req.on('end', () => {
        try {
          const j = JSON.parse(body || '{}')
          const dir = path.join(SESS_DIR, id)
          fs.mkdirSync(dir, { recursive: true })
          for (const [f, content] of Object.entries(j.files || {})) {
            if (f.includes('/') || f.includes('..')) continue
            fs.writeFileSync(path.join(dir, f), String(content))
          }
          return sendJson(res, 200, { ok: true })
        } catch (e) { return sendJson(res, 500, { error: String(e).slice(0, 160) }) }
      })
      return
    }
    return sendJson(res, 404, { error: 'not found' })
  } catch (e) {
    return sendJson(res, 500, { error: String(e).slice(0, 160) })
  }
}

http.createServer((req, res) => { handle(req, res) }).listen(PORT, '127.0.0.1', () => {
  console.log('wa-bridge on 127.0.0.1:' + PORT)
})
