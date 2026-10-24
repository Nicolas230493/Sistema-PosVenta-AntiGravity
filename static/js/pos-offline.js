/*
 * SPEC-SYNC-05 / SPEC-PWA-11: POS offline-first.
 * - IndexedDB "ImpulsoPOS_DB":
 *   · catalog_cache: copia local del catálogo (id, name, sku, price, unit, stock).
 *   · offline_sales_queue: ventas sin conexión (client_uuid, items, payments, total_amount, created_at).
 * - Semáforo en la barra superior: Conectado / Modo Sin Conexión / X ventas por sincronizar.
 * - Sincronización contra /api/sales/sync/ al volver la conexión, cada 30 s y con "Sincronizar ahora".
 */
(function () {
    'use strict';

    const cfg = window.POS_OFFLINE_CONFIG || {};
    const DB_NAME = 'ImpulsoPOS_DB';
    const DB_VERSION = 1;
    const CATALOG_STORE = 'catalog_cache';
    const QUEUE_STORE = 'offline_sales_queue';
    const LEGACY_DB_NAME = 'impulso-pos';  // SPEC-SYNC-05 (v1), se migra y elimina
    const SALE_TIMEOUT_MS = 10000;
    const SYNC_BATCH_SIZE = 50;
    const SYNC_INTERVAL_MS = 30000;

    const form = document.getElementById('pos-form');
    const ui = {
        netBadge: document.getElementById('pos-net-badge'),
        netText: document.getElementById('pos-net-text'),
        pendingBadge: document.getElementById('pos-pending-badge'),
        pendingText: document.getElementById('pos-pending-text'),
        syncButton: document.getElementById('pos-sync-now'),
        errorAlert: document.getElementById('offline-indicator'),
        errorText: document.getElementById('offline-indicator-text'),
    };
    if (!form || !('indexedDB' in window)) return;

    let syncing = false;
    let submitting = false;
    let syncError = '';  // último error de sincronización a nivel lote (sesión vencida, 403, 5xx)

    // ---------- IndexedDB ----------
    function openDB() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open(DB_NAME, DB_VERSION);
            request.onupgradeneeded = () => {
                const db = request.result;
                if (!db.objectStoreNames.contains(CATALOG_STORE)) {
                    db.createObjectStore(CATALOG_STORE, { keyPath: 'id' });
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

    // Pasa las ventas pendientes de la base anterior ("impulso-pos") a ImpulsoPOS_DB.
    // Si la base vieja no existe, se aborta su creación y no queda nada en el equipo.
    function readLegacyQueue() {
        return new Promise((resolve) => {
            let existed = true;
            const request = indexedDB.open(LEGACY_DB_NAME);
            request.onupgradeneeded = (event) => {
                if (event.oldVersion === 0) { existed = false; request.transaction.abort(); }
            };
            request.onerror = () => resolve(null);
            request.onsuccess = () => {
                const db = request.result;
                if (!existed || !db.objectStoreNames.contains(QUEUE_STORE)) { db.close(); resolve([]); return; }
                const getAll = db.transaction(QUEUE_STORE, 'readonly').objectStore(QUEUE_STORE).getAll();
                getAll.onsuccess = () => { db.close(); resolve(getAll.result || []); };
                getAll.onerror = () => { db.close(); resolve(null); };
            };
        });
    }

    const legacyMigration = (async () => {
        try {
            const legacy = await readLegacyQueue();
            if (legacy === null) return;  // no se pudo leer: se conserva para el próximo intento
            for (const entry of legacy) await queue.put(normalizeEntry(entry));
            indexedDB.deleteDatabase(LEGACY_DB_NAME);
        } catch (e) { /* se reintenta en la próxima carga */ }
    })();

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

    // El HTML del POS puede venir de la caché con el token de una sesión anterior (Django lo
    // rota en cada login): se reemplaza por el vigente que entrega el catálogo.
    function setCsrfToken(token) {
        if (!token) return;
        document.querySelectorAll('input[name="csrfmiddlewaretoken"]').forEach((input) => { input.value = token; });
    }

    function currentTotal() {
        const el = document.getElementById('total-amount');
        const value = parseFloat(el ? el.textContent : '');
        return Number.isFinite(value) ? value.toFixed(2) : '0.00';
    }

    // Ventas guardadas por la versión anterior usaban "cart" en lugar de "items"
    function normalizeEntry(entry) {
        const { cart, ...rest } = entry;
        return { ...rest, items: entry.items || cart || [] };
    }

    function saleFromForm(formData, clientUuid) {
        let items = [];
        try { items = JSON.parse(formData.get('cart_data') || '[]'); } catch (e) { items = []; }
        const totalAmount = currentTotal();
        const methodId = formData.get('payment_method');
        // SPEC-POS-08: pagos mixtos como array. Con un único medio el servidor cobra el total que
        // calcule (promos incluidas), por eso solo se le envía el método (single_payment).
        let payments = null;
        try { payments = JSON.parse(formData.get('payments_data') || 'null'); } catch (e) { payments = null; }
        const singlePayment = !Array.isArray(payments) || !payments.length;
        return {
            client_uuid: clientUuid,
            created_at: new Date().toISOString(),
            items: items,
            payments: singlePayment ? [{ method_id: methodId, amount: totalAmount, reference: '' }] : payments,
            single_payment: singlePayment,
            total_amount: totalAmount,
            customer_id: formData.get('customer_id'),
            payment_method_id: methodId,
            status: formData.get('sale_status') || 'COMPLETED',
            price_list: formData.get('price_list'),
            discount_amount: formData.get('discount_amount') || '0',
            surcharge_amount: formData.get('surcharge_amount') || '0',
            points_redeemed: formData.get('points_redeemed') || '0',
        };
    }

    function syncPayload(entry) {
        const sale = normalizeEntry(entry);
        const { last_error, single_payment, payments, ...rest } = sale;
        return single_payment || !Array.isArray(payments) ? rest : { ...rest, payments: payments };
    }

    // ---------- Semáforo de estado ----------
    function setBadge(el, classes) {
        if (el) el.className = `badge rounded-pill px-3 py-2 ${classes}`;
    }

    async function renderIndicator() {
        let pending = [];
        try { pending = await queue.all(); } catch (e) { /* IndexedDB no disponible */ }
        const failed = pending.filter((s) => s.last_error);

        if (navigator.onLine) {
            setBadge(ui.netBadge, 'bg-success-subtle text-success-emphasis fw-normal');
            if (ui.netText) ui.netText.textContent = 'Conectado';
        } else {
            setBadge(ui.netBadge, 'bg-warning text-dark');
            if (ui.netText) ui.netText.textContent = 'Modo Sin Conexión (Operando en Local)';
        }

        if (ui.pendingBadge) {
            ui.pendingBadge.classList.toggle('d-none', !pending.length);
            if (ui.pendingText) {
                ui.pendingText.textContent = pending.length === 1
                    ? '1 venta por sincronizar'
                    : `${pending.length} ventas por sincronizar`;
            }
        }
        if (ui.syncButton) {
            ui.syncButton.classList.toggle('d-none', !pending.length);
            ui.syncButton.disabled = syncing || !navigator.onLine;
            ui.syncButton.innerHTML = syncing
                ? '<span class="spinner-border spinner-border-sm me-1"></span>Sincronizando…'
                : '<i class="bi bi-arrow-repeat me-1"></i>Sincronizar ahora';
        }
        if (ui.errorAlert) {
            const messages = [];
            if (syncError && pending.length && navigator.onLine) messages.push(syncError);
            if (failed.length === 1) messages.push(`1 venta no se pudo sincronizar. Error: ${failed[0].last_error}`);
            if (failed.length > 1) messages.push(`${failed.length} ventas no se pudieron sincronizar. Último error: ${failed[0].last_error}`);
            ui.errorAlert.classList.toggle('d-none', !messages.length);
            if (ui.errorText) ui.errorText.textContent = messages.join(' · ');
        }
    }

    // ---------- Catálogo local ----------
    // Actualiza catálogo y token CSRF. Devuelve false si el servidor no respondió.
    async function refreshCatalog() {
        if (!navigator.onLine || !cfg.catalogUrl) return false;
        try {
            const response = await fetch(cfg.catalogUrl, { credentials: 'same-origin', redirect: 'manual', cache: 'no-store' });
            if (!response.ok) return false;
            const data = await response.json();
            setCsrfToken(data.csrf_token);
            await withStore(CATALOG_STORE, 'readwrite', (s) => {
                s.clear();
                (data.products || []).forEach((p) => s.put(p));
            });
            return true;
        } catch (e) { return false; /* sin conexión: se conserva la copia anterior */ }
    }

    // ---------- Ventas ----------
    async function saveOffline(sale) {
        await queue.put(sale);
        if (typeof window.posResetSale === 'function') window.posResetSale();
        await renderIndicator();
        alert('Sin conexión: la venta se guardó en este equipo y se sincronizará automáticamente al volver internet.');
    }

    async function postSale(formData) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), SALE_TIMEOUT_MS);
        try {
            return await fetch(form.action || cfg.posUrl, {
                method: 'POST',
                body: formData,
                credentials: 'same-origin',
                headers: { 'X-POS-Ajax': '1' },
                signal: controller.signal,
            });
        } finally {
            clearTimeout(timer);
        }
    }

    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        if (submitting) return;

        const clientUuid = uuidv4();
        document.getElementById('client-uuid').value = clientUuid;
        const formData = new FormData(form);
        const sale = saleFromForm(formData, clientUuid);
        if (!sale.items.length) {
            alert('El carrito está vacío.');
            return;
        }

        submitting = true;
        try {
            if (!navigator.onLine) {
                await saveOffline(sale);
                return;
            }
            let response = await postSale(formData);
            if (response.status === 403) {
                // Token CSRF vencido (página abierta desde la caché tras un nuevo login):
                // se pide uno vigente y se reintenta una vez con el mismo client_uuid.
                const usedToken = formData.get('csrfmiddlewaretoken');
                if (!(await refreshCatalog())) {
                    await saveOffline(sale);  // sin token nuevo: queda en cola y se sincroniza después
                    return;
                }
                // Si el token que se usó ya era el vigente, el 403 es falta de permisos: no se reintenta
                if (csrfToken() !== usedToken) response = await postSale(new FormData(form));
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
            alert(response.status === 403
                ? 'El servidor rechazó la venta (403): su usuario no tiene permiso para vender. Consulte al administrador.'
                : `No se pudo registrar la venta (error ${response.status}). Recargue la página e intente nuevamente.`);
        } catch (error) {
            // Timeout o fallo de red
            await saveOffline(sale);
        } finally {
            submitting = false;
        }
    });

    // ---------- Sincronización ----------
    class SyncHttpError extends Error {
        constructor(response) {
            let message;
            if (response.type === 'opaqueredirect' || response.status === 0) {
                message = 'La sesión expiró: inicie sesión nuevamente para sincronizar las ventas guardadas.';
            } else if (response.status === 403) {
                message = 'El servidor rechazó la sincronización (403). Recargue el POS; si persiste, revise los permisos del usuario.';
            } else {
                message = `Error del servidor al sincronizar (HTTP ${response.status}). Se reintenta automáticamente.`;
            }
            super(message);
        }
    }

    async function syncQueue() {
        if (syncing || !navigator.onLine || !cfg.syncUrl) return;
        await legacyMigration;
        let pending;
        try { pending = await queue.all(); } catch (e) { return; }
        if (!pending.length) { renderIndicator(); return; }

        syncing = true;
        renderIndicator();
        try {
            pending.sort((a, b) => (a.created_at < b.created_at ? -1 : 1));
            for (let start = 0; start < pending.length; start += SYNC_BATCH_SIZE) {
                const batch = pending.slice(start, start + SYNC_BATCH_SIZE);
                let usedToken;
                const send = () => {
                    usedToken = csrfToken();
                    return fetch(cfg.syncUrl, {
                        method: 'POST',
                        credentials: 'same-origin',
                        redirect: 'manual',
                        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': usedToken },
                        body: JSON.stringify({ sales: batch.map(syncPayload) }),
                    });
                };
                let response = await send();
                if (response.status === 403 && (await refreshCatalog()) && csrfToken() !== usedToken) {
                    response = await send();
                }
                if (!response.ok) throw new SyncHttpError(response);
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
            }
            syncError = '';
        } catch (error) {
            // Se reintenta en el próximo ciclo; el motivo queda visible en el POS
            syncError = error instanceof SyncHttpError
                ? error.message
                : 'No se pudo contactar al servidor para sincronizar. Se reintenta cada 30 segundos.';
        } finally {
            syncing = false;
            renderIndicator();
        }
    }

    if (ui.syncButton) {
        ui.syncButton.addEventListener('click', () => {
            if (!navigator.onLine) {
                alert('Todavía no hay conexión. Las ventas se sincronizarán solas al volver internet.');
                return;
            }
            syncQueue();
        });
    }

    // ---------- Service Worker ----------
    async function registerServiceWorker() {
        if (!('serviceWorker' in navigator) || !cfg.swUrl) return;
        try {
            // Las instalaciones previas registraban /pos/sw.js con alcance /pos/: se reemplazan por /sw.js ('/')
            const scopeRoot = new URL('/', window.location.href).href;
            for (const registration of await navigator.serviceWorker.getRegistrations()) {
                if (registration.scope !== scopeRoot) await registration.unregister();
            }
            await navigator.serviceWorker.register(cfg.swUrl, { scope: '/' });
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

    // Primero el catálogo (trae el token CSRF vigente), después la sincronización
    window.addEventListener('online', () => { renderIndicator(); refreshCatalog().then(syncQueue); });
    window.addEventListener('offline', renderIndicator);
    setInterval(syncQueue, SYNC_INTERVAL_MS);

    registerServiceWorker();
    legacyMigration.then(renderIndicator);
    refreshCatalog().then(syncQueue);
})();
