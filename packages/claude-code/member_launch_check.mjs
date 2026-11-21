// Offline, signed-out integration check for a disposable guest. The outer ACP
// fixture consumes the entry point's test turn. It never forwards it to Claude.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath, pathToFileURL } from 'node:url';

const self = fileURLToPath(import.meta.url);
const probeDir = dirname(self);
const hqSha = '746281207ad2c853257b70d5a2d2b99e23e88c14604589fcab6473e5e609f84c';
const soulsSha = 'b2321d578b724db6c3e380c453670287bb08dfa6b704f61787fd172231ea4332';

function extract(path, digest, destination) {
  // Extract the very bytes checked, inside a new mode-0700 directory.
  const bytes = readFileSync(path);
  assert.equal(createHash('sha256').update(bytes).digest('hex'), digest, 'unexpected subject archive');
  execFileSync('/usr/bin/tar', ['-xf', '-', '-C', destination], { input: bytes });
}

async function member(root, archive, profile) {
  for await (const line of createInterface({ input: process.stdin })) {
    const request = JSON.parse(line);
    if (request.id === undefined) continue;
    let result;
    switch (request.method) {
      case 'initialize':
        result = { protocolVersion: 1, agentCapabilities: {}, authMethods: [] };
        break;
      case 'session/new': {
        const text = execFileSync('/usr/bin/python3', ['-B', join(probeDir, 'vm_session.py'), archive,
          '--machine-profile', profile, '--member-cli', process.execPath,
          join(root, 'souls/bin/agent-bot-souls.mjs')], { encoding: 'utf8', timeout: 100_000 });
        const metadata = text.split('\n').filter(line => line.startsWith('{'));
        assert.equal(metadata.length, 1);
        writeFileSync(join(root, 'runtime.json'), metadata[0], { flag: 'wx', mode: 0o600 });
        result = { sessionId: 'local-fixture-session' };
        break;
      }
      case 'session/prompt':
        result = { stopReason: 'end_turn' };
        break;
      default:
        throw new Error('unexpected test driver request');
    }
    process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: request.id, result }) + '\n');
  }
}

async function check(hqTar, soulsTar, archive, profile) {
  // Same virtual-hardware and standard-user eligibility as the underlying probe.
  execFileSync('/usr/bin/python3', ['-B', '-c', 'from vm_session import require_guest; require_guest()'],
    { cwd: probeDir, env: { PATH: '/usr/bin:/bin' } });
  const root = mkdtempSync('/private/tmp/acp-');
  let host, soul;
  try {
    extract(hqTar, hqSha, root);
    extract(soulsTar, soulsSha, root);
    const contracts = join(root, 'hq/vendor/soul-contracts');
    process.env.HQ_INTERIM_OFFLINE = '1';
    process.env.AGENT_BOT_SOUL_CONTRACTS = contracts;
    const load = path => import(pathToFileURL(join(root, path)));
    const { startHQ, SOUL, DIGEST } = await load('hq/tests/hq-harness.mjs');
    const { default: broker } = await load('hq/vendor/plugins/agent-comms/plugin.mjs');
    const { startSoul } = await load('souls/lib/entry-point.mjs');
    host = await startHQ({ plugins: [broker] });
    const fixture = join(root, 'fixture');
    const contents = join(fixture, 'Contents');
    mkdirSync(join(contents, 'host'), { recursive: true });
    const save = (name, data) => writeFileSync(join(contents, name), JSON.stringify(data));
    const memberId = 'm_lead';
    save('SoulInfo.json', { soul_id: SOUL, members: [
      { member_id: memberId, role: 'lead', adapter_id: 'local-probe', adapter_version: '0.1.0' },
    ] });
    save('SoulLock.json', { definition_digest: DIGEST });
    save('host/adapters.json', { 'local-probe': {
      command: [process.execPath, self, '--member', root, archive, profile],
      env: { AGENT_BOT_SOULS_BROKER: join(host.home, 'broker.sock') },
    } });
    const binds = () => host.hq.core.receipts.filter(receipt => receipt.kind === 'hq.bind');
    let preflight;
    soul = await startSoul({
      root: fixture, hqHome: host.home, renewEveryMs: 0,
      env: { PATH: '/usr/bin:/bin', TMPDIR: '/private/tmp', AGENT_BOT_SOUL_CONTRACTS: contracts },
      log(event) {
        if (event.event === 'member-launch-fallback' && event.member_id === memberId) {
          assert.equal(preflight, undefined);
          preflight = binds().length;
        }
      },
    });
    assert.equal(binds().length, 0);
    await soul.prompt('Run the local startup check.');
    assert.equal(preflight, 1);
    assert.equal(binds().length, preflight + 1);
    const receipt = binds()[preflight];
    const observed = JSON.parse(readFileSync(join(root, 'runtime.json'), 'utf8'));
    const status = soul.status();
    const runtime = status.members.find(row => row.member_id === memberId);
    assert.equal(status.profile, 'managed-cooperative');
    assert.equal(runtime.running, true);
    assert.equal(runtime.runtime_id, observed.runtime_id);
    assert.equal(status.installation_epoch, observed.installation_epoch);
    assert.equal(status.lease_generation, observed.lease_generation);
    assert.deepEqual([receipt.soul_id, receipt.member_id, receipt.runtime_id, receipt.profile, receipt.proof],
      [SOUL, memberId, observed.runtime_id, status.profile, 'hmac-sha256']);
    console.log('PASS: cooperative runtime ready, one preflight bind, one matching member CLI bind, signed-out ACP session and clean EOF');
  } finally {
    try { await soul?.stop(); } finally {
      try { await host?.done(); } finally { rmSync(root, { recursive: true, force: true }); }
    }
  }
}

if (process.argv[2] === '--member') {
  await member(...process.argv.slice(3));
} else {
  assert.equal(process.argv.length, 6, 'expected HQ tar, Souls tar, Claude release zip and Machine policy');
  await check(...process.argv.slice(2).map(arg => resolve(arg)));
}
