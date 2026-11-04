self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith('oracool-') && k !== 'oracool-v5').map(k => caches.delete(k)))));
  return self.clients.claim();
});
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  if (new URL(e.request.url).pathname.startsWith('/api/')) return;
  /* pages must always come fresh from the network so updates reach every device */
  if (e.request.mode === 'navigate') {
    e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
    return;
  }
  e.respondWith(
    fetch(e.request).then(r => {
      const c = caches.open('oracool-v5').then(c => { c.put(e.request, r.clone()); return r; });
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
