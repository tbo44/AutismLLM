'use strict';

// Exercise the handler rendered on the dashboard, with no live HTTP calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function run() {
  const { script, click } = JSON.parse(fs.readFileSync(0, 'utf8'));
  const attributes = new Map();
  const button = {
    getAttribute: key => attributes.get(key),
    setAttribute: (key, value) => attributes.set(key, value),
    removeAttribute: key => attributes.delete(key),
  };
  const badge = { textContent: 'No active pause', className: 'badge badge-ok' };
  const detail = { textContent: 'No local rate-limit pause is active.' };
  const message = { textContent: '' };
  const elements = {
    providerPauseRefresh: button, providerPauseBadge: badge,
    providerPauseDetail: detail, providerPauseMessage: message,
  };
  const bootElements = { emailAlertStatus: {}, emailLastSent: {} };
  let booting = true;
  let requests = 0;
  let respond;
  const context = vm.createContext({
    AbortSignal,
    document: {
      getElementById(id) {
        if (booting) return elements[id] ?? bootElements[id] ?? null;
        assert.ok(elements[id], `Refresh touched an unrelated element: ${id}`);
        return elements[id];
      },
      addEventListener() {},
    },
    fetch(url, options) {
      assert.equal(url, '/admin/provider-pause');
      assert.equal(options.credentials, 'same-origin');
      assert.equal(options.cache, 'no-store');
      requests++;
      return respond();
    },
  });
  vm.runInContext(script, context);
  booting = false;
  const refresh = () => vm.runInContext(click, context);
  const reply = (status, body) => ({ status, ok: status === 200, json: async () => body });

  let finish;
  respond = () => new Promise(resolve => { finish = resolve; });
  const pending = refresh();
  assert.match(message.textContent, /Refreshing/);
  assert.equal(attributes.get('aria-disabled'), 'true');
  await refresh();
  assert.equal(requests, 1);
  finish(reply(200, {
    badge: 'badge-run', label: 'Provider calls paused', detail: 'About 5 seconds remaining.',
  }));
  await pending;
  assert.equal(badge.className, 'badge badge-run');
  assert.equal(badge.textContent, 'Provider calls paused');
  assert.equal(detail.textContent, 'About 5 seconds remaining.');
  assert.equal(message.textContent, 'Refreshed.');
  assert.equal(attributes.has('aria-disabled'), false);

  for (const [response, notice] of [
    [() => reply(401), /Sign in/],
    [() => reply(503), /Could not refresh/],
    [() => reply(200, {}), /Could not refresh/],
    [() => Promise.reject(new Error('Disconnected')), /Could not refresh/],
  ]) {
    respond = response;
    await refresh();
    assert.match(message.textContent, notice);
    assert.match(message.textContent, /previous status/);
    assert.equal(badge.textContent, 'Provider calls paused');
    assert.equal(detail.textContent, 'About 5 seconds remaining.');
    assert.equal(attributes.has('aria-disabled'), false);
  }
  respond = () => reply(200, {
    badge: 'badge-ok', label: 'No active pause', detail: 'No local rate-limit pause is active.',
  });
  await refresh();
  assert.equal(badge.textContent, 'No active pause');
  assert.equal(requests, 6);
}

run().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
