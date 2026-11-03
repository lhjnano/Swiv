// swiv service worker: an installed app that cannot reach the server shows a
// connect notice instead of a browser error page. Nothing else is cached.
const PAGE = '<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">'
  + '<style>body{margin:0;min-height:100dvh;display:grid;place-items:center;background:#141414;'
  + 'color:#eee;font:16px system-ui,sans-serif;text-align:center;padding:0 24px}</style>'
  + '<p>Cannot reach swiv.<br>Connect to the same network or the VPN.</p>';

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", e => {
  e.respondWith(fetch(e.request).catch(
    () => new Response(PAGE, {headers: {"Content-Type": "text/html; charset=utf-8"}})));
});
