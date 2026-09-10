'use strict';

const vm = require('vm');

let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { raw += chunk; });
process.stdin.on('end', async () => {
    const { script, cacheSnapshots } = JSON.parse(raw);
    const elements = new Map();
    const requests = [];
    let intervalCallback = null;
    let statusPoll = 0;

    function element(id) {
        if (!elements.has(id)) {
            elements.set(id, {
                id,
                disabled: false,
                innerHTML: '',
                textContent: '',
                style: {},
                value: '',
                reset: () => {},
            });
        }
        return elements.get(id);
    }

    const session = new Map([['maya_admin_crawl_token', 'test-token']]);
    const running = {
        running: true,
        last_run: null,
        last_result: null,
        history: [],
        cache: { total_entries: 1, expiring_entries: 0 },
    };
    const complete = {
        running: false,
        last_run: '2026-09-10T08:45:00Z',
        last_result: {
            success: true,
            elapsed_seconds: 1.2,
            total_chunks: 42,
            seed_chunks: 40,
            crawled_chunks: 2,
        },
        history: [{
            ts: '2026-09-10 08:45:00,000',
            source: 'manual-crawl',
            outcome: 'SUCCESS',
            detail: '42 chunks',
        }],
        cache: { total_entries: 1, expiring_entries: 0 },
    };

    function response(body) {
        return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve(body),
        });
    }

    const context = vm.createContext({
        console,
        document: {
            getElementById: element,
            querySelector: () => element('query'),
            addEventListener: () => {},
        },
        window: {
            prompt: () => 'test-token',
            confirm: () => true,
        },
        sessionStorage: {
            getItem: key => session.get(key) || null,
            setItem: (key, value) => session.set(key, value),
            removeItem: key => session.delete(key),
        },
        fetch: (url, options = {}) => {
            requests.push([options.method || 'GET', url]);
            if (url === '/admin/crawl') {
                return response({ status: 'started', message: 'started' });
            }
            statusPoll += 1;
            return response(statusPoll === 1 ? running : complete);
        },
        setInterval: callback => {
            intervalCallback = callback;
            return 1;
        },
        clearInterval: () => {
            intervalCallback = null;
        },
    });

    vm.runInContext(script, context);
    if (cacheSnapshots) {
        const snapshots = cacheSnapshots.map(cache => {
            context._applyStatus({ ...complete, cache });
            return {
                html: element('cacheExpiryTbody').innerHTML,
                count: element('cacheExpiring').textContent,
                detailCount: element('cacheDetailCount').textContent,
                window: element('cacheWindow').textContent,
                disabled: element('expiringBtn').disabled,
            };
        });
        process.stdout.write(JSON.stringify(snapshots));
        return;
    }
    context.triggerReindex();
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));
    intervalCallback();
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));

    process.stdout.write(JSON.stringify({
        requests,
        historyHtml: element('historyTbody').innerHTML,
        pollingStopped: intervalCallback === null,
    }));
});