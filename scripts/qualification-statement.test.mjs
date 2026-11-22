import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import { buildStatement } from './qualification-statement.mjs';

const sha = bytes => 'sha256:' + createHash('sha256').update(bytes).digest('hex');
const protectedSHA = 'sha256:' + 'a'.repeat(64);
const cooperativeSHA = 'sha256:' + 'b'.repeat(64);
const protectedEnvelope = Buffer.from('exact protected v2 envelope bytes');
const cooperativeEnvelope = Buffer.from('exact cooperative v1 envelope bytes');
const approvedPrefix = 'qualification://stage2a/';
const report = (version, profile, payload, signature, payloadSHA, envelope) => ({
  report_schema_version: version,
  signature_purpose: `agent-bot/qualification-host-report/v${version}`,
  payload: { uri: `qualification/${payload}.report.json`, sha256: payloadSHA },
  signature: { uri: `${signature}-envelope.json`, sha256: sha(envelope) },
});
const soulsBuild = {
  source_repository: 'true-line/agent-bot-souls',
  source_commit: '1'.repeat(40), source_tree: '2'.repeat(40),
  builder_sha256: 'sha256:' + '3'.repeat(64),
  toolchain_sha256: 'sha256:' + '4'.repeat(64),
  workflow: '.github/workflows/souls-build-receipt.yml',
  run_id: '123456789', run_attempt: 1,
  package_sha256: 'sha256:' + '5'.repeat(64),
  receipt: { uri: approvedPrefix + 'souls-build-bundle/' + '6'.repeat(64),
    sha256: 'sha256:' + '6'.repeat(64) },
};
const assetIndex = {
  schema_version: 1,
  assets: Object.fromEntries([
    ['qualification', 'qualification.json'],
    ['policy-envelope', 'policy-envelope.json'],
    ['protected-envelope', 'protected-envelope.json'],
    ['cooperative-envelope', 'cooperative-envelope.json'],
    ['souls-package', 'souls-package.tgz'],
    ['souls-build-bundle', 'souls-build-receipt.bundle.json'],
  ].map(([role, filename]) => {
    const assetSHA = role === 'souls-package' ? soulsBuild.package_sha256 :
      role === 'souls-build-bundle' ? soulsBuild.receipt.sha256 : sha(filename);
    return [role, {
      uri: approvedPrefix + role + '/' + assetSHA.slice(7),
      sha256: assetSHA,
      byte_length: 1,
    }];
  })),
};
const indexBytes = Buffer.from(JSON.stringify(assetIndex, null, 2) + '\n');
const qualificationRecord = {
  schema_version: 2,
  profile_in_force: 'managed-protected',
  souls_build: soulsBuild,
  conformance: {
    protected_report: report(2, 'protected', 'protected', 'protected', protectedSHA, protectedEnvelope),
    cooperative_report: report(1, 'cooperative', 'cooperative', 'cooperative', cooperativeSHA, cooperativeEnvelope),
  },
};
const qualification = Buffer.from(JSON.stringify(qualificationRecord));
const preflight = {
  schema_version: 2,
  qualification_sha256: sha(qualification),
  protected_profile: 'managed-protected',
  protected_report_sha256: protectedSHA,
  protected_envelope_sha256: sha(protectedEnvelope),
  cooperative_profile: 'managed-cooperative',
  cooperative_report_sha256: cooperativeSHA,
  cooperative_envelope_sha256: sha(cooperativeEnvelope),
  souls_build_bundle_sha256: soulsBuild.receipt.sha256,
  souls_package_sha256: soulsBuild.package_sha256,
  souls_source_tree: soulsBuild.source_tree,
};
const commit = 'c'.repeat(40);
const soulsWorkflow = {
  repository: soulsBuild.source_repository,
  workflow_path: soulsBuild.workflow,
  source_commit: soulsBuild.source_commit,
  source_tree: soulsBuild.source_tree,
  builder_sha256: soulsBuild.builder_sha256,
  toolchain_sha256: soulsBuild.toolchain_sha256,
  oidc_issuer: 'https://token.actions.githubusercontent.com',
  certificate_identity: 'https://github.com/true-line/agent-bot-souls/' +
    soulsBuild.workflow + '@refs/heads/main',
  trust_root_sha256: 'sha256:' + '7'.repeat(64),
  receipt_predicate_type: 'https://true-line.ai/soul-contracts/adapter/v2/souls-build',
  bundle_media_type: 'application/vnd.dev.sigstore.bundle.v0.3+json',
  dsse_payload_type: 'application/vnd.in-toto+json',
};
const policy = {
  schema_version: 2,
  purpose: 'agent-bot/qualification-policy/v2',
  policy_base64url: Buffer.from(JSON.stringify({
    contract: 'control/v2',
    kind: 'qualification-trust-policy',
    release_workflow: {
      repository: 'true-line/soul-adapters',
      workflow_path: '.github/workflows/release-qualification.yml',
      source_commit: commit,
      oidc_issuer: 'https://token.actions.githubusercontent.com',
      certificate_identity: 'https://github.com/true-line/soul-adapters/.github/workflows/release-qualification.yml@refs/heads/main',
    },
    souls_build_workflow: soulsWorkflow,
  })).toString('base64url'),
};
policy.policy_sha256 = sha(Buffer.from(policy.policy_base64url, 'base64url'));
preflight.policy_sha256 = policy.policy_sha256;
preflight.policy_envelope_sha256 = sha(Buffer.from(JSON.stringify(policy)));
const now = new Date('2026-11-21T00:00:00.000Z');
const expiry = '2026-12-21T00:00:00Z';
const build = (q = qualification, p = preflight, owner = policy,
  protectedBytes = protectedEnvelope, cooperativeBytes = cooperativeEnvelope,
  source = commit, until = expiry, index = indexBytes, prefix = approvedPrefix) =>
  buildStatement(q, p, Buffer.from(JSON.stringify(owner)), protectedBytes,
    cooperativeBytes, index, prefix, source, until, now);

