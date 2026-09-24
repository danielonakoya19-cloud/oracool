self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith('oracool-') && k !== 'oracool-v4').map(k => caches.delete(k)))));
  return self.clients.claim();
});
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  if (new URL(e.request.url).pathname.startsWith('/api/')) return;
  e.respondWith(
    fetch(e.request).then(r => {
      const c = caches.open('oracool-v4').then(c => { c.put(e.request, r.clone()); return r; });
      return r;
    }).catch(() => caches.match(e.request))
  );
});

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = {title: 'OraCool alert', body: (e.data && e.data.text()) || ''}; }
  e.waitUntil(self.registration.showNotification(d.title || 'OraCool alert', {
    body: d.body || '', icon: '/icon-192.png?v=2', badge: '/icon-192.png?v=2', tag: 'oracool'
  }));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then(cs => {
    for (const c of cs) { if ('focus' in c) return c.focus(); }
    return self.clients.openWindow('/app');
  }));
});
