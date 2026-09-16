// Execute the actual player handler with a fake DOM; never send a robot command.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'panel.py'), 'utf8');
const scripts = [...source.matchAll(/<script>([\s\S]*?)<\/script>/g)];
assert.ok(scripts.length);
for (const [, script] of scripts) new vm.Script(script); // full inline JS syntax check
const toggle = source.split('async function toggleListen(node){')[1].split('// ---- Talk')[0];
assert.ok(toggle);
(async () => {
  for (const globalListen of ['true', 'false']) {
    const video = {muted: true, play: () => Promise.resolve()};
    const commands = [];
    const messages = [];
    const context = vm.createContext({
      document: {getElementById: () => video},
      hearingRobot: () => !video.muted,
      startSpeakerMeter() {}, stopSpeakerMeter() {}, updateListenUI() {},
      cmd: (...args) => commands.push(args), toast: text => messages.push(text),
      ROBOTS: [{node: 'ebo', state: {listen: globalListen}}],
    });
    vm.runInContext('async function toggleListen(node){' + toggle, context);
    await context.toggleListen('ebo');
    assert.equal(video.muted, false);
    await context.toggleListen('ebo');
    assert.equal(video.muted, true);
    assert.deepEqual(commands, []);
    assert.equal(context.ROBOTS[0].state.listen, globalListen);
    if (globalListen === 'false') assert.match(messages[0], /全局麦克风已关闭/);
  }
  const globalHandler = source.split('async function setGlobalMicrophone(node,on){')[1].split('function esc(')[0];
  const globalCommands = [];
  let confirmed = false;
  const globalContext = vm.createContext({
    confirm: () => confirmed, refresh() {},
    cmd: (...args) => globalCommands.push(args),
  });
  vm.runInContext('async function setGlobalMicrophone(node,on){' + globalHandler, globalContext);
  await globalContext.setGlobalMicrophone('ebo', false);
  assert.deepEqual(globalCommands, []); // dismissing privacy confirmation changes nothing
  confirmed = true;
  await globalContext.setGlobalMicrophone('ebo', false);
  await globalContext.setGlobalMicrophone('ebo', true);
  assert.deepEqual(globalCommands, [['ebo', 'microphone/set', 'off'], ['ebo', 'microphone/set', 'on']]);
  console.log('Panel JS syntax and local unmute/mute isolation: passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
