// ltsm_bridge.js — persistent Node bridge that runs LINE's real LTSM secure
// module (ltsm.wasm) to compute the per-request `X-Hmac` signature the Chrome
// gateway requires.
//
// It loads ltsmSandbox.js (the extension's sandbox bundle) inside a minimal DOM
// shim, drives it through the same postMessage command protocol the extension
// uses, and exposes a line-based JSON stdio API:
//
//   stdin :  {"id": <n>, "accessToken": "<tok>", "path": "<path>", "body": "<body>"}
//   stdout:  {"id": <n>, "hmac": "<base64>"}            on success
//            {"id": <n>, "error": "<message>"}          on failure
//   stdout:  {"ready": true}                            once initialised
//
// HMAC == base64( Hmac(deriveKey(SHA256("3.7.2"), SHA256(accessToken)))
//                   .digest(path + body) ), keyed by SecureKey.loadToken(token)
// inside the wasm.

'use strict';
const fs = require('fs');
const path = require('path');
const readline = require('readline');
const { webcrypto } = require('crypto');
const { performance: perfHook } = require('perf_hooks');

const DIR = __dirname;
const ORIGIN = process.env.LTSM_ORIGIN ||
  'chrome-extension://ophjlpahpchlmihnnnihgmmeilfjmjjc';

const wasmBytes = fs.readFileSync(path.join(DIR, 'ltsm.wasm'));
const wasmAB = wasmBytes.buffer.slice(wasmBytes.byteOffset,
  wasmBytes.byteOffset + wasmBytes.byteLength);

// Some Node globals (crypto/navigator/performance/Event/...) are read-only
// getters; assign defensively.
function setGlobal(name, value, onlyIfMissing) {
  if (onlyIfMissing && typeof global[name] !== 'undefined') return;
  try { global[name] = value; return; } catch (e) { /* fall through */ }
  try { Object.defineProperty(global, name, { value, configurable: true, writable: true }); } catch (e) { /* ignore */ }
}

const CRYPTO = global.crypto || webcrypto;
const ATOB = (s) => Buffer.from(s, 'base64').toString('binary');
const BTOA = (s) => Buffer.from(s, 'binary').toString('base64');

// ---------------------------------------------------------------------------
// Minimal browser environment shims.
// ---------------------------------------------------------------------------
const winListeners = {};
const docListeners = {};
const parentMessages = [];

const fakeEl = () => ({ setAttribute() {}, getAttribute() { return null; }, appendChild() {}, removeChild() {}, style: {}, src: '' });
const anchorEl = () => {
  let u = null;
  const parse = (v) => { try { u = new URL(v, ORIGIN + '/'); } catch { u = new URL(ORIGIN + '/'); } };
  return {
    setAttribute(k, v) { if (k === 'href') parse(v); },
    getAttribute() { return u ? u.href : null; },
    get href() { return u ? u.href : ''; }, get protocol() { return u ? u.protocol : ''; },
    get host() { return u ? u.host : ''; }, get hostname() { return u ? u.hostname : ''; },
    get port() { return u ? u.port : ''; }, get pathname() { return u ? u.pathname : '/'; },
    get search() { return u ? u.search : ''; }, get hash() { return u ? u.hash : ''; },
  };
};

const documentShim = {
  addEventListener: (t, cb) => { (docListeners[t] = docListeners[t] || []).push(cb); },
  removeEventListener: () => {},
  createElement: (tag) => (String(tag).toLowerCase() === 'a' ? anchorEl() : fakeEl()),
  createElementNS: () => fakeEl(),
  createTextNode: () => fakeEl(), createComment: () => fakeEl(),
  createDocumentFragment: () => fakeEl(),
  createTreeWalker: () => ({ currentNode: null, nextNode: () => null, firstChild: () => null }),
  importNode: () => fakeEl(), adoptNode: () => fakeEl(),
  getElementsByTagName: () => [], getElementById: () => null,
  querySelector: () => null, querySelectorAll: () => [],
  currentScript: { src: 'file://' + DIR.replace(/\\/g, '/') + '/ltsmSandbox.js' },
  head: fakeEl(), body: fakeEl(),
};

