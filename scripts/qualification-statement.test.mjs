import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import { buildStatement } from './qualification-statement.mjs';

const sha = bytes => 'sha256:' + createHash('sha256').update(bytes).digest('hex');
const protectedSHA = 'sha256:' + 'a'.repeat(64);
const cooperativeSHA = 'sha256:' + 'b'.repeat(64);
const qualification = Buffer.from(JSON.stringify({
  profile_in_force: 'managed-protected',
  conformance: { report: { sha256: protectedSHA } },
}));
const preflight = {
  qualification_sha256: sha(qualification),
  protected_profile: 'managed-protected',
  protected_report_sha256: protectedSHA,
  cooperative_profile: 'managed-cooperative',
  cooperative_report_sha256: cooperativeSHA,
};
const commit = 'c'.repeat(40);
const now = new Date('2026-11-21T00:00:00.000Z');
const expiry = '2026-12-21T00:00:00.000Z';

test('statement binds exact qualification bytes, source commit and both verified reports', () => {
  const statement = buildStatement(qualification, preflight, commit, expiry, now);
  assert.equal(statement.subject[0].digest.sha256, sha(qualification).slice(7));
  assert.equal(statement.predicate.release.source_commit, commit);
  assert.deepEqual(statement.predicate.host_reports.map(x => x.sha256), [protectedSHA, cooperativeSHA]);
});

test('a changed qualification or unchecked protected report cannot be stated', () => {
  assert.throws(() => buildStatement(Buffer.concat([qualification, Buffer.from(' ')]), preflight, commit, expiry, now));
  assert.throws(() => buildStatement(qualification, {
    ...preflight,
    protected_report_sha256: cooperativeSHA,
  }, commit, expiry, now));
});

test('source and expiry must be pinned at release', () => {
  assert.throws(() => buildStatement(qualification, preflight, 'main', expiry, now));
  assert.throws(() => buildStatement(qualification, preflight, commit, now.toISOString(), now));
  assert.throws(() => buildStatement(qualification, preflight, commit, 'not-a-time', now));
});