// Exported only so the release disclosure review can inspect the exact JSON
// bytes produced by this synthetic, nonprivate test input.
export const syntheticPublicStatement = build();

test('v2 statement binds qualification, source, protected v2 and cooperative v1 payloads and envelopes', () => {
  const statement = build();
  assert.equal(statement.predicateType,
    'https://true-line.ai/soul-contracts/adapter/v2/macos-protected-qualification');
  assert.equal(statement.subject[0].digest.sha256, sha(qualification).slice(7));
  assert.equal(statement.predicate.release.source_commit, commit);
  assert.deepEqual(statement.predicate.host_reports, qualificationRecord.conformance);
  assert.deepEqual(statement.predicate.souls_build, soulsBuild);
});

test('changed qualification, raw report or signature envelope cannot be stated', () => {
  assert.throws(() => build(qualification, { ...preflight, schema_version: 1 }));
  assert.throws(() => build(Buffer.concat([qualification, Buffer.from(' ')])));
  assert.throws(() => build(qualification, { ...preflight, protected_report_sha256: cooperativeSHA }));
  assert.throws(() => build(qualification, preflight, policy, Buffer.from('different protected bytes')));
  assert.throws(() => build(qualification, preflight, policy, protectedEnvelope,
    Buffer.from('different cooperative bytes')));
  assert.throws(() => build(qualification,
    { ...preflight, protected_envelope_sha256: sha(Buffer.from('different envelope')) }));
});

test('v1 protected purpose and wrong report location refuse a protected statement', () => {
  for (const change of [
    { report_schema_version: 1, signature_purpose: 'agent-bot/qualification-host-report/v1' },
    { payload: { uri: 'qualification/other.report.json', sha256: protectedSHA } },
  ]) {
    const changed = Buffer.from(JSON.stringify({ ...qualificationRecord, conformance: {
      ...qualificationRecord.conformance,
      protected_report: { ...qualificationRecord.conformance.protected_report, ...change },
    } }));
    assert.throws(() => build(changed, { ...preflight, qualification_sha256: sha(changed) }));
  }
});

test('missing or malformed Souls source and receipt claims cannot be stated', () => {
  for (const changedBuild of [undefined,
    { ...soulsBuild, source_tree: 'not-a-git-tree' },
    { ...soulsBuild, receipt: { ...soulsBuild.receipt, sha256: 'sha256:' + 'x'.repeat(64) } },
  ]) {
    const changed = Buffer.from(JSON.stringify({ ...qualificationRecord, souls_build: changedBuild }));
    assert.throws(() => build(changed, { ...preflight, qualification_sha256: sha(changed) }));
  }
});