const windowShim = {
  origin: ORIGIN,
  location: {
    href: ORIGIN + '/ltsmSandbox.html?sandboxId=py',
    protocol: 'chrome-extension:',
    host: ORIGIN.replace('chrome-extension://', ''),
    hostname: ORIGIN.replace('chrome-extension://', ''),
    port: '', pathname: '/ltsmSandbox.html', search: '?sandboxId=py', hash: '',
    origin: ORIGIN,
  },
  crypto: CRYPTO,
  atob: ATOB, btoa: BTOA,
  addEventListener: (t, cb) => { (winListeners[t] = winListeners[t] || []).push(cb); },
  removeEventListener: () => {},
  parent: { postMessage: (msg) => { parentMessages.push(msg); } },
  postMessage: (msg) => { parentMessages.push(msg); },
  document: documentShim,
  navigator: { userAgent: 'Mozilla/5.0 Chrome/124 LTSM-bridge', language: 'en-US' },
  performance: global.performance || perfHook,
  screen: { width: 1280, height: 800 },
  devicePixelRatio: 1,
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  requestAnimationFrame: (cb) => setTimeout(() => cb((global.performance || perfHook).now()), 0),
  cancelAnimationFrame: () => {},
  requestIdleCallback: (cb) => setTimeout(() => cb({ timeRemaining: () => 0, didTimeout: false }), 0),
  cancelIdleCallback: () => {},
  setTimeout, clearTimeout, setInterval, clearInterval,
};
documentShim.location = windowShim.location;

setGlobal('window', windowShim);
setGlobal('self', windowShim);
setGlobal('document', documentShim);
setGlobal('atob', ATOB);
setGlobal('btoa', BTOA);
setGlobal('location', windowShim.location);
setGlobal('origin', ORIGIN);
setGlobal('history', { pushState() {}, replaceState() {}, back() {}, forward() {}, length: 0, state: null });
setGlobal('parent', windowShim.parent);
setGlobal('top', windowShim);
setGlobal('navigator', windowShim.navigator, true);
setGlobal('performance', windowShim.performance, true);
setGlobal('fetch', async () => new Response(wasmBytes, { headers: { 'Content-Type': 'application/wasm' } }));

const localStore = {};
setGlobal('localStorage', { getItem: (k) => (k in localStore ? localStore[k] : null), setItem: (k, v) => { localStore[k] = String(v); }, removeItem: (k) => { delete localStore[k]; }, clear: () => { for (const k in localStore) delete localStore[k]; } });
setGlobal('sessionStorage', global.localStorage);

// Emscripten loads the wasm via (synchronous) XMLHttpRequest in web env.
setGlobal('XMLHttpRequest', class XMLHttpRequest {
  constructor() { this.readyState = 0; this.status = 0; this.response = null; this.responseText = ''; this._rt = ''; }
  open(method, url) { this._url = String(url); this.readyState = 1; }
  set responseType(v) { this._rt = v; } get responseType() { return this._rt; }
  setRequestHeader() {}
  addEventListener(t, cb) { if (t === 'load') this.onload = cb; if (t === 'error') this.onerror = cb; }
  send() {
    if (this._rt === 'arraybuffer') this.response = wasmAB;
    else { this.responseText = wasmBytes.toString('binary'); this.response = this.responseText; }  // already a Buffer
    this.status = 200; this.readyState = 4;
    if (this.onreadystatechange) this.onreadystatechange();
    if (this.onload) this.onload();
  }
});

setGlobal('customElements', { define() {}, get() { return undefined; }, whenDefined() { return Promise.resolve(); } });
setGlobal('getComputedStyle', () => ({ getPropertyValue: () => '' }));
setGlobal('DOMParser', class DOMParser { parseFromString() { return { documentElement: fakeEl(), querySelector: () => null, querySelectorAll: () => [], getElementsByTagName: () => [] }; } }, true);
setGlobal('XMLSerializer', class XMLSerializer { serializeToString() { return ''; } }, true);
setGlobal('MutationObserver', class MutationObserver { observe() {} disconnect() {} takeRecords() { return []; } }, true);
setGlobal('ResizeObserver', class ResizeObserver { observe() {} unobserve() {} disconnect() {} }, true);
setGlobal('IntersectionObserver', class IntersectionObserver { observe() {} unobserve() {} disconnect() {} }, true);

for (const name of [
  'HTMLElement', 'HTMLIFrameElement', 'Element', 'CustomEvent', 'Event',
  'CSSStyleSheet', 'ShadowRoot', 'DocumentFragment', 'Text', 'Comment',
  'HTMLTemplateElement', 'HTMLStyleElement', 'HTMLDivElement', 'HTMLSpanElement',
  'HTMLInputElement', 'HTMLAnchorElement', 'HTMLButtonElement', 'HTMLCanvasElement',
  'HTMLImageElement', 'HTMLScriptElement', 'SVGElement', 'File', 'FileReader',
  'Image', 'Notification', 'DOMException', 'Document', 'Window', 'HTMLDocument',
  'Range', 'StaticRange', 'NodeList', 'HTMLCollection', 'DOMTokenList',
  'NamedNodeMap', 'Attr', 'CharacterData', 'CSSRule', 'CSSStyleDeclaration',
  'StyleSheet', 'MediaQueryList', 'PerformanceObserver', 'NodeFilter',
  'XPathResult', 'DOMImplementation', 'Node',
]) {
  setGlobal(name, class {}, true);
}
try { if (global.Node && !global.Node.ELEMENT_NODE) { global.Node.ELEMENT_NODE = 1; global.Node.TEXT_NODE = 3; } } catch (e) {}

