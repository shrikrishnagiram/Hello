// Minimal service worker: makes the app installable and lets the shell open even
// when the local server is stopped (the page then explains how to start it).
// Market data (/api/*) is NEVER cached -- always fetched live.
const SHELL = "strangle-shell-v1";
const FILES = ["/", "/manifest.webmanifest", "/icon-192.png", "/icon.svg"];
self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== SHELL).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  // network first, cache fallback
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok && e.request.method === "GET") { const cp = r.clone(); caches.open(SHELL).then(c => c.put(e.request, cp)); }
    return r;
  }).catch(() => caches.match(e.request).then(r => r || caches.match("/"))));
});
