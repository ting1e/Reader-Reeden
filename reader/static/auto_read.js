/* Shared speed mapping and fractional, elapsed-time scrolling. */
(function (root) {
    'use strict';
    var speeds = [], i;
    for (i = 2; i <= 20; i++) speeds.push(i);
    for (i = 22; i <= 60; i += 2) speeds.push(i);
    for (i = 70; i <= 100; i += 10) speeds.push(i);
    for (i = 125; i <= 250; i += 25) speeds.push(i);
    function index(speed) {
        var best = 0;
        speeds.forEach(function (value, n) {
            if (Math.abs(value - speed) < Math.abs(speeds[best] - speed)) best = n;
        });
        return best;
    }
    function load(storage) {
        var value = Number(storage.getItem('auto_read_speed_px_s'));
        if (!(value > 0) || !isFinite(value)) {
            var legacy = Number(storage.getItem('auto_read_speed'));
            value = legacy > 0 && isFinite(legacy) ? legacy * 60 : 20;
        }
        value = speeds[index(value)];
        storage.setItem('auto_read_speed_px_s', value);
        return value;
    }
    function Motion() { this.reset(); }
    Motion.prototype.reset = function () {
        this.last = null; this.remainder = 0; this.element = null; this.position = null;
    };
    Motion.prototype.step = function (element, speed, now) {
        if (this.element !== element || (this.position !== null && Math.abs(element.scrollTop - this.position) > 0.01)) {
            this.reset(); this.element = element;
        }
        if (this.last === null) { this.last = now; this.position = element.scrollTop; return; }
        var elapsed = now - this.last;
        this.last = now;
        // Hidden tabs or long stalls must not turn into a large jump on return.
        if (elapsed < 0 || elapsed > 250) { this.remainder = 0; return; }
        this.remainder += speed * elapsed / 1000;
        var pixels = Math.floor(this.remainder + 1e-9);
        if (pixels > 0) { element.scrollTop += pixels; this.remainder -= pixels; }
        this.position = element.scrollTop;
    };
    root.AutoRead = { speeds: speeds, index: index, load: load, Motion: Motion };
})(typeof window === 'undefined' ? globalThis : window);
