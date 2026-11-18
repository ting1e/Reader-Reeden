const assert = require('node:assert/strict');
require('../reader/static/auto_read.js');
const { speeds, Motion, load } = globalThis.AutoRead;
assert.equal(speeds.length, 49);
assert.equal(speeds[0], 2);
assert.equal(speeds[48], 250);
function distance(speed, hz) {
    let position = 0;
    // Simulate a browser that rounds scrollTop assignments to integer pixels.
    const element = { get scrollTop() { return position; }, set scrollTop(v) { position = Math.round(v); } };
    const motion = new Motion();
    for (let i = 0; i <= hz * 10; i++) motion.step(element, speed, i * 1000 / hz);
    return position;
}
for (const speed of [2, 6, 12, 18, 20, 60, 250]) {
    for (const hz of [20, 60, 120, 144]) assert.ok(Math.abs(distance(speed, hz) - speed * 10) <= 1);
}
// At 2px/s the visual position must advance every frame, not once per half second.
for (const hz of [60, 120, 144]) {
    let position = 0, previous = 0;
    const element = { scrollHeight: 10000, clientHeight: 500,
        get scrollTop() { return position; }, set scrollTop(v) { position = Math.round(v); } };
    const content = { style: { translate: '', willChange: '' } };
    const motion = new Motion();
    for (let i = 0; i <= hz * 2; i++) {
        motion.step(element, 2, i * 1000 / hz, [content]);
        const translated = Number.parseFloat((content.style.translate || '0 0').split(' ')[1]);
        const visual = position - translated;
        assert.ok(Math.abs(visual - 2 * i / hz) < 1e-6);
        if (i > 0) assert.ok(visual > previous);
        previous = visual;
    }
    motion.reset();
    assert.equal(content.style.translate, '');
    assert.equal(content.style.willChange, '');
}
const end = { scrollTop: 99, scrollHeight: 200, clientHeight: 100 };
const endContent = { style: { translate: '0 0px', willChange: 'opacity' } };
const endMotion = new Motion();
endMotion.step(end, 20, 0, [endContent]);
endMotion.step(end, 20, 100, [endContent]);
assert.equal(end.scrollTop, 100);
assert.equal(endContent.style.translate, '0 0px');
endMotion.reset();
assert.equal(endContent.style.willChange, 'opacity');
const element = { scrollTop: 0 }, motion = new Motion();
motion.step(element, 20, 0);
motion.step(element, 20, 100);
assert.equal(element.scrollTop, 2);
motion.step(element, 20, 10000);
assert.equal(element.scrollTop, 2); // No hidden-tab catch-up.
element.scrollTop = 100;
motion.step(element, 20, 10100);
assert.equal(element.scrollTop, 100); // Manual scroll never snaps back.
motion.reset();
motion.step(element, 20, 20000);
assert.equal(element.scrollTop, 100);
function storage(values = {}) { return { getItem: k => values[k] ?? null, setItem: (k, v) => values[k] = String(v) }; }
assert.equal(load(storage()), 20);
assert.equal(load(storage({ auto_read_speed: '0.1' })), 6);
assert.equal(load(storage({ auto_read_speed: '0.2' })), 12);
assert.equal(load(storage({ auto_read_speed: '0.3' })), 18);
assert.equal(load(storage({ auto_read_speed_px_s: '20', auto_read_speed: '2' })), 20);
console.log('Auto-read tests passed: 49 speeds, subpixel frame smoothness, refresh rates, bounds, reset, migration.');
