// Exercise the real refresh strategy with a fake clock/network (no five-minute wait).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const listeners = {};
const timers = [];
let now = 1000;
let calls = 0;
let response = { PIP: 'Old definition' };
let failure = null;
let resolveFetch;
const warnings = [];
const document = {
    hidden: false,
    addEventListener: (name, fn) => { listeners[name] = fn; },
};
const ctx = vm.createContext({
    document,
    Date: { now: () => now },
    console: { warn: (...args) => warnings.push(args) },
    setInterval: (fn, ms) => timers.push({ fn, ms }),
    fetch: async (url, options) => {
        calls++;
        assert.equal(url, '/api/acronyms');
        assert.equal(options.cache, 'no-store');
        if (failure === 'network') throw new Error('Offline');
        if (failure === 'pending') await new Promise(resolve => { resolveFetch = resolve; });
        return {
            ok: failure !== 'http',
            status: 503,
            json: async () => {
                if (failure === 'json') throw new Error('Invalid JSON');
                return response;
            },
        };
    },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/script.js'), 'utf8'), ctx);
const run = code => vm.runInContext(code, ctx);
const flush = () => new Promise(resolve => setImmediate(resolve));

(async () => {
    run(`
        const app = Object.create(MayaApp.prototype);
        const answer = { innerHTML: '', mayaMessage: { content: 'PIP NEW', role: 'assistant' } };
        app.chatArea = { querySelectorAll: () => [answer] };
        _startAcronymRefresh(app);
    `);
    await flush();
    assert.equal(calls, 1);
    assert.equal(timers.length, 1);
    assert.equal(timers[0].ms, 300000);
    assert.match(run('answer.innerHTML'), /Old definition/);

    response = { PIP: 'Updated definition', NEW: 'Added entry' };
    now += 300000;
    await timers[0].fn();
    assert.match(run('answer.innerHTML'), /Updated definition/);
    assert.match(run('answer.innerHTML'), /Added entry/);
    assert.match(run("app.renderAnswer('PIP', {})"), /Updated definition/);

    response = { RENAMED: 'Renamed entry' };
    await timers[0].fn();
    assert.doesNotMatch(run('answer.innerHTML'), /<abbr/);
    assert.match(run("app.renderAnswer('RENAMED', {})"), /Renamed entry/);
    response = {};
    await timers[0].fn();
    assert.doesNotMatch(run("app.renderAnswer('RENAMED', {})"), /<abbr/);

    response = { PIP: 'Keep this' };
    await timers[0].fn();
    for (const kind of ['network', 'http', 'json']) {
        failure = kind;
        await timers[0].fn();
        assert.match(run('answer.innerHTML'), /Keep this/);
    }
    failure = null;
    for (const invalid of [null, [], { PIP: 42 }]) {
        response = invalid;
        await timers[0].fn();
        assert.match(run("app.renderAnswer('PIP', {})"), /Keep this/);
    }
    assert.equal(warnings.length, 6);
    response = { PIP: 'Recovered' };
    await timers[0].fn();
    assert.match(run('answer.innerHTML'), /Recovered/);

    let previous = calls;
    listeners.visibilitychange();
    await flush();
    assert.equal(calls, previous);
    now += 300000;
    document.hidden = true;
    listeners.visibilitychange();
    await flush();
    assert.equal(calls, previous);
    document.hidden = false;
    listeners.visibilitychange();
    await flush();
    assert.equal(calls, previous + 1);

    failure = 'pending';
    const pending = timers[0].fn();
    await flush();
    previous = calls;
    await timers[0].fn();
    assert.equal(calls, previous, 'overlapping requests must be suppressed');
    resolveFetch();
    await pending;
    console.log('Acronym refresh checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });