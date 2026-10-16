/*
 * SPEC-SYNC-05: Service Worker del POS (alcance /pos/).
 * - Interfaz del POS: network-first con respaldo en caché.
 * - Catálogo JSON: network-first.
 * - Estilos, scripts y fuentes: stale-while-revalidate.
 */
const CACHE_NAME = 'impulso-pos-v1';
const SCOPE_PATH = new URL(self.registration.scope).pathname;  // p. ej. /pos/
const POS_PAGE_KEY = SCOPE_PATH;

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
            .then(() => self.clients.claim())
    );
});

function isCacheablePage(response) {
    return response && response.ok && !response.redirected && !response.headers.get('X-POS-No-Cache');
}

async function networkFirstPage(request) {
    const cache = await caches.open(CACHE_NAME);
    try {
        const response = await fetch(request);
        if (isCacheablePage(response)) {
            cache.put(POS_PAGE_KEY, response.clone());
        }
        return response;
    } catch (error) {
        const cached = await cache.match(POS_PAGE_KEY);
        if (cached) return cached;
        return new Response(
            '<h1>Sin conexión</h1><p>Abrí el POS al menos una vez con internet para habilitar el modo sin conexión.</p>',
            { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
        );
    }
}

async function networkFirst(request) {
    const cache = await caches.open(CACHE_NAME);
    try {
        const response = await fetch(request);
        if (response.ok && !response.redirected) cache.put(request, response.clone());
        return response;
    } catch (error) {
        const cached = await cache.match(request);
        if (cached) return cached;
        throw error;
    }
}

async function staleWhileRevalidate(request) {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(request);
    const network = fetch(request)
        .then((response) => {
            if (response.ok || response.type === 'opaque') cache.put(request, response.clone());
            return response;
        })
        .catch(() => cached);
    return cached || network;
}

self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.method !== 'GET') return;
    const url = new URL(request.url);
    const sameOrigin = url.origin === self.location.origin;

    if (request.mode === 'navigate' && sameOrigin && url.pathname === SCOPE_PATH) {
        event.respondWith(networkFirstPage(request));
    } else if (sameOrigin && url.pathname === `${SCOPE_PATH}api/catalog/`) {
        event.respondWith(networkFirst(request));
    } else if (['style', 'script', 'font'].includes(request.destination)) {
        event.respondWith(staleWhileRevalidate(request));
    }
});

// La página informa sus recursos para dejarlos disponibles offline desde la primera visita
self.addEventListener('message', (event) => {
    const data = event.data || {};
    if (data.type !== 'CACHE_ASSETS') return;
    event.waitUntil((async () => {
        const cache = await caches.open(CACHE_NAME);
        for (const asset of data.assets || []) {
            try {
                if (await cache.match(asset)) continue;
                const sameOrigin = new URL(asset).origin === self.location.origin;
                const response = await fetch(asset, { mode: sameOrigin ? 'same-origin' : 'no-cors' });
                if (response.ok || response.type === 'opaque') await cache.put(asset, response);
            } catch (e) { /* recurso no disponible */ }
        }
        // Primera copia de la interfaz del POS (solo si todavía no hay ninguna)
        if (!(await cache.match(POS_PAGE_KEY))) {
            try {
                const response = await fetch(data.posUrl || SCOPE_PATH, { credentials: 'same-origin' });
                if (isCacheablePage(response)) await cache.put(POS_PAGE_KEY, response);
            } catch (e) { /* sin conexión */ }
        }
    })());
});
