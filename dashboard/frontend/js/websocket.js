/* ============================================================
   websocket.js — backend WS client, detects new time windows landing in MySQL
   ============================================================ */
const BikeWS = (function () {
    let ws = null;
    let timer = null;
    let handlers = {};
    let lastWindow = null;

    function endpoint() {
        const proto = location.protocol === 'https:' ? 'wss' : 'ws';
        const host = (location.protocol === 'file:') ? 'localhost:8000' : location.host;
        return `${proto}://${host}/ws/tidal`;
    }

    function connect(opts) {
        handlers = opts || {};
        openSocket();
        return () => { if (ws) ws.close(); clearInterval(timer); };
    }

    function openSocket() {
        try {
            ws = new WebSocket(endpoint());
        } catch (e) {
            handlers.onClose && handlers.onClose(e);
            scheduleReconnect();
            return;
        }

        ws.onopen = () => {
            handlers.onOpen && handlers.onOpen();
            if (timer) { clearInterval(timer); timer = null; }
        };

        ws.onmessage = (evt) => {
            try {
                const msg = JSON.parse(evt.data);
                if (msg.type === 'new_window' && msg.window_start !== lastWindow) {
                    lastWindow = msg.window_start;
                    handlers.onWindow && handlers.onWindow(msg.window_start);
                }
            } catch (e) { /* ignore malformed messages */ }
        };

        ws.onclose = () => {
            handlers.onClose && handlers.onClose();
            scheduleReconnect();
        };

        ws.onerror = () => { /* reconnection is handled centrally by onclose */ };
    }

    function scheduleReconnect() {
        if (timer) return;
        timer = setInterval(() => {
            openSocket();
        }, 4000);
    }

    return { connect, getState: () => (ws ? ws.readyState : -1) };
})();
