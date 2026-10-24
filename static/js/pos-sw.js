/*
 * SPEC-SYNC-05 / SPEC-PWA-11: Service Worker del POS, servido en /sw.js con alcance '/'.
 * - App Shell:
 *   · HTML del POS (/pos/): network-first con tiempo límite y respaldo en caché. El HTML lleva el
 *     token CSRF, la caja abierta y precios vigentes, por eso nunca se sirve viejo si hay red.
 *   · Estilos, scripts (incluido pos-offline.js) y fuentes que pide la pantalla del POS:
 *     stale-while-revalidate. En producción los archivos llevan hash (ManifestStaticFilesStorage);
 *     en desarrollo (DEV_MODE) los propios van network-first para ver los cambios al instante.
 * - Catálogo (/pos/api/catalog/): network-first con respaldo en caché (se actualiza en cada
 *   apertura del POS con conexión).
 * El resto de las páginas del sistema no usan la caché del POS.
 */
const DEV_MODE = false;  // la vista sales.views.service_worker lo pone en true con DEBUG=True
const CACHE_NAME = 'impulso-pos-v3';
const POS_PATH = '/pos/';
const CATALOG_PATH = `${POS_PATH}api/catalog/`;
const PAGE_NETWORK_TIMEOUT_MS = 4000;

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

function offlinePage() {
    return new Response(
        '<h1>Sin conexión</h1><p>Abrí el POS al menos una vez con internet para habilitar el modo sin conexión.</p>',
        { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
    );
}

async function networkFirstPage(request) {
    const cache = await caches.open(CACHE_NAME);
    const network = fetch(request).then((response) => {
        if (isCacheablePage(response)) cache.put(POS_PATH, response.clone());
        return response;
    });
    network.catch(() => { /* se maneja abajo; evita rechazos no capturados si ganó la caché */ });

    // Con internet lento o intermitente no se deja al cajero esperando: tras el tiempo límite
    // se muestra la copia local (la respuesta de red igual actualiza la caché al llegar).
    let timer;
    const timeout = new Promise((resolve) => { timer = setTimeout(resolve, PAGE_NETWORK_TIMEOUT_MS); });
    try {
        const first = await Promise.race([network, timeout]);
        if (first) return first;
        const cached = await cache.match(POS_PATH);
        return cached || await network;
    } catch (error) {
        return (await cache.match(POS_PATH)) || offlinePage();
    } finally {
        clearTimeout(timer);
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

async function isPosClient(clientId) {
    if (!clientId) return false;
    const client = await self.clients.get(clientId);
    return Boolean(client) && new URL(client.url).pathname === POS_PATH;
}

async function posAsset(event, sameOrigin) {
    const request = event.request;
    const fromPos = await isPosClient(event.clientId).catch(() => false);
    if (!fromPos) return fetch(request);  // otras pantallas del sistema: sin caché del POS
    return DEV_MODE && sameOrigin ? networkFirst(request) : staleWhileRevalidate(request);
}

self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.method !== 'GET') return;
    const url = new URL(request.url);
    const sameOrigin = url.origin === self.location.origin;

    if (request.mode === 'navigate') {
        if (sameOrigin && url.pathname === POS_PATH) event.respondWith(networkFirstPage(request));
    } else if (sameOrigin && url.pathname === CATALOG_PATH) {
        event.respondWith(networkFirst(request));
    } else if (['style', 'script', 'font'].includes(request.destination)) {
        event.respondWith(posAsset(event, sameOrigin));
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
        if (!(await cache.match(POS_PATH))) {
            try {
                const response = await fetch(data.posUrl || POS_PATH, { credentials: 'same-origin' });
                if (isCacheablePage(response)) await cache.put(POS_PATH, response);
            } catch (e) { /* sin conexión */ }
        }
    })());
});
