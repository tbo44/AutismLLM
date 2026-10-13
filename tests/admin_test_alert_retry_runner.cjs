'use strict';

// Run the actual rendered dashboard in fresh page contexts sharing tab storage.
// All HTTP/delivery responses are mocked; this runner never contacts a server.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');

let input = '';
process.stdin.on('data', chunk => { input += chunk; });
process.stdin.on('end', async () => {
  try {
    const { script, logout } = JSON.parse(input);
    const session = new Map();
    const storageKey = 'maya_admin_pending_test_alert';
    const tokenKey = 'maya_admin_crawl_token';
    const requests = [];
    let answer = () => Promise.reject(new Error('Disconnected'));
    let confirmations = 0;
    let confirmed = true;
    let storageBlocked = false;
    const flush = () => new Promise(resolve => setImmediate(resolve));

    function page() {
      const elements = new Map();
      function element(id) {
        if (!elements.has(id)) {
          elements.set(id, {
            disabled: false, style: {}, textContent: '', innerHTML: '',
            classList: { add() {}, remove() {}, toggle() {} },
          });
        }
        return elements.get(id);
      }
      const context = vm.createContext({
        console,
        document: {
          getElementById: element, querySelector: () => element('query'),
          addEventListener() {},
        },
        window: {
          crypto: webcrypto, prompt: () => 'mock-token',
          confirm: message => {
            confirmations++;
            assert.match(message, /duplicate delivery/);
            assert.match(message, /inbox/);
            return confirmed;
          },
        },
        sessionStorage: {
          getItem: key => session.get(key) || null,
          setItem: (key, value) => {
            if (storageBlocked) throw new Error('Storage blocked');
            session.set(key, value);
          },
          removeItem: key => session.delete(key),
        },
        fetch: (url, options) => {
          assert.equal(url, '/admin/alerts/test');
          const key = options.headers['Idempotency-Key'];
          assert.equal(session.get(storageKey), key, 'persist before dispatch');
          requests.push(key);
          return answer();
        },
        setInterval: () => 1, clearInterval() {},
      });
      vm.runInContext(script, context);
      return { context, element };
    }
    function response(status, body) {
      answer = () => Promise.resolve({
        ok: status >= 200 && status < 300, status,
        json: () => Promise.resolve(body),
      });
    }

    let current = page();
    // A disconnect followed by a reload must retry without new-send confirmation.
    current.context.triggerTestAlert();
    const original = session.get(storageKey);
    assert.match(original, /^[a-f0-9]{32}$/);
    await flush();
    assert.equal(current.context._testAlertKey, original);
    assert.match(current.element('testAlertMsg').textContent, /Could not confirm/);
    current = page();
    assert.equal(current.context._testAlertKey, original);
    assert.match(current.element('testAlertMsg').textContent, /same attempt/);
    response(409, { detail: 'Delivery is unconfirmed; check inbox.' });
    current.context.triggerTestAlert();
    await flush();
    assert.equal(confirmations, 1);
    assert.deepEqual(requests, [original, original]);
    assert.equal(session.get(storageKey), original);

    // 409, auth rejection, storage/server errors and malformed responses retain it.
    for (const [status, body] of [
      [409, { detail: 'Still uncertain' }], [401, { detail: 'Token rejected' }],
      [403, { detail: 'Token rejected' }], [503, { detail: 'Storage unavailable' }],
      [200, {}],
    ]) {
      current = page();
      session.set(tokenKey, 'mock-token');
      response(status, body);
      current.context.triggerTestAlert();
      await flush();
      assert.equal(requests.at(-1), original);
      assert.equal(session.get(storageKey), original);
      assert.equal(current.context._testAlertKey, original);
      if (status === 401 || status === 403) assert.equal(session.has(tokenKey), false);
    }
    answer = () => Promise.resolve({ json: () => Promise.reject(new Error('Bad JSON')) });
    current.context.triggerTestAlert();
    await flush();
    assert.equal(session.get(storageKey), original);

    // Cancelling a deliberate new send preserves the old key and makes no request.
    current = page();
    confirmed = false;
    const before = requests.length;
    current.context.triggerTestAlert(true);
    assert.equal(requests.length, before);
    assert.equal(session.get(storageKey), original);
    confirmed = true;
    response(409, { detail: 'Uncertain' });
    current.context.triggerTestAlert(true);
    await flush();
    const replacement = session.get(storageKey);
    assert.notEqual(replacement, original);
    assert.equal(requests.at(-1), replacement);

    // Confirmed success and confirmed failure both complete an attempt.
    for (const sent of [true, false]) {
      current = page();
      response(200, { sent, recipient: 'mock recipient', error: sent ? null : 'Mock failure' });
      current.context.triggerTestAlert();
      await flush();
      assert.equal(session.has(storageKey), false);
      assert.equal(current.context._testAlertKey, '');
      assert.equal(page().context._testAlertKey, '');
      assert.equal(current.element('testAlertBtn').disabled, false);
      assert.equal(current.element('newTestAlertBtn').disabled, false);
    }

    // Reload while a request is still in flight, before any response.
    answer = () => new Promise(() => {});
    current = page();
    current.context.triggerTestAlert();
    const inFlight = session.get(storageKey);
    current = page();
    assert.equal(current.context._testAlertKey, inFlight);
    response(409, { detail: 'Still sending' });
    current.context.triggerTestAlert();
    await flush();
    assert.equal(requests.at(-1), inFlight);

    // Execute the actual sign-out handler, then simulate returning to the dashboard.
    session.set(tokenKey, 'mock-token');
    vm.runInContext(logout, current.context);
    assert.equal(session.has(storageKey), false);
    assert.equal(session.has(tokenKey), false);
    assert.equal(current.context._testAlertKey, '');
    assert.equal(page().context._testAlertKey, '');

    // Fail closed if the pending key cannot be persisted before dispatch.
    storageBlocked = true;
    current = page();
    const count = requests.length;
    current.context.triggerTestAlert();
    assert.equal(requests.length, count);
    assert.match(current.element('testAlertMsg').textContent, /No email was sent/);
    assert.equal(current.element('testAlertBtn').disabled, false);
    console.log('Dashboard retry persistence scenarios passed (mocked delivery only).');
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  }
});
