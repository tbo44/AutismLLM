/* Execute the real dashboard history code with a small DOM and mocked requests. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor() { this.children = []; this.disabled = false; this._text = ''; }
  set textContent(text) { this._text = text; this.children = []; }
  get textContent() { return this._text; }
  appendChild(child) { this.children.push(child); }
}
const elements = {};
for (const suffix of ['Msg', 'Rows', 'Refresh', 'Previous', 'Next']) {
  elements['testAttemptHistory' + suffix] = new Element();
}
let response, status = 200, calls = [], removed = [];
const sandbox = {
  document: {
    getElementById: id => elements[id],
    createElement: () => new Element(),
  },
  window: { prompt: () => { throw Error('Unexpected prompt'); } },
  _getSavedToken: () => 'mock-history-token',
  _TOKEN_KEY: 'mock-token-key', _crawlToken: 'mock-history-token',
  sessionStorage: { removeItem: key => removed.push(key) },
  fetch: async (path, options) => {
    calls.push({path, options});
    assert.equal(options.headers.Authorization, 'Bearer mock-history-token');
    assert.equal(options.method, undefined); // History must only GET, never send/resolve.
    assert.match(path, /^\/admin\/alerts\/test\/attempts\/history\?/);
    return {ok: status === 200, status, json: async () => response};
  },
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('static/admin-test-attempts.js', 'utf8'), sandbox);
const el = suffix => elements['testAttemptHistory' + suffix];

(async () => {
  response = {attempts: [], total: 0, offset: 0, limit: 25};
  await sandbox.loadTestAlertHistory(0);
  assert.match(el('Msg').textContent, /No archived/);
  assert.equal(el('Previous').disabled, true);
  assert.equal(el('Next').disabled, true);
  const key = '<img src=x onerror=alert(1)>';
  response = {
    attempts: ['delivered', 'not_found', 'unknown'].map(resolution => ({
      key, resolution, resolved_at: 1700000000, inbox_checked: true,
    })),
    total: 28, offset: 0, limit: 25,
  };
  await sandbox.loadTestAlertHistory(0);
  assert.equal(el('Rows').children.length, 3);
  const values = el('Rows').children.map(row => row.children.map(cell => cell.textContent));
  assert.deepEqual(values.map(row => row[1]), ['Email found', 'Email not found', 'Still uncertain']);
  assert.equal(values[0][0], key); // Rendered as text, not HTML.
  assert.equal(values[0][2], '2023-11-14 22:13:20.000 UTC');
  assert.equal(el('Next').disabled, false);
  response = {...response, offset: 25, attempts: response.attempts};
  await sandbox.loadTestAlertHistory(25);
  assert.match(calls.at(-1).path, /offset=25$/);
  assert.equal(el('Previous').disabled, false);
  assert.equal(el('Next').disabled, true);
  assert.match(el('Msg').textContent, /26–28 of 28/);
  response = {...response, offset: 0};
  await sandbox.loadTestAlertHistory(0);
  assert.equal(el('Previous').disabled, true);
  status = 403; response = {detail: 'Invalid admin token.'};
  await sandbox.loadTestAlertHistory(25);
  assert.equal(el('Rows').children.length, 0);
  assert.equal(el('Msg').textContent, response.detail);
  assert.deepEqual(removed, ['mock-token-key']);
  assert.equal(sandbox._crawlToken, '');
  assert.equal(el('Next').disabled, true);
  assert.equal(el('Refresh').disabled, false);
  status = 503; response = {detail: 'Test email attempt storage unavailable.'};
  await sandbox.loadTestAlertHistory(0);
  assert.equal(el('Msg').textContent, response.detail);
  assert.equal(el('Refresh').disabled, false);
})().catch(error => { console.error(error); process.exitCode = 1; });
