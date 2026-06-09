/* Minimal network-only service worker.
   Exists so the app is installable (Add to Home Screen / PWA install prompt);
   it deliberately caches NOTHING — the server's no-cache headers stay the
   single source of freshness, so stale-asset bugs can't come back. */
self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) { e.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", function (e) { e.respondWith(fetch(e.request)); });
