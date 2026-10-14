const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync(process.argv[2], 'utf8');
const warningId = process.argv[3];
const ackScript = source.slice(source.indexOf('var _backupAckBtn'), source.indexOf('function _getSavedToken'));

async function run({confirm = true, token = 'synthetic-admin', status = 200, throws = false}) {
  let calls = [];
  const button = {dataset: {warningId}, disabled: false};
  const result = {};
  vm.runInNewContext(ackScript, {
    document: {getElementById: id => id === 'githubBackupHistoryAck' ? button : result},
    window: {confirm: () => confirm, prompt: () => token},
    fetch: async (url, options) => {
      calls.push({url, options});
      if (throws) throw new Error('network');
      return {ok: status === 200, status};
    },
  });
  await button.onclick();
  return {button, result, calls};
}

(async () => {
  assert.equal((await run({confirm: false})).calls.length, 0);
  assert.equal((await run({token: ''})).calls.length, 0);
  const success = await run({});
  assert.equal(success.calls[0].url, '/admin/backup/history/acknowledge');
  assert.equal(success.calls[0].options.method, 'POST');
  assert.equal(success.calls[0].options.headers['X-Admin-Token'], 'synthetic-admin');
  assert.deepEqual(JSON.parse(success.calls[0].options.body), {warning_id: warningId, investigated: true});
  assert.equal(success.button.disabled, true);
  assert.match(success.result.textContent, /Missing history was not recovered/);
  for (const status of [401, 403, 409, 503]) {
    const failed = await run({status});
    assert.equal(failed.button.disabled, false);
    assert.match(failed.result.textContent, status === 409 ? /Reload and review/ : /not confirmed/);
  }
  const uncertain = await run({throws: true});
  assert.equal(uncertain.button.disabled, false);
  assert.match(uncertain.result.textContent, /Reload to check the audit/);
})().catch(error => { console.error(error); process.exit(1); });
