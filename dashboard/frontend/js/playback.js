/* ============================================================
   playback.js — playback controls: play / pause / speed / progress
   1x = advance one 30-minute window every 1.5 seconds
   ============================================================ */
const BikePlay = (function () {
    const BASE_INTERVAL = 1500;      // ms per window (1x)
    let windows = [];
    let index = 0;
    let playing = false;
    let speed = 2;
    let handle = null;
    let seekCb = () => { };
    let stateCb = () => { };

    function setWindows(list) {
        windows = list || [];
        if (index >= windows.length) index = Math.max(0, windows.length - 1);
    }

    function emitState() {
        stateCb(playing);
    }

    function schedule() {
        clearTimeout(handle);
        if (!playing) return;
        handle = setTimeout(() => {
            if (!playing) return;
            let next = index + 1;
            if (next >= windows.length) next = 0;      // loop back to the start
            seek(next);
            schedule();
        }, BASE_INTERVAL / speed);
    }

    function seek(i) {
        index = Math.max(0, Math.min(i, windows.length - 1));
        seekCb(index);
    }

    function play() {
        if (!windows.length) return;
        playing = true;
        emitState();
        schedule();
    }

    function pause() {
        playing = false;
        clearTimeout(handle);
        emitState();
    }

    function toggle() { playing ? pause() : play(); }

    function setSpeed(s) {
        speed = s;
        if (playing) schedule();
    }

    return {
        setWindows, seek, play, pause, toggle, setSpeed,
        onSeek: (cb) => { seekCb = cb; },
        onPlayState: (cb) => { stateCb = cb; },
        getIndex: () => index,
        isPlaying: () => playing
    };
})();
