'use strict';

const assert = require('node:assert/strict');
const vm = require('node:vm');

let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { raw += chunk; });
process.stdin.on('end', async () => {
    const { script } = JSON.parse(raw);
    let now = Date.parse('2026-09-10T00:00:00Z');
    const elements = new Map();
    const timers = new Map();
    const requests = [];
    let timerId = 0;
    let payload;
    let pollingStopped = false;

    function element(id) {
        if (!elements.has(id)) {
            elements.set(id, { innerHTML: '', textContent: '', style: {}, disabled: false });
        }
        return elements.get(id);
    }

    class Clock extends Date {
        static now() { return now; }
    }

    const context = vm.createContext({
        console,
        Date: Clock,
        document: { getElementById: element, addEventListener: () => {} },
        sessionStorage: { getItem: () => null },
        setTimeout: (callback, delay) => {
            timers.set(++timerId, { callback, delay });
            return timerId;
        },
        clearTimeout: id => timers.delete(id),
        clearInterval: () => { pollingStopped = true; },
        fetch: url => {
            requests.push(url);
            return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) });
        },
    });
    vm.runInContext(script, context);

    const note = () => element('emailAlertStatus').innerHTML;
    assert.match(note(), /throttled until/); // Server-rendered initial throttle gets a timer.
    assert.equal(timers.size, 1);
    assert.equal([...timers.values()][0].delay, 1000);
    now += 1000;
    const initialTimer = [...timers.values()][0];
    timers.clear();
    initialTimer.callback();
    assert.doesNotMatch(note(), /throttled until/);
    assert.equal(timers.size, 0);

    async function poll(alerts, running = true) {
        payload = {
            running, email_alerts: alerts, history: [],
            cache: { total_entries: 2, expiring_entries: 1 },
        };
        context._pollOnce();
        await new Promise(resolve => setImmediate(resolve));
    }
    const alerts = {
        configured: true,
        recipient_display: '<staff@example.org>',
        last_sent_at: '2026-09-10T00:00:00Z',
        throttled: true,
        next_allowed_at: '2026-09-10T00:01:00Z',
    };
    await poll(alerts);
    assert.match(note(), /throttled until/);
    assert.match(note(), /&lt;staff@example.org&gt;/);
    assert.match(element('emailLastSent').textContent, /10 Sept? 2026/);
    const firstNote = note();
    const firstTimerId = [...timers.keys()][0];

    alerts.next_allowed_at = '2026-09-10T00:02:00Z';
    await poll({ ...alerts });
    assert.notEqual(note(), firstNote);
    assert.equal(timers.has(firstTimerId), false);
    assert.equal(timers.size, 1);

    await poll({ ...alerts, throttled: false, next_allowed_at: null });
    assert.doesNotMatch(note(), /throttled until/);
    assert.equal(timers.size, 0);

    context._pollTimer = 99;
    await poll({ ...alerts }, false);
    assert.equal(pollingStopped, true);
    assert.equal(timers.size, 1); // Crawl completion must not cancel email expiry.
    now = Date.parse(alerts.next_allowed_at);
    const expiryTimer = [...timers.values()][0];
    timers.clear();
    expiryTimer.callback();
    assert.doesNotMatch(note(), /throttled until/);
    assert.match(note(), /badge-ok">On/);
    assert.equal(timers.size, 0);

    await poll({ ...alerts }); // A stale polled timestamp must not restore the note.
    assert.doesNotMatch(note(), /throttled until/);
    await poll({ configured: false, throttled: false, next_allowed_at: null });
    assert.match(note(), /badge-none">Off/);
    assert.equal(element('emailLastSent').textContent, '—');
    assert.equal(timers.size, 0);
    assert.equal(requests.length, 6);
    assert.ok(requests.every(url => url === '/admin/crawl/status'));
    process.stdout.write('Email throttle polling and expiry checks passed.\n');
});
