// Exercise the production sendMessage logic with controlled fetch and timers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

let onTimeout;
let fetches = 0;
let stalled = true;
const context = vm.createContext({
    AbortController,
    console,
    document: { addEventListener() {} },
    fetch: async (url, opts) => {
        assert.equal(url, '/chat');
        fetches++;
        return {
            ok: true,
            // Stall while reading the response body, after fetch returned.
            json: () => stalled ? new Promise((_, reject) => {
                opts.signal.addEventListener('abort', () => reject(opts.signal.reason));
            }) : Promise.resolve({ answer: 'Answer received.', sources: [] })
        };
    },
    setTimeout(fn, ms) {
        assert.equal(ms, 40_000);
        onTimeout = fn;
        return 1;
    },
    clearTimeout() { onTimeout = undefined; }
});

const script = fs.readFileSync(path.join(__dirname, '../static/script.js'), 'utf8');
vm.runInContext(`${script}\nthis.MayaApp = MayaApp;`, context);

async function run() {
    const app = Object.create(context.MayaApp.prototype);
    let removed = 0;
    Object.assign(app, {
        messages: [{ role: 'assistant', content: 'Welcome.' }],
        suggestions: { style: {} },
        messageInput: { value: 'What is PIP?' },
        sendButton: { disabled: false },
        comprehensionLevel: 'standard',
        chatArea: { lastElementChild: { remove() { removed++; } } },
        addMessage(message) { this.messages.push(message); }
    });
    const send = app.sendMessage();
    assert.equal(app.sendButton.disabled, true);
    app.messageInput.value = 'Second question while loading';
    await app.sendMessage();
    assert.equal(fetches, 1);
    onTimeout();
    await send;

    assert.equal(removed, 1);
    assert.equal(app.messages.length, 3);
    const reply = app.messages.at(-1);
    assert.match(reply.content, /took too long.*Please try asking again/);
    const html = app.renderAnswer(reply.content, reply);
    for (const domain of ['www.nhs.uk', 'www.autism.org.uk', 'www.gov.uk']) {
        assert.ok(html.includes(`<a href="https://${domain}/`));
    }
    assert.equal(app.sendButton.disabled, false);
    assert.equal(onTimeout, undefined);

    stalled = false;
    app.messageInput.value = 'What is PIP?';
    await app.sendMessage();
    assert.equal(fetches, 2);
    assert.equal(app.messages.at(-1).content, 'Answer received.');
    assert.equal(app.sendButton.disabled, false);
    assert.equal(onTimeout, undefined);
}

run().then(() => console.log('chat timeout and retry verified')).catch(error => {
    console.error(error);
    process.exitCode = 1;
});
