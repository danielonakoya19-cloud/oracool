#!/usr/bin/env node
/* OraCool WhatsApp link bridge — REAL WhatsApp multi-device sessions (the same
 * pairing handshake WhatsApp Web uses). One Node process serves every OraCool
 * user session; the Python server talks to 127.0.0.1 only and proxies to the
 * app. The QR shown to users is ALWAYS the live pairing payload from WhatsApp —
 * never drawn, never faked. Sessions persist as auth folders that the Python
 * server stores in its key-value vault and restores on boot. */
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
const sessions = new Map() // id -> { sock, qr, state, user, chats:Map, msgs:Map, wantQr }

function safeId(id) { return String(id || '').replace(/[^a-zA-Z0-9._-]/g, '').slice(0, 40) }
function jid(to) {
  let n = String(to || '').replace(/[^\d]/g, '')
  if (!n) return null
  return n + '@s.whatsapp.net'
}

async function openSession(id) {
  if (sessions.has(id)) return sessions.get(id)
  const dir = path.join(SESS_DIR, id)
  fs.mkdirSync(dir, { recursive: true })
  const { state, saveCreds } = await useMultiFileAuthState(dir)
  const { version } = await fetchLatestBaileysVersion()
  const rec = { sock: null, qr: '', state: 'connecting', user: null, chats: new Map(), msgs: new Map(), wantQr: true, saveCreds }
  sessions.set(id, rec)
  const sock = makeWASocket({
    version, auth: state, printQRInTerminal: false, logger: silent,
    markOnlineOnConnect: false, syncFullHistory: false,
    generateHighQualityLinkPreview: false
  })
  rec.sock = sock
  sock.ev.on('creds.update', saveCreds)
  sock.ev.on('connection.update', (u) => {
    if (u.qr) { rec.qr = u.qr; rec.state = 'qr' }
    if (u.connection === 'open') {
      rec.state = 'connected'; rec.qr = ''
      try { rec.user = { id: sock.user?.id || '', name: sock.user?.name || '' } } catch (e) {}
    }
    if (u.connection === 'close') {
      const code = u.lastDisconnect?.error?.output?.statusCode
      rec.state = code === DisconnectReason.loggedOut ? 'logged_out' : 'disconnected'
      if (code !== DisconnectReason.loggedOut) {
        // transient drop: reopen so the session survives (multi-device)
        setTimeout(() => { try { openSession(id) } catch (e) {} }, 1500)
        sessions.delete(id)
      } else {
        try { fs.rmSync(path.join(SESS_DIR, id), { recursive: true, force: true }) } catch (e) {}
        sessions.delete(id)
      }
    }
  })
  sock.ev.on('messaging-history.set', ({ chats, contacts, messages }) => {
    try {
      for (const ch of chats || []) rec.chats.set(ch.id, { id: ch.id, name: ch.name || (contacts.find(c => c.id === ch.id)?.name) || '', ts: ch.conversationTimestamp || 0 })
      for (const m of messages || []) {
        const j = m.key?.remoteJid
        if (!j || j === 'status@broadcast') continue
        const list = rec.msgs.get(j) || []
        list.push({ fromMe: !!m.key?.fromMe, ts: Number(m.messageTimestamp || 0), text: msgText(m.message), id: m.key?.id })
        if (list.length > 50) list.shift()
        rec.msgs.set(j, list)
      }
    } catch (e) {}
  })
  sock.ev.on('messages.upsert', ({ messages, type }) => {
    try {
      for (const m of messages || []) {
        const j = m.key?.remoteJid
        if (!j || j === 'status@broadcast') continue
        const list = rec.msgs.get(j) || []
        list.push({ fromMe: !!m.key?.fromMe, ts: Number(m.messageTimestamp || 0), text: msgText(m.message), id: m.key?.id })
        if (list.length > 50) list.shift()
        rec.msgs.set(j, list)
        const nm = rec.chats.get(j) || { id: j, name: '', ts: 0 }
        nm.ts = Math.max(nm.ts, Number(m.messageTimestamp || 0))
        rec.chats.set(j, nm)
      }
    } catch (e) {}
  })
  return rec
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
  const b = JSON.stringify(obj)
  res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
  res.end(b)
}

async function handle(req, res) {
  const u = new URL(req.url, 'http://127.0.0.1')
  const p = u.pathname
  const id = safeId(u.searchParams.get('session') || '')
  try {
    if (p === '/health') return sendJson(res, 200, { ok: true, sessions: sessions.size })
    if (!id) return sendJson(res, 400, { error: 'session required' })

    if (p === '/new') {
      const rec = await openSession(id)
      return sendJson(res, 200, {
        state: rec.state, qr: rec.qr || '',
        connected: rec.state === 'connected', user: rec.user
      })
    }
    if (p === '/status') {
      const rec = sessions.get(id)
      return sendJson(res, 200, rec ? {
        state: rec.state, connected: rec.state === 'connected', user: rec.user,
        chats: rec.chats.size
      } : { state: 'none', connected: false })
    }
    if (p === '/send' && req.method === 'POST') {
      let body = ''
      req.on('data', (c) => { body += c; if (body.length > 20000) req.destroy() })
      req.on('end', async () => {
        try {
          const j = JSON.parse(body || '{}')
          const rec = sessions.get(id)
          if (!rec || rec.state !== 'connected') return sendJson(res, 409, { error: 'not connected' })
          const to = jid(j.to)
          if (!to) return sendJson(res, 400, { error: 'bad recipient' })
          const r = await rec.sock.sendMessage(to, { text: String(j.text || '').slice(0, 4000) })
          return sendJson(res, 200, { ok: true, id: r?.key?.id || '' })
        } catch (e) { return sendJson(res, 500, { error: String(e).slice(0, 160) }) }
      })
      return
    }
    if (p === '/chats') {
      const rec = sessions.get(id)
      if (!rec) return sendJson(res, 200, { chats: [] })
      const list = [...rec.chats.values()].sort((a, b) => (b.ts || 0) - (a.ts || 0)).slice(0, 30)
      return sendJson(res, 200, { chats: list })
    }
    if (p === '/messages') {
      const rec = sessions.get(id)
      const to = u.searchParams.get('to') || ''
      const list = rec ? (rec.msgs.get(to) || rec.msgs.get(jid(to) || '') || []) : []
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
      // the Python server pulls this to vault the session (survives redeploys)
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
