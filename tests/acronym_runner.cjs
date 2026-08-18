/**
 * Node.js runner that loads the REAL static/script.js in a vm sandbox with
 * a minimal mock DOM, then exposes MayaApp.annotateAcronyms() and
 * MayaApp.renderAnswer() for the pytest suite to exercise.
 *
 * Stdin:  JSON  { op: "annotate"|"render", html?, raw?, seen? }
 * Stdout: JSON  { result, seen, glossary? }
 */

'use strict';

const vm   = require('vm');
const fs   = require('fs');
const path = require('path');

// ── Minimal DOM mock ──────────────────────────────────────────────────────────

function mockEl() {
    const el = {
        addEventListener : () => {},
        setAttribute     : () => {},
        getAttribute     : () => null,
        classList        : {
            toggle   : () => {},
            add      : () => {},
            remove   : () => {},
            contains : () => false,
        },
        style      : {},
        hidden     : false,
        textContent: '',
        innerHTML  : '',
        value      : 'standard',   // comprehensionSelect default
        disabled   : false,
        scrollTop  : 0,
        scrollHeight: 0,
        dataset    : {},
        // child queries
        appendChild      : () => {},
        remove           : () => {},
        querySelector    : () => mockEl(),
        querySelectorAll : () => ({ forEach: () => {} }),
        closest          : () => null,
        lastElementChild : null,
    };
    return el;
}

const mockDocument = {
    getElementById   : () => mockEl(),
    querySelector    : () => mockEl(),
    querySelectorAll : () => ({ forEach: () => {} }),
    createElement    : () => mockEl(),
    addEventListener : () => {},
    body             : mockEl(),
};

const mockLocalStorage = {
    getItem : () => null,
    setItem : () => {},
};

// ── Load the real static/script.js ───────────────────────────────────────────

const scriptPath = path.join(__dirname, '..', 'static', 'script.js');
let scriptSrc = fs.readFileSync(scriptPath, 'utf8');

// Strip the DOMContentLoaded boot block so it doesn't auto-instantiate MayaApp
// in the vm context (the addEventListener is a no-op anyway, but be explicit).
scriptSrc = scriptSrc.replace(
    /document\.addEventListener\(\s*['"]DOMContentLoaded['"]\s*,[\s\S]*$/,
    ''
);

// Wrap in an IIFE so block-scoped class/const are returned
const wrapped = `(function () {
    ${scriptSrc}
    return { MayaApp: MayaApp, ACRONYM_GLOSSARY: ACRONYM_GLOSSARY };
})()`;

const ctx = vm.createContext({
    document     : mockDocument,
    localStorage : mockLocalStorage,
    window       : { addEventListener: () => {} },
    console      : console,
    fetch        : () => Promise.resolve(),
});

const loaded = vm.runInContext(wrapped, ctx);

// ── Boot a MayaApp instance ───────────────────────────────────────────────────

const app = new loaded.MayaApp();

// ── Handle stdin request ──────────────────────────────────────────────────────

let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => { raw += chunk; });
process.stdin.on('end', () => {
    const payload = JSON.parse(raw);
    const seen    = new Set(payload.seen || []);

    let result;
    if (payload.op === 'render') {
        // Exercise the full renderAnswer path (parses markdown/sections → annotates)
        result = app.renderAnswer(payload.raw, payload.msg || { content: payload.raw });
    } else {
        // Default: just annotateAcronyms
        result = app.annotateAcronyms(payload.html, seen);
    }

    process.stdout.write(JSON.stringify({
        result,
        seen     : [...seen],
        glossary : loaded.ACRONYM_GLOSSARY,   // available for sync checks
    }));
});