// ---------------------------------------------------------------------------
// Load the real sandbox bundle and drive its message protocol.
// ---------------------------------------------------------------------------
require(path.join(DIR, 'ltsmSandbox.js'));
(docListeners['DOMContentLoaded'] || []).forEach((cb) => cb());

function send(data) {
  const before = parentMessages.length;
  const msg = { data: { sandboxId: 'py', type: 'request', data } };
  const ls = winListeners['message'] || [];
  return (async () => {
    for (const cb of ls) await cb(msg);
    for (let i = 0; i < 400 && parentMessages.length === before; i++) {
      await new Promise((r) => setTimeout(r, 5));
    }
    const resp = parentMessages.slice(before).find(
      (m) => m && m.sandboxId === 'py' && (m.type === 'response' || m.type === 'error'));
    if (!resp) throw new Error('no response for ' + data.command);
    if (resp.type === 'error') {
      const d = resp.data || {};
      throw new Error(d.message || d.name || String(d) || 'LTSM error');
    }
    return resp.data;
  })();
}

// base64 <-> bytes helpers (match the bundle's RR/NR)
const b64ToBytes = (b64) => new Uint8Array(Buffer.from(String(b64), 'base64'));
const bytesToB64 = (x) => Buffer.from(Array.from(x)).toString('base64');
// uint64 Embind params want a BigInt; coerce numbers/strings safely.
const bigIntOrNum = (v) => {
  try { return BigInt(v); } catch (e) { return Number(v); }
};
const argTypes = (r) => `to=${typeof r.to} from=${typeof r.from} ` +
  `senderKeyId=${typeof r.senderKeyId}:${r.senderKeyId} ` +
  `receiverKeyId=${typeof r.receiverKeyId}:${r.receiverKeyId} ` +
  `contentType=${typeof r.contentType}:${r.contentType} ` +
  `sequenceNumber=${typeof r.sequenceNumber}:${r.sequenceNumber}`;

