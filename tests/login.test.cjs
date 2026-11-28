const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));

async function setup(fetch, permissionError, robotMode = false) {
  const elements = new Map();
  class Element {
    constructor() { this.listeners = {}; this.hidden = false; this.disabled = false; this.textContent = ''; }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    querySelector() { return this.button ||= new Element(); }
  }
  for (const id of ['login-message', 'face-form', 'password-form', 'face-preview', 'camera-open', 'camera-close', 'face-upload', 'camera-status', 'face-success']) elements.set(id, new Element());
  elements.set('robot-face-preview', new Element());
  elements.get('robot-face-preview').removeAttribute = () => {};
  elements.get('face-form').dataset = {cameraSource: robotMode ? 'robot' : 'browser'};
  elements.get('face-preview').videoWidth = 640;
  elements.get('face-preview').videoHeight = 480;
  elements.get('face-preview').play = async () => {};
  const timers = new Map();
  let timerId = 0;
  let cameraCalls = 0;
  let stopped = 0;
  const track = {stop() { stopped++; }, addEventListener() {}};
  const stream = {getTracks: () => [track], getVideoTracks: () => [track]};
  const navigations = [];
  const document = {hidden: false, listeners: {}, getElementById: id => elements.get(id),
    addEventListener(name, handler) { this.listeners[name] = handler; },
    createElement: () => ({getContext: () => ({drawImage() {}}), toBlob: fn => fn({size: 100})})};
  const window = {location: {assign: url => navigations.push(url)}, listeners: {},
    addEventListener(name, handler) { this.listeners[name] = handler; }};
  const context = vm.createContext({document, window, AbortController, Date, Error,
    navigator: {mediaDevices: {getUserMedia: async () => {cameraCalls++; if (permissionError) throw permissionError; return stream; }}},
    FormData: class {append() {}}, fetch,
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, {fn, delay}); return id; },
    clearTimeout(id) { timers.delete(id); }});
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../frontend/static/js/login.js'), 'utf8'), context);
  await tick();
  return {elements, timers, navigations, window, document, cameraCalls: () => cameraCalls, stopped: () => stopped,
    async run(delay) {
      const entry = [...timers].find(([, value]) => value.delay === delay);
      assert.ok(entry, `timer ${delay} exists`);
      timers.delete(entry[0]); entry[1].fn(); await tick();
    },
    async click(id) { elements.get(id).listeners.click(); await tick(); }};
}
const response = (status, result) => ({ok: status === 200, status, json: async () => result});

test('camera opens automatically; successful recognition shows identity before redirect', async () => {
  let requests = 0;
  const env = await setup(async () => {requests++; return response(200, {authorized: true, name: '刘天悦', role: 'operator', role_name: '操作员'});});
  assert.equal(env.cameraCalls(), 1);
  assert.equal(requests, 0);
  await env.run(1000);
  assert.equal(requests, 1);
  assert.match(env.elements.get('face-success').textContent, /人脸识别成功：刘天悦 · 操作员/);
  assert.equal(env.elements.get('face-success').hidden, false);
  assert.equal(env.stopped(), 1);
  assert.equal(env.navigations.length, 0);
  await env.run(1400);
  assert.deepEqual(env.navigations, ['/tasks']);
  assert.equal(env.timers.size, 0);
});

test('no face retries serially; closing camera cancels the pending attempt', async () => {
  let finish;
  let requests = 0;
  const env = await setup(() => {requests++; return new Promise(resolve => {finish = resolve;});});
  await env.run(1000);
  assert.equal(requests, 1);
  assert.equal([...env.timers.values()].filter(t => t.delay === 1000).length, 0);
  finish(response(422, {authorized: false})); await tick();
  assert.match(env.elements.get('camera-status').textContent, /单张人脸/);
  await env.click('camera-close');
  assert.equal(env.timers.size, 0);
  assert.equal(env.navigations.length, 0);
});

test('late success after closing camera does not redirect or display identity', async () => {
  let finish;
  const env = await setup(() => new Promise(resolve => {finish = resolve;}));
  await env.run(1000);
  await env.click('camera-close');
  finish(response(200, {authorized: true, name: '耿绮', role: 'manager'})); await tick();
  assert.equal(env.elements.get('face-success').hidden, true);
  assert.equal(env.navigations.length, 0);
  assert.equal(env.timers.size, 0);
});

test('permission denial offers retry; no upload occurs', async () => {
  let requests = 0;
  const env = await setup(async () => {requests++;}, {name: 'NotAllowedError'});
  assert.match(env.elements.get('login-message').textContent, /请允许浏览器/);
  assert.equal(env.elements.get('camera-open').disabled, false);
  assert.equal(requests, 0);
});

test('server failure stops auto capture; hidden page releases camera', async () => {
  const env = await setup(async () => response(503, {error: '模型不可用'}));
  await env.run(1000);
  assert.match(env.elements.get('login-message').textContent, /模型不可用/);
  assert.equal(env.timers.size, 0);
  await env.click('camera-open');
  env.document.hidden = true;
  env.document.listeners.visibilitychange();
  assert.equal(env.timers.size, 0);
  assert.equal(env.stopped(), 2);
});

test('robot mode waits for ready pose and authenticates without opening the PC camera', async () => {
  let attempts = 0;
  const env = await setup(async (url) => {
    assert.equal(url, '/api/auth/face/device');
    attempts++;
    return attempts === 1 ? response(409, {error: '等待机械臂到位'}) :
      response(200, {authorized:true, name:'耿绮', role:'manager'});
  }, undefined, true);
  assert.equal(env.cameraCalls(), 0);
  await env.run(1000);
  assert.match(env.elements.get('camera-status').textContent, /等待机械臂到位/);
  await env.run(1000);
  assert.match(env.elements.get('face-success').textContent, /耿绮 · 班组长/);
  await env.run(1400);
  assert.deepEqual(env.navigations, ['/stats']);
});
