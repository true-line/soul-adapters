import assert from 'node:assert/strict';
import { execFileSync, spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const root = process.cwd();
const hq = join(root, 'hq-container', 'hq');
const contracts = join(hq, 'vendor/soul-contracts');
const runtime = join(root, 'souls');
const fixture = JSON.parse(readFileSync(join(contracts, 'fixtures/positive/hello.first-start.json'))).input;
const soulId = fixture.soul_id;
const digest = fixture.definition_digest;
const hello = { ...fixture, requested_profile: 'managed-cooperative', requested_roles: [
  { role: 'lead', member_id: 'm_lead', adapter_id: 'codex', adapter_version: JSON.parse(readFileSync(new URL('./package-lock.json', import.meta.url))).packages[''].dependencies['@agentclientprotocol/codex-acp'] },
] };
const { stage1Subject } = await import(pathToFileURL(join(root, 'conformance/lib/stage1-subject.mjs')));
const { rpcPeer } = await import(pathToFileURL(join(runtime, 'lib/jsonrpc.mjs')));
const soulRoot = join(root, 'soul');
const contents = join(soulRoot, 'Contents');
mkdirSync(join(contents, 'host'), { recursive: true, mode: 0o700 });
writeFileSync(join(contents, 'SoulInfo.json'), JSON.stringify({ soul_id: soulId, members: hello.requested_roles }));
writeFileSync(join(contents, 'SoulLock.json'), JSON.stringify({ definition_digest: digest }));
writeFileSync(join(contents, 'host/adapters.json'), JSON.stringify({ codex: {
  command: [process.execPath, resolve(root, 'install/node_modules/@agentclientprotocol/codex-acp/dist/index.js')], env: {},
} }));
const subject = await stage1Subject(hq, contracts, hello, { bundledBroker: true, notices: true });
const world = await subject.open();
let child;
try {
  assert.equal(world.profile, 'managed-cooperative');
  const env = { ...process.env, AGENT_BOT_SOUL_CONTRACTS: contracts };
  const profile = join(root, 'soul-cooperative.sb');
  const socket = join(contents, 'host', 'members.sock');
  const literal = value => JSON.stringify(value);
  writeFileSync(profile, `(version 1)\n(allow default)\n(deny network*)\n(allow network-outbound (remote ip "localhost:*"))\n` +
    `(allow network-bind (local unix-socket (path-literal ${literal(socket)})))\n` +
    [socket, join(world.hqHome, 'admission.sock'), join(world.hqHome, 'broker.sock')]
      .map(path => `(allow network-outbound (remote unix-socket (path-literal ${literal(path)})))\n`).join(''));
  const canary = "import errno,socket,sys; s=socket.socket(); s.settimeout(2); " +
    "r=s.connect_ex(('203.0.113.1',443)); sys.exit(0 if r in (errno.EPERM,errno.EACCES) else 1)";
  execFileSync('/usr/bin/sandbox-exec', ['-f', profile, '/usr/bin/python3', '-IS', '-c', canary], { timeout: 5000 });
  child = spawn('/usr/bin/sandbox-exec', ['-f', profile, process.execPath, join(runtime, 'bin/agent-bot-souls.mjs'), 'run', '--soul', soulRoot, '--hq-home', world.hqHome], {
    cwd: root, env, stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stderr = '';
  child.stderr.on('data', chunk => { stderr += chunk.toString(); });
  const updates = [];
  const client = rpcPeer({ input: child.stdout, output: child.stdin, onNotify: (method, params) => {
    if (method === 'session/update' && params?.update?.sessionUpdate === 'agent_message_chunk' && params.update.content?.type === 'text') updates.push(params.update.content.text);
  } });
  const init = await client.request('initialize', { protocolVersion: 1, clientCapabilities: {} });
  assert.equal(init.protocolVersion, 1);
  const session = await client.request('session/new', { cwd: soulRoot, mcpServers: [] });
  const result = await client.request('session/prompt', { sessionId: session.sessionId, prompt: [{ type: 'text', text: 'Reply with a short greeting.' }] });
  assert.equal(result.stopReason, 'end_turn');
  assert.ok(updates.join('').includes('Fixture response complete.'));
  const rows = world.receipts();
  const { tables } = JSON.parse(readFileSync(join(world.hqHome, 'store.json'), 'utf8'));
  const runtimes = new Map(tables).get('hq-admission\0runtimes');
  assert.ok(Array.isArray(runtimes), 'missing admitted runtime table');
  const matching = runtimes.filter(([, rt]) => rt.soul_id === soulId && rt.member_id === 'm_lead' &&
    rt.role === 'lead' && rt.adapter_id === 'codex' && !rt.superseded);
  assert.equal(matching.length, 1, 'expected one live runtime for the requested Soul and member');
  const [runtimeId, assignment] = matching[0];
  const admission = rows.filter(row => row.kind === 'hq.admission' && row.soul_id === soulId &&
    row.lease_generation === assignment.lease_generation && row.runtimes === 1);
  assert.equal(admission.length, 1, 'missing the requested Soul admission receipt');
  for (const kind of ['hq.equip', 'hq.ready', 'hq.launch.refused']) {
    const matched = rows.filter(row => row.kind === kind && row.runtime_id === runtimeId);
    assert.equal(matched.length, 1, `missing ${kind} receipt for the admitted runtime`);
    assert.equal(matched[0].profile, 'managed-cooperative');
    if (kind === 'hq.launch.refused') assert.equal(matched[0].code, 'host.launch_unavailable');
  }
  assert.equal(admission[0].profile, 'managed-cooperative');
  assert.ok(rows.length > 0 && rows.every(row => row.profile === 'managed-cooperative'));
  const events = stderr.split('\n').filter(Boolean).flatMap(line => {
    try { return [JSON.parse(line)]; } catch { return []; }
  });
  assert.ok(events.some(event => event.event === 'member-started' && event.adapter_id === 'codex'));
  assert.ok(events.some(event => event.event === 'member-launch-fallback' && event.code === 'host.launch_unavailable'));
  console.log(JSON.stringify({ profile: world.profile, front_door: 'ACP run', stop_reason: result.stopReason,
    answer_received: true, nonempty_chunks: updates.filter(Boolean).length, all_receipts_cooperative: true,
    broker: 'production-pinned', hq_enrollment: 'fixture', required_receipts_present: true, cooperative_launch_fallback: true }));
  child.stdin.end();
  const [code] = await once(child, 'exit');
  assert.equal(code, 0);
} finally {
  if (child?.exitCode === null) child.kill('SIGTERM');
  await world.close();
}