// Dispatch a high-level op onto the sandbox command protocol.
async function handle(req) {
  switch (req.op) {
    case 'hmac':
      // returns base64 string already (cg = btoa)
      return await send({ command: 'get_hmac', payload: {
        accessToken: req.accessToken || '', path: req.path, body: req.body || '',
      }});
    case 'curvekey_generate':
      // returns a numeric ltsmKeyId handle
      return await send({ command: 'curvekey_generate' });
    case 'e2ee_public_key': {
      const pk = await send({ command: 'e2eekey_get_public_key', ltsmKeyId: req.keyId });
      return bytesToB64(pk);
    }
    case 'e2ee_create_channel':
      return await send({ command: 'e2eekey_create_channel', ltsmKeyId: req.keyId,
        payload: b64ToBytes(req.serverPubKeyB64) });
    case 'e2ee_unwrap_keychain':
      // returns an array of unwrapped E2EE key handles (numbers)
      return await send({ command: 'e2eechannel_unwrap_e2ee_key_chain',
        ltsmKeyId: req.channelId, payload: b64ToBytes(req.encKeyChainB64) });
    case 'e2ee_get_key_id':
      return await send({ command: 'e2eekey_get_key_id', ltsmKeyId: req.keyHandle });
    case 'e2ee_public_key_for_handle': {
      const pk = await send({ command: 'e2eekey_get_public_key', ltsmKeyId: req.keyHandle });
      return bytesToB64(pk);
    }
    case 'e2ee_create_channel_with_pubkey':
      // channel from one of *our* key handles and a peer public key (bytes)
      return await send({ command: 'e2eekey_create_channel', ltsmKeyId: req.keyHandle,
        payload: b64ToBytes(req.peerPubKeyB64) });
    case 'e2ee_encrypt_v2': {
      // sequenceNumber is a uint64 -> must be a BigInt for Embind; key ids are
      // plain numbers (uint32). contentType is a small int.
      try {
        const ct = await send({ command: 'e2eechannel_encrypt_v2', ltsmKeyId: req.channelId,
          payload: { to: req.to, from: req.from, senderKeyId: Number(req.senderKeyId),
            receiverKeyId: Number(req.receiverKeyId), contentType: Number(req.contentType),
            sequenceNumber: bigIntOrNum(req.sequenceNumber),
            plaintext: b64ToBytes(req.plaintextB64) } });
        return bytesToB64(ct);
      } catch (e) {
        throw new Error((e && e.message ? e.message : e) + ' | args ' + argTypes(req));
      }
    }
    case 'e2ee_decrypt_v2': {
      try {
        const pt = await send({ command: 'e2eechannel_decrypt_v2', ltsmKeyId: req.channelId,
          payload: { to: req.to, from: req.from, senderKeyId: Number(req.senderKeyId),
            receiverKeyId: Number(req.receiverKeyId), contentType: Number(req.contentType),
            ciphertext: b64ToBytes(req.ciphertextB64) } });
        return bytesToB64(pt);
      } catch (e) {
        throw new Error((e && e.message ? e.message : e) + ' | args ' + argTypes(req));
      }
    }
    case 'e2ee_decrypt_v1': {
      // old format: decryptV1(channel, ciphertext) — payload IS the raw ciphertext
      // bytes (no to/from/keyIds/contentType).
      const pt = await send({ command: 'e2eechannel_decrypt_v1', ltsmKeyId: req.channelId,
        payload: b64ToBytes(req.ciphertextB64) });
      return bytesToB64(pt);
    }
    case 'e2ee_encrypt_v1': {
      // old format: encryptV1(channel, plaintext) — payload IS the raw plaintext
      // bytes (no to/from/keyIds/contentType/sequenceNumber), mirroring decryptV1.
      const ct = await send({ command: 'e2eechannel_encrypt_v1', ltsmKeyId: req.channelId,
        payload: b64ToBytes(req.plaintextB64) });
      return bytesToB64(ct);
    }
    case 'e2ee_wrap_group_shared_key': {
      // wrap a group shared key (a key *handle*, e.g. from curvekey_generate)
      // for a member channel -> base64 wrapped key. NOTE: unlike most ops the
      // payload is the raw handle number (the sandbox resolves it via its
      // key-handle map), NOT bytes.
      const wrapped = await send({ command: 'e2eechannel_wrap_group_shared_key',
        ltsmKeyId: req.channelId, payload: Number(req.keyHandle) });
      return bytesToB64(wrapped);
    }
    case 'e2ee_export_key': {
      // serialize an unwrapped private-key handle so it can be persisted and
      // re-loaded in a future process (cross-session E2EE).
      const bytes = await send({ command: 'e2eekey_export_key', ltsmKeyId: req.keyHandle });
      return bytesToB64(bytes);
    }
    case 'e2ee_load_key':
      // re-import an exported key blob -> a fresh numeric key handle. NOTE: the
      // payload is at the TOP level (not under ltsmKeyId), per the bundle wrapper.
      return await send({ command: 'e2eekey_load_key', payload: b64ToBytes(req.exportedB64) });
    case 'e2ee_unwrap_group_shared_key':
      // channel = e2eekey_create_channel(myKeyHandle, groupCreatorPubKey); this
      // unwraps the group's encrypted shared key -> a group-key handle (number).
      return await send({ command: 'e2eechannel_unwrap_group_shared_key',
        ltsmKeyId: req.channelId, payload: b64ToBytes(req.encSharedKeyB64) });
    case 'e2ee_generate_hash_key_chain_to_confirm_e2ee': {
      // E2EE e-mail device-confirm login flow: generate the hash key chain
      // from the primary device's encryptedKeyChain over the login channel.
      // Payload is the raw encryptedKeyChain bytes; returns the hash key
      // chain bytes (base64'd here) for confirmE2EELogin(verifier, b64(chain)).
      const chain = await send({ command: 'e2eechannel_generate_hash_key_chain_to_confirm_e2ee',
        ltsmKeyId: req.channelId, payload: b64ToBytes(req.encKeyChainB64) });
      return bytesToB64(chain);
    }
    default:
      throw new Error('unknown op: ' + req.op);
  }
}

async function main() {
  await send({ command: 'init' });
  process.stdout.write(JSON.stringify({ ready: true }) + '\n');

  const rl = readline.createInterface({ input: process.stdin });
  for await (const line of rl) {
    const s = line.trim();
    if (!s) continue;
    let req;
    try { req = JSON.parse(s); } catch { continue; }
    try {
      const result = await handle(req);
      process.stdout.write(JSON.stringify({ id: req.id, result }) + '\n');
    } catch (e) {
      process.stdout.write(JSON.stringify({ id: req.id, error: e && e.message ? e.message : String(e) }) + '\n');
    }
  }
}

main().catch((e) => {
  process.stdout.write(JSON.stringify({ ready: false, error: e && e.message ? e.message : String(e) }) + '\n');
  process.exit(1);
});
