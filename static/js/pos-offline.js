/*
 * SPEC-SYNC-05: POS offline-first.
 * - Copia local del catálogo (IndexedDB: products_store).
 * - Cola de ventas sin conexión (IndexedDB: offline_sales_queue) con client_uuid.
 * - Sincronización automática contra /api/sales/sync/ al volver la conexión.
 */
(function () {
    'use strict';

    const cfg = window.POS_OFFLINE_CONFIG || {};
    const DB_NAME = 'impulso-pos';
    const DB_VERSION = 1;
    const PRODUCTS_STORE = 'products_store';
    const QUEUE_STORE = 'offline_sales_queue';
    const SALE_TIMEOUT_MS = 10000;
    const SYNC_BATCH_SIZE = 50;
    const SYNC_INTERVAL_MS = 30000;

    const form = document.getElementById('pos-form');
    const indicator = document.getElementById('offline-indicator');
    const indicatorText = document.getElementById('offline-indicator-text');
    if (!form || !('indexedDB' in window)) return;

    let syncing = false;
    let submitting = false;

    // ---------- IndexedDB ----------
    function openDB() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, DB_VERSION);
            request.onupgradeneeded = () => {
                const db = request.result;
                if (!db.objectStoreNames.contains(PRODUCTS_STORE)) {
                    db.createObjectStore(PRODUCTS_STORE, { keyPath: 'id' });
                }
                if (!db.objectStoreNames.contains(QUEUE_STORE)) {
                    db.createObjectStore(QUEUE_STORE, { keyPath: 'client_uuid' });
                }
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    async function withStore(storeName, mode, fn) {
        const db = await openDB();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(storeName, mode);
            const store = tx.objectStore(storeName);
            let result;
            Promise.resolve(fn(store)).then((value) => { result = value; });
            tx.oncomplete = () => { db.close(); resolve(result); };
            tx.onerror = () => { db.close(); reject(tx.error); };
            tx.onabort = () => { db.close(); reject(tx.error); };
        });
    }

    function requestToPromise(request) {
        return new Promise((resolve, reject) => {
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }

    const queue = {
        all: () => withStore(QUEUE_STORE, 'readonly', (s) => requestToPromise(s.getAll())),
        put: (sale) => withStore(QUEUE_STORE, 'readwrite', (s) => { s.put(sale); }),
        remove: (uuid) => withStore(QUEUE_STORE, 'readwrite', (s) => { s.delete(uuid); }),
    };

    // ---------- Utilidades ----------
    function uuidv4() {
        if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
        const bytes = crypto.getRandomValues(new Uint8Array(16));
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
        return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }

    function csrfToken() {
        const input = form.querySelector('input[name="csrfmiddlewaretoken"]');
        return input ? input.value : '';
    }

    function saleFromForm(formData, clientUuid) {
        let cart = [];
        try { cart = JSON.parse(formData.get('cart_data') || '[]'); } catch (e) { cart = []; }
        // SPEC-POS-08: pagos mixtos como array; con un único medio se envía solo payment_method_id
        let payments;
        try { payments = JSON.parse(formData.get('payments_data') || 'null') || undefined; } catch (e) { payments = undefined; }
        return {
            client_uuid: clientUuid,
            created_at: new Date().toISOString(),
            cart: cart,
            customer_id: formData.get('customer_id'),
            payment_method_id: formData.get('payment_method'),
            payments: payments,
            status: formData.get('sale_status') || 'COMPLETED',
            price_list: formData.get('price_list'),
            discount_amount: formData.get('discount_amount') || '0',
            surcharge_amount: formData.get('surcharge_amount') || '0',
            points_redeemed: formData.get('points_redeemed') || '0',
        };
    }

    // ---------- Indicador ----------
    async function renderIndicator() {
        if (!indicator) return;
        let pending = [];
        try { pending = await queue.all(); } catch (e) { /* IndexedDB no disponible */ }
        const failed = pending.filter((s) => s.last_error);

        if (!navigator.onLine) {
            indicator.className = 'alert alert-danger border-0 shadow-sm fw-bold sticky-top';
            indicatorText.textContent = `Modo Sin Conexión — ${pending.length} ventas guardadas localmente`;
        } else if (pending.length) {
            indicator.className = 'alert alert-warning border-0 shadow-sm fw-bold sticky-top';
            indicatorText.textContent = failed.length
                ? `${pending.length} ventas pendientes de sincronizar. Último error: ${failed[0].last_error}`
                : `Sincronizando ${pending.length} ventas guardadas localmente…`;
        } else {
            indicator.className = 'alert d-none';
        }
    }

    // ---------- Catálogo local ----------
    async function refreshCatalog() {
        if (!navigator.onLine || !cfg.catalogUrl) return;
        try {
            const response = await fetch(cfg.catalogUrl, { credentials: 'same-origin', redirect: 'manual' });
            if (!response.ok) return;
            const data = await response.json();
            await withStore(PRODUCTS_STORE, 'readwrite', (s) => {
                s.clear();
                (data.products || []).forEach((p) => s.put(p));
            });
        } catch (e) { /* sin conexión: se conserva la copia anterior */ }
    }

    // ---------- Ventas ----------
    async function saveOffline(sale) {
        await queue.put(sale);
        if (typeof window.posResetSale === 'function') window.posResetSale();
        await renderIndicator();
        alert('Sin conexión: la venta se guardó en este equipo y se sincronizará automáticamente al volver internet.');
    }

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        if (submitting) return;

        const clientUuid = uuidv4();
        document.getElementById('client-uuid').value = clientUuid;
        const formData = new FormData(form);
        const sale = saleFromForm(formData, clientUuid);
        if (!sale.cart.length) {
            alert('El carrito está vacío.');
            return;
        }

        submitting = true;
        try {
            if (!navigator.onLine) {
                await saveOffline(sale);
                return;
            }
            const controller = new AbortController();
            const timer = setTimeout(() => controller.abort(), SALE_TIMEOUT_MS);
            let response;
            try {
                response = await fetch(form.action || cfg.posUrl, {
                    method: 'POST',
                    body: formData,
                    credentials: 'same-origin',
                    headers: { 'X-POS-Ajax': '1' },
                    signal: controller.signal,
                });
            } finally {
                clearTimeout(timer);
            }

            const isJson = (response.headers.get('Content-Type') || '').includes('application/json');
            if (isJson && (response.ok || response.status === 400)) {
                // Venta registrada o rechazada con mensaje: recargar para ver el resultado (y el ticket)
                window.location.href = cfg.posUrl;
                return;
            }
            if (response.redirected || response.status >= 500) {
                // Sesión expirada (redirección al login) o servidor caído:
                // se guarda localmente; el client_uuid evita duplicados al sincronizar.
                await saveOffline(sale);
                if (response.redirected) {
                    alert('Su sesión expiró. Vuelva a iniciar sesión para sincronizar las ventas guardadas.');
                    window.location.href = response.url;
                }
                return;
            }
            alert(`No se pudo registrar la venta (error ${response.status}). Recargue la página e intente nuevamente.`);
        } catch (error) {
            // Timeout o fallo de red
            await saveOffline(sale);
        } finally {
            submitting = false;
        }
    });

    // ---------- Sincronización ----------
    async function syncQueue() {
        if (syncing || !navigator.onLine || !cfg.syncUrl) return;
        let pending;
        try { pending = await queue.all(); } catch (e) { return; }
        if (!pending.length) { renderIndicator(); return; }

        syncing = true;
        try {
            pending.sort((a, b) => (a.created_at < b.created_at ? -1 : 1));
            const batch = pending.slice(0, SYNC_BATCH_SIZE);
            const response = await fetch(cfg.syncUrl, {
                method: 'POST',
                credentials: 'same-origin',
                redirect: 'manual',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
                body: JSON.stringify({ sales: batch.map(({ last_error, ...rest }) => rest) }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status || 'redirect'}`);
            const data = await response.json();
            const byUuid = Object.fromEntries(batch.map((s) => [s.client_uuid, s]));
            for (const result of data.results || []) {
                const entry = byUuid[result.client_uuid];
                if (!entry) continue;
                if (result.status === 'created' || result.status === 'duplicate') {
                    await queue.remove(result.client_uuid);
                } else {
                    entry.last_error = result.error || 'Error desconocido';
                    await queue.put(entry);
                }
            }
        } catch (error) {
            /* se reintenta en el próximo ciclo */
        } finally {
            syncing = false;
            renderIndicator();
        }
    }

    // ---------- Service Worker ----------
    async function registerServiceWorker() {
        if (!('serviceWorker' in navigator) || !cfg.swUrl) return;
        try {
            await navigator.serviceWorker.register(cfg.swUrl, { scope: cfg.posUrl });
            const registration = await navigator.serviceWorker.ready;
            const assets = [
                ...Array.from(document.querySelectorAll('script[src]'), (el) => el.src),
                ...Array.from(document.querySelectorAll('link[rel="stylesheet"][href]'), (el) => el.href),
            ];
            if (registration.active) {
                registration.active.postMessage({ type: 'CACHE_ASSETS', assets: assets, posUrl: cfg.posUrl });
            }
        } catch (e) { /* el POS sigue funcionando sin Service Worker */ }
    }

    window.addEventListener('online', () => { renderIndicator(); syncQueue(); refreshCatalog(); });
    window.addEventListener('offline', renderIndicator);
    setInterval(syncQueue, SYNC_INTERVAL_MS);

    registerServiceWorker();
    refreshCatalog();
    renderIndicator();
    syncQueue();
})();