test('private Souls fields and unreviewed receipt URI cannot enter a public Statement', () => {
  const alteredBuilds = [
    { ...soulsBuild, private_sentinel: 'DO_NOT_PUBLISH_PRIVATE_SENTINEL' },
    { ...soulsBuild, receipt: { ...soulsBuild.receipt,
      private_sentinel: 'DO_NOT_PUBLISH_PRIVATE_SENTINEL' } },
    { ...soulsBuild, receipt: { ...soulsBuild.receipt,
      uri: soulsBuild.receipt.uri + '-private-sentinel' } },
    { ...soulsBuild, receipt: { ...soulsBuild.receipt,
      uri: soulsBuild.receipt.uri + '?token=DO_NOT_PUBLISH_PRIVATE_SENTINEL' } },
  ];
  for (const alteredBuild of alteredBuilds) {
    const changed = Buffer.from(JSON.stringify({ ...qualificationRecord, souls_build: alteredBuild }));
    assert.throws(() => build(changed, { ...preflight, qualification_sha256: sha(changed) }));
  }
  const statement = build();
  assert.equal(JSON.stringify(statement).includes('DO_NOT_PUBLISH_PRIVATE_SENTINEL'), false);
  assert.equal(statement.predicate.souls_build.receipt.uri,
    assetIndex.assets['souls-build-bundle'].uri);
  assert.match(statement.predicate.souls_build.receipt.uri,
    /^qualification:\/\/stage2a\/souls-build-bundle\/[0-9a-f]{64}$/);
  assert.equal(JSON.stringify(statement).includes('s3://'), false);
  assert.equal(JSON.stringify(statement).includes('r2://'), false);
  assert.deepEqual(Object.keys(statement.predicate.souls_build).sort(),
    Object.keys(soulsBuild).sort());
});

test('Statement rejects missing or changed reviewed asset index', () => {
  assert.throws(() => build(qualification, preflight, policy, protectedEnvelope,
    cooperativeEnvelope, commit, expiry, null));
  const altered = structuredClone(assetIndex);
  altered.assets['souls-build-bundle'].uri = approvedPrefix + 'souls-build-bundle/' + '0'.repeat(64);
  assert.throws(() => build(qualification, preflight, policy, protectedEnvelope,
    cooperativeEnvelope, commit, expiry, Buffer.from(JSON.stringify(altered, null, 2) + '\n')));
  assert.throws(() => build(qualification, preflight, policy, protectedEnvelope,
    cooperativeEnvelope, commit, expiry, indexBytes, 's3://other-bucket/'));
});

test('Souls source, package and receipt must match authenticated preflight', () => {
  for (const field of ['souls_build_bundle_sha256', 'souls_package_sha256', 'souls_source_tree']) {
    assert.throws(() => build(qualification, { ...preflight, [field]: undefined }));
    assert.throws(() => build(qualification, { ...preflight, [field]: 'changed' }));
  }
});

test('different owner-pinned release identity, source or expiry is refused', () => {
  const decoded = JSON.parse(Buffer.from(policy.policy_base64url, 'base64url').toString('utf8'));
  decoded.release_workflow.certificate_identity =
    'https://github.com/true-line/soul-adapters/.github/workflows/other.yml@refs/heads/main';
  assert.throws(() => build(qualification, preflight,
    { policy_base64url: Buffer.from(JSON.stringify(decoded)).toString('base64url') }));
  assert.throws(() => build(qualification, preflight, policy, protectedEnvelope, cooperativeEnvelope, 'main'));
  assert.throws(() => build(qualification, preflight, policy, protectedEnvelope, cooperativeEnvelope, commit, now.toISOString()));
  assert.throws(() => build(qualification, preflight, policy, protectedEnvelope, cooperativeEnvelope, commit, 'not-a-time'));
});

test('v1 policy and unpinned Souls workflow or source tree are refused', () => {
  assert.throws(() => build(qualification, preflight,
    { ...policy, schema_version: 1, purpose: 'agent-bot/qualification-policy/v1' }));
  const decoded = JSON.parse(Buffer.from(policy.policy_base64url, 'base64url').toString('utf8'));
  for (const value of [undefined, { ...soulsWorkflow, source_tree: 'f'.repeat(40) },
    { ...soulsWorkflow, certificate_identity: 'https://github.com/other/workflow@refs/heads/main' }]) {
    const changed = { ...decoded, souls_build_workflow: value };
    assert.throws(() => build(qualification, preflight, {
      ...policy,
      policy_base64url: Buffer.from(JSON.stringify(changed)).toString('base64url'),
    }));
  }
});

test('a swapped owner policy is refused even when release identity remains valid', () => {
  const decoded = JSON.parse(Buffer.from(policy.policy_base64url, 'base64url').toString('utf8'));
  const altered = Buffer.from(JSON.stringify({ ...decoded, policy_revision: 2 }));
  assert.throws(() => build(qualification, preflight, {
    ...policy,
    policy_base64url: altered.toString('base64url'),
    policy_sha256: sha(altered),
  }));
});

test('changed owner envelope bytes are refused before statement signing', () => {
  assert.throws(() => build(qualification, {
    ...preflight,
    policy_envelope_sha256: sha(Buffer.from('another envelope')),
  }));
});
