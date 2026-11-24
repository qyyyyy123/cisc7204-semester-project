/* ============================================================
   map.js — Leaflet map setup + fence net-flow layer
   ============================================================ */
const BikeMap = (function () {
    let map = null;
    let layerGroup = null;
    let baseLayer = null;
    const XIAMEN = [24.485, 118.13];

    const TILES = {
        dark: {
            url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
            opts: { subdomains: 'abcd', maxZoom: 19, attribution: '&copy; OpenStreetMap &copy; CARTO' }
        },
        amap: {
            // lang=en keeps the label rendering in English
            url: 'https://webrd0{s}.is.autonavi.com/appmaptile?lang=en&size=1&scale=1&style=8&x={x}&y={y}&z={z}',
            opts: { subdomains: '1234', maxZoom: 18, attribution: '&copy; Amap' }
        }
    };

    function init(domId) {
        map = L.map(domId, {
            center: XIAMEN,
            zoom: 12,
            zoomControl: true,
            preferCanvas: true,          // canvas renderer stays smooth with thousands of points
            attributionControl: true
        });
        layerGroup = L.layerGroup().addTo(map);
        switchLayer('dark');
        return map;
    }

    function switchLayer(kind) {
        if (!map) return;
        if (baseLayer) { map.removeLayer(baseLayer); baseLayer = null; }
        const conf = TILES[kind];
        if (!conf) return;
        baseLayer = L.tileLayer(conf.url, conf.opts);
        baseLayer.on('tileerror', () => {
            // Degrade silently when the basemap is unreachable; markers stay visible
            console.warn('Basemap failed to load, falling back to vector-only layer');
        });
        baseLayer.addTo(map);
    }

    /**
     * Colour: negative = blue (shortage), positive = red (surplus).
     * Magnitude drives opacity.
     */
    function colorOf(v, maxAbs) {
        const t = Math.min(1, Math.abs(v) / (maxAbs || 1));
        if (Math.abs(v) < 0.5) return { fill: '#5d6b86', op: 0.35 };
        if (v > 0) {
            // surplus: orange-red -> pure red
            return { fill: `rgba(255, ${Math.round(110 - 43 * t)}, ${Math.round(120 - 26 * t)}, ${0.35 + 0.55 * t})` };
        }
        // shortage: light blue -> deep blue
        return { fill: `rgba(${Math.round(120 - 50 * t)}, ${Math.round(190 - 30 * t)}, 255, ${0.35 + 0.55 * t})` };
    }

    /**
     * Render fence net_flow
     * @param {Array} rows [{fence_id,latitude,longitude,net_flow,borrow_count,return_count,...}]
     */
    function render(rows) {
        if (!map) return;
        layerGroup.clearLayers();

        const valid = (rows || []).filter(r => r.latitude && r.longitude);
        if (!valid.length) return;

        // Only draw the most significant points to avoid over-plotting
        const MAX_POINTS = 4000;
        let top = valid;
        if (valid.length > MAX_POINTS) {
            top = valid.slice().sort((a, b) => Math.abs(b.net_flow) - Math.abs(a.net_flow)).slice(0, MAX_POINTS);
        }

        const maxAbs = Math.max(1, ...top.map(r => Math.abs(r.net_flow)));
        const geoms = [];
        top.forEach(r => {
            const c = colorOf(r.net_flow, maxAbs);
            const radius = 3 + 9 * Math.sqrt(Math.abs(r.net_flow) / maxAbs);
            const mk = L.circleMarker([r.latitude, r.longitude], {
                radius: radius,
                color: 'rgba(255,255,255,.25)',
                weight: 0.5,
                fillColor: c.fill,
                fillOpacity: c.op
            });
            mk.bindTooltip(
                `<b>${r.fence_id}</b><br>Net flow ${r.net_flow > 0 ? '+' : ''}${r.net_flow}<br>` +
                `Borrows ${r.borrow_count} &middot; Returns ${r.return_count}`,
                { direction: 'top', opacity: .95 }
            );
            geoms.push(mk);
        });

        geoms.forEach(g => layerGroup.addLayer(g));
    }

    function flyToActive(rows) {
        if (!map || !rows || !rows.length) return;
        const lats = rows.map(r => r.latitude), lngs = rows.map(r => r.longitude);
        map.fitBounds([[Math.min(...lats), Math.min(...lngs)], [Math.max(...lats), Math.max(...lngs)]], { padding: [30, 30] });
    }

    return { init, render, switchLayer, flyToActive, getMap: () => map };
})();
