/*
 * Herramientas de código de barras compartidas por el POS, el ingreso de stock y la recepción.
 * - Etiquetas de balanza EAN-13 (prefijos 20-29): misma lógica que products/barcodes.py,
 *   en el navegador para que el POS las lea sin conexión.
 * - Lectura con la cámara del celular/tablet: BarcodeDetector nativo cuando existe; si no,
 *   ZXing (se descarga solo al abrir la cámara). La cámara exige https (o localhost).
 */
(function () {
    'use strict';

    const ZXING_URL = 'https://cdn.jsdelivr.net/npm/@zxing/library@0.21.3/umd/index.min.js';
    const NATIVE_FORMATS = ['ean_13', 'ean_8', 'code_128', 'upc_a', 'upc_e'];
    const REPEAT_WINDOW_MS = 1500;

    // ---------- EAN-13 y etiquetas de balanza ----------
    function ean13CheckDigit(first12) {
        let total = 0;
        for (let i = 0; i < 12; i++) total += Number(first12[i]) * (i % 2 ? 3 : 1);
        return (10 - (total % 10)) % 10;
    }

    function isValidEan13(code) {
        return /^\d{13}$/.test(code) && ean13CheckDigit(code.slice(0, 12)) === Number(code[12]);
    }

    /** {plu, value, mode} o null. cfg: {pluDigits: 4|5|6, mode: 'peso'|'importe'} */
    function parseScale(code, cfg) {
        const pluDigits = Number((cfg && cfg.pluDigits) || 5);
        code = String(code || '').trim();
        if (!isValidEan13(code) || code[0] !== '2' || ![4, 5, 6].includes(pluDigits)) return null;
        return {
            plu: Number(code.slice(2, 2 + pluDigits)),
            value: Number(code.slice(2 + pluDigits, 12)),
            mode: (cfg && cfg.mode) || 'peso',
        };
    }

    /** Cantidad (kg o unidades) que representa la etiqueta. product: {price, fractional} */
    function scaleQuantity(label, product) {
        if (label.mode === 'importe') {
            const price = Number(product.price);
            return price > 0 ? Math.round((label.value / price) * 1000) / 1000 : null;
        }
        return product.fractional ? label.value / 1000 : label.value;
    }

    // ---------- Cámara ----------
    function loadScript(src) {
        return new Promise((resolve, reject) => {
            const existing = document.querySelector(`script[src="${src}"]`);
            if (existing && window.ZXing) return resolve();
            const script = document.createElement('script');
            script.src = src;
            script.onload = () => resolve();
            script.onerror = () => reject(new Error('No se pudo descargar el lector de códigos.'));
            document.head.appendChild(script);
        });
    }

    function beep() {
        try {
            const ctx = new (window.AudioContext || window.webkitAudioContext)();
            const osc = ctx.createOscillator();
            osc.frequency.value = 1200;
            osc.connect(ctx.destination);
            osc.start();
            osc.stop(ctx.currentTime + 0.08);
            osc.onended = () => ctx.close();
        } catch (e) { /* sin audio */ }
        if (navigator.vibrate) navigator.vibrate(60);
    }

    function buildOverlay(title) {
        const overlay = document.createElement('div');
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-label', title);
        overlay.style.cssText = 'position:fixed;inset:0;z-index:2000;background:rgba(10,12,20,.85);display:flex;align-items:center;justify-content:center;padding:16px';
        overlay.innerHTML = `
            <div style="background:#fff;color:#141820;border-radius:16px;max-width:520px;width:100%;overflow:hidden;font-family:system-ui,sans-serif">
                <div style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px">
                    <strong data-role="title"></strong>
                    <button type="button" data-role="close" class="btn btn-outline-secondary btn-sm rounded-pill">Cerrar</button>
                </div>
                <div style="position:relative;background:#000">
                    <video data-role="video" playsinline muted style="width:100%;max-height:60vh;display:block;object-fit:cover"></video>
                    <div aria-hidden="true" style="position:absolute;left:10%;right:10%;top:45%;height:2px;background:#ff4d4d;box-shadow:0 0 8px #ff4d4d"></div>
                </div>
                <p data-role="status" aria-live="polite" style="margin:0;padding:12px 16px;font-size:14px">Iniciando cámara…</p>
            </div>`;
        overlay.querySelector('[data-role="title"]').textContent = title;
        document.body.appendChild(overlay);
        return overlay;
    }

    /**
     * Abre la cámara y llama onCode(texto) por cada código leído.
     * continuous=false cierra al primer código (POS); true sigue leyendo (recepción).
     */
    async function openCamera({ onCode, continuous = false, title = 'Escanear con la cámara' } = {}) {
        if (!window.isSecureContext) {
            alert('La cámara solo funciona con conexión segura (https) o desde la misma computadora (localhost).');
            return;
        }
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            alert('Este navegador no permite usar la cámara.');
            return;
        }

        const overlay = buildOverlay(title);
        const video = overlay.querySelector('[data-role="video"]');
        const status = overlay.querySelector('[data-role="status"]');
        let stream = null;
        let timer = null;
        let reader = null;
        let closed = false;
        let last = { code: '', at: 0 };

        function close() {
            if (closed) return;
            closed = true;
            if (timer) clearInterval(timer);
            if (reader) { try { reader.reset(); } catch (e) { /* ya detenido */ } }
            if (stream) stream.getTracks().forEach((t) => t.stop());
            overlay.remove();
        }

        function handle(code) {
            const now = Date.now();
            if (!code || (code === last.code && now - last.at < REPEAT_WINDOW_MS)) return;
            last = { code, at: now };
            beep();
            status.textContent = `Leído: ${code}`;
            if (!continuous) close();
            onCode(code);
        }

        overlay.querySelector('[data-role="close"]').addEventListener('click', close);
        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

        try {
            stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
            if (closed) { stream.getTracks().forEach((t) => t.stop()); return; }
            video.srcObject = stream;
            await video.play();
            status.textContent = 'Apuntá al código de barras.';

            let detector = null;
            if ('BarcodeDetector' in window) {
                const supported = await window.BarcodeDetector.getSupportedFormats();
                const formats = NATIVE_FORMATS.filter((f) => supported.includes(f));
                if (formats.length) detector = new window.BarcodeDetector({ formats });
            }
            if (detector) {
                timer = setInterval(async () => {
                    if (closed || video.readyState < 2) return;
                    try {
                        const found = await detector.detect(video);
                        if (found.length) handle(found[0].rawValue);
                    } catch (e) { /* cuadro sin código */ }
                }, 250);
            } else {
                await loadScript(ZXING_URL);
                if (closed) return;
                const Z = window.ZXing;
                const hints = new Map();
                hints.set(Z.DecodeHintType.POSSIBLE_FORMATS, [
                    Z.BarcodeFormat.EAN_13, Z.BarcodeFormat.EAN_8, Z.BarcodeFormat.CODE_128,
                    Z.BarcodeFormat.UPC_A, Z.BarcodeFormat.UPC_E,
                ]);
                reader = new Z.BrowserMultiFormatReader(hints);
                reader.decodeFromStream(stream, video, (result) => { if (result) handle(result.getText()); });
            }
        } catch (error) {
            status.textContent = error && error.name === 'NotAllowedError'
                ? 'No hay permiso para usar la cámara. Habilitalo en el navegador.'
                : `No se pudo abrir la cámara: ${(error && error.message) || error}`;
        }
    }

    window.ImpulsoBarcode = { ean13CheckDigit, isValidEan13, parseScale, scaleQuantity, openCamera };
})();
