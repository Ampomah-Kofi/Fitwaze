// FitWaze service worker: keeps the app usable where the signal is weak.
//
// - The page: network first (so a deploy is picked up straight away), and
//   the last copy when there is no connection.
// - The map library and map tiles already seen: from the phone, refreshed in
//   the background, so the map still shows mid-walk without signal.
// - Everything else, including every API call and anything with health data
//   or sign-in in it, is never cached here.
const VERSION = "fitwaze-v1";
const PAGE_CACHE = VERSION + "-page";
const ASSET_CACHE = VERSION + "-assets";
const TILE_CACHE = VERSION + "-tiles";
const MAX_TILES = 300;
const PAGES = ["/", "/demo", "/mobile"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(PAGE_CACHE).then((cache) => cache.add("/")).catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => !key.startsWith(VERSION)).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

async function networkFirstPage(request) {
  const cache = await caches.open(PAGE_CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put("/", response.clone());
    return response;
  } catch (error) {
    return (await cache.match("/")) || Response.error();
  }
}

async function cacheFirst(request, cacheName, limit) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  const refresh = fetch(request).then(async (response) => {
    if (response.ok) {
      await cache.put(request, response.clone());
      if (limit) {
        const keys = await cache.keys();
        for (const key of keys.slice(0, Math.max(0, keys.length - limit))) await cache.delete(key);
      }
    }
    return response;
  }).catch(() => cached || Response.error());
  return cached || refresh;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin === self.location.origin) {
    if (request.mode === "navigate" && PAGES.includes(url.pathname)) event.respondWith(networkFirstPage(request));
    else if (/^\/(icon-\d+\.png|manifest\.webmanifest)$/.test(url.pathname)) event.respondWith(cacheFirst(request, ASSET_CACHE));
    return;  // API calls: always the network, never stored
  }
  if (url.hostname === "unpkg.com" && url.pathname.startsWith("/leaflet@")) {
    event.respondWith(cacheFirst(request, ASSET_CACHE));
  } else if (url.hostname === "tile.openstreetmap.org") {
    event.respondWith(cacheFirst(request, TILE_CACHE, MAX_TILES));
  }
});
