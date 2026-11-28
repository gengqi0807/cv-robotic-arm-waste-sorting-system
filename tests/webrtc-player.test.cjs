const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup(fetch) {
  const peers = [];
  const timers = new Map();
  let timer = 0;
  class Peer {
    constructor() { this.iceGatheringState = 'complete'; peers.push(this); }
    addTransceiver(kind, options) { assert.equal(kind, 'video'); assert.equal(options.direction, 'recvonly'); }
    async createOffer() { return { type: 'offer', sdp: 'offer-sdp' }; }
    async setLocalDescription(value) { this.localDescription = value; }
    async setRemoteDescription(value) { this.remote = value; }
    async getStats() { return new Map([['video', {type: 'inbound-rtp', kind: 'video', framesDecoded: 1}]]); }
    close() { this.closed = true; }
  }
  const states = [];
  const window = { RTCPeerConnection: Peer };
  const context = vm.createContext({ window, RTCPeerConnection: Peer, AbortController, URL, fetch,
    setTimeout: (callback, ms) => { timers.set(++timer, {callback, ms}); return timer; },
    clearTimeout: id => timers.delete(id),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/webrtc-player.js'), 'utf8'), context);
  const video = { srcObject: null };
  const player = new window.RobotWebRTCPlayer(video, (state, message) => states.push({state, message}));
  return {player, peers, timers, states};
}
const tick = () => new Promise(resolve => setImmediate(resolve));

test('WHEP negotiates, verifies decoded frames, and DELETEs on stop', async () => {
  const requests = [];
  const env = setup(async (url, options) => {
    requests.push({url, options});
    return {status: 201, headers: new Map([['Location', '/robot-camera/whep/session']]), text: async () => 'answer-sdp'};
  });
  await env.player.start('http://127.0.0.1:8889/robot-camera/whep');
  await tick();
  assert.equal(requests[0].options.method, 'POST');
  assert.equal(requests[0].options.body, 'offer-sdp');
  assert.equal(env.peers[0].remote.sdp, 'answer-sdp');
  assert.ok(env.states.some(item => item.state === 'playing'));
  env.player.stop();
  assert.equal(requests[1].options.method, 'DELETE');
  assert.equal(env.peers[0].closed, true);
  assert.equal(env.timers.size, 0);
});

test('service unavailable retries; stop cancels the retry', async () => {
  const env = setup(async () => ({status: 404}));
  await env.player.start('http://localhost:8889/robot-camera/whep');
  await tick();
  assert.ok(env.states.some(item => item.state === 'retrying'));
  assert.ok([...env.timers.values()].some(item => item.ms === 3000));
  env.player.stop();
  assert.equal(env.timers.size, 0);
});

test('late answer after switching away is deleted and cannot revive video', async () => {
  let answer;
  const requests = [];
  const env = setup((url, options) => {
    requests.push(options.method);
    if (options.method === 'DELETE') return Promise.resolve({});
    return new Promise(resolve => { answer = resolve; });
  });
  await env.player.start('http://localhost:8889/robot-camera/whep');
  await tick();
  env.player.stop();
  answer({status: 201, headers: new Map([['Location', '/robot-camera/whep/late']]), text: async () => 'late'});
  await tick();
  assert.deepEqual(requests, ['POST', 'DELETE']);
  assert.equal(env.peers[0].remote, undefined);
  assert.equal(env.timers.size, 0);
});
