self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => self.clients.claim());
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    fetch(e.request).then(r => {
      const c = caches.open('oracool-v1').then(c => { c.put(e.request, r.clone()); return r; });
      return r;
    }).catch(() => caches.match(e.request))
  );
});
