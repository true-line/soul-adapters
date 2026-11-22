import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import { buildStatementV3 } from './qualification-statement-v3.mjs';

const hash = bytes => 'sha256:' + createHash('sha256').update(bytes).digest('hex');
const jcs = value => Buffer.from(JSON.stringify(value, (_key, item) =>
  item && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.entries(item).sort(([a], [b]) =>
      a < b ? -1 : a > b ? 1 : 0))
    : item));
const ref = (name, bytes) => ({ uri: `qualification://stage2a/${name}/${hash(bytes).slice(7)}`,
  sha256: hash(bytes) });
const machineEnvelope = (purpose, payload) => {
  const payloadBytes = jcs(payload);
  return jcs({ schema_version: 1, purpose,
    issuer_public_key_base64url: Buffer.alloc(32, 7).toString('base64url'),
    payload_sha256: hash(payloadBytes), payload_base64url: payloadBytes.toString('base64url'),
    signature_base64url: Buffer.alloc(64, 8).toString('base64url') });
};
const frozenSoulBytes = Buffer.from('synthetic frozen Soul');
const soulsPackageBytes = Buffer.from('synthetic Souls package');
const soulsBuildBundleBytes = Buffer.from('synthetic Souls build bundle');
const soulArtifact = hash(frozenSoulBytes).slice(7);
const sourceCommit = 'c'.repeat(40);
const reportPurpose = 'agent-bot/qualification-host-report/v3';

function run(profile, suffix) {
  const report = {
    schema_version: 3, profile_in_force: profile,
    measurement_binding: { soul_artifact_sha256: soulArtifact },
    recorded_at: '2026-11-22T06:02:00Z',
    run_evidence: {
      reservation: { uri: `fixture:${suffix}-reservation`,
        sha256: hash(Buffer.from(`${suffix}-reservation`)) },
      finalization: { uri: `fixture:${suffix}-finalization`,
        sha256: hash(Buffer.from(`${suffix}-finalization`)) },
      challenge_sha256: hash(Buffer.from(`${suffix}-challenge`)),
    },
  };
  const pre = machineEnvelope('agent-bot/stage2a-execution-completion/v1', {
    schema_version: 1, kind: 'stage2a-execution-completion',
    report_candidate_sha256: hash(jcs(report)),
    reservation_sha256: report.run_evidence.reservation.sha256,
    finalization_sha256: report.run_evidence.finalization.sha256,
    challenge_sha256: report.run_evidence.challenge_sha256,
    measurement_binding_sha256: hash(jcs(report.measurement_binding)),
    completed_at: '2026-11-22T06:01:00Z',
  });
  report.run_evidence.execution_completion = ref(`${suffix}-execution`, pre);
  const reportBytes = jcs(report);
  const envelope = jcs({ schema_version: 3, purpose: reportPurpose,
    host_id: 'fixture-host', signer_id: 'fixture-signer',
    report_sha256: hash(reportBytes), report_base64url: reportBytes.toString('base64url'),
    signature_base64url: Buffer.alloc(64, 9).toString('base64url') });
  const post = machineEnvelope('agent-bot/stage2a-report-commitment/v1', {
    schema_version: 1, kind: 'stage2a-report-commitment',
    execution_completion_sha256: hash(pre),
    report_payload_sha256: hash(reportBytes), report_envelope_sha256: hash(envelope),
    committed_at: '2026-11-22T06:03:00Z',
  });
  return {
    files: { report: reportBytes, envelope, executionCompletion: pre, completedRun: post },
    claim: { report_schema_version: 3, signature_purpose: reportPurpose,
      payload: ref(`${suffix}-report`, reportBytes), signature: ref(`${suffix}-envelope`, envelope),
      completed_run: ref(`${suffix}-commitment`, post) },
    verified: { profile, report_sha256: hash(reportBytes), envelope_sha256: hash(envelope),
      execution_completion_sha256: hash(pre), completed_run_sha256: hash(post) },
  };
}

function scenario() {
  const protectedRun = run('managed-protected', 'protected');
  const cooperativeRun = run('managed-cooperative', 'cooperative');
  const soulsBuild = {
    source_repository: 'true-line/agent-bot-souls', source_commit: '1'.repeat(40),
    source_tree: '2'.repeat(40), builder_sha256: `sha256:${'3'.repeat(64)}`,
    toolchain_sha256: `sha256:${'4'.repeat(64)}`,
    workflow: '.github/workflows/souls-build-receipt.yml', run_id: '123456789', run_attempt: 1,
    package_sha256: hash(soulsPackageBytes),
    receipt: ref('souls-build-bundle', soulsBuildBundleBytes),
  };
  const qualification = { schema_version: 3, profile_in_force: 'managed-protected',
    soul_artifact_sha256: soulArtifact, souls_build: soulsBuild,
    recorded_at: '2026-11-22T06:04:00Z',
    conformance: { protected_report: protectedRun.claim,
      cooperative_report: cooperativeRun.claim } };
  const policy = { contract: 'control/v3', kind: 'qualification-trust-policy',
    frozen_soul_release_provenance_enabled: false,
    frozen_soul_mapping: { soul_id: 'agent_11111111-1111-1111-1111-111111111111',
      artifact_sha256: soulArtifact,
      claimed_definition_digest: `sha256:${'8'.repeat(64)}`,
      definition_measurement: `sha256:${'9'.repeat(64)}` },
    release_workflow: { repository: 'true-line/soul-adapters',
      workflow_path: '.github/workflows/release-qualification.yml', source_commit: sourceCommit,
      oidc_issuer: 'https://token.actions.githubusercontent.com',
      certificate_identity: 'https://github.com/true-line/soul-adapters/.github/workflows/release-qualification.yml@refs/heads/main' },
    souls_build_workflow: { repository: soulsBuild.source_repository,
      workflow_path: soulsBuild.workflow, source_commit: soulsBuild.source_commit,
      source_tree: soulsBuild.source_tree, builder_sha256: soulsBuild.builder_sha256,
      toolchain_sha256: soulsBuild.toolchain_sha256,
      receipt_predicate_type: 'https://true-line.ai/soul-contracts/adapter/v2/souls-build',
      bundle_media_type: 'application/vnd.dev.sigstore.bundle.v0.3+json',
      dsse_payload_type: 'application/vnd.in-toto+json',
      oidc_issuer: 'https://token.actions.githubusercontent.com',
      certificate_identity: `https://github.com/true-line/agent-bot-souls/${soulsBuild.workflow}@refs/heads/main`,
      trust_root_sha256: `sha256:${'7'.repeat(64)}` } };
  const policyBytes = jcs(policy);
  const policyEnvelopeBytes = jcs({ schema_version: 3,
    purpose: 'agent-bot/qualification-policy/v3', policy_sha256: hash(policyBytes),
    policy_base64url: policyBytes.toString('base64url') });
  const qualificationBytes = jcs(qualification);
  const privateBytes = {
    qualification: qualificationBytes,
    'policy-envelope': policyEnvelopeBytes,
    'protected-report': protectedRun.files.report,
    'protected-envelope': protectedRun.files.envelope,
    'protected-execution': protectedRun.files.executionCompletion,
    'protected-commitment': protectedRun.files.completedRun,
    'cooperative-report': cooperativeRun.files.report,
    'cooperative-envelope': cooperativeRun.files.envelope,
    'cooperative-execution': cooperativeRun.files.executionCompletion,
    'cooperative-commitment': cooperativeRun.files.completedRun,
    'souls-package': soulsPackageBytes,
    'souls-build-bundle': soulsBuildBundleBytes,
    'frozen-soul': frozenSoulBytes,
  };
  const assets = Object.fromEntries(Object.entries(privateBytes).map(([role, bytes]) =>
    [role, { ...ref(role, bytes), byte_length: bytes.length }]));
  const assetIndexBytes = Buffer.from(JSON.stringify({ schema_version: 2, assets }, null, 2) + '\n');
  const privateAssetVerification = { schema_version: 2, index_sha256: hash(assetIndexBytes),
    assets: Object.fromEntries(Object.entries(privateBytes).map(([role, bytes]) =>
      [role, { sha256: hash(bytes), byte_length: bytes.length }])) };
  return { qualificationBytes, policyEnvelopeBytes, assetIndexBytes, privateAssetVerification,
    runs: { protected: protectedRun.files, cooperative: cooperativeRun.files },
    machineVerification: { schema_version: 3,
      verification_contract: 'agent-bot/machine-qualification-preflight/v3',
      qualification_sha256: hash(qualificationBytes),
      policy_envelope_sha256: hash(policyEnvelopeBytes), policy_sha256: hash(policyBytes),
      soul_artifact_sha256: soulArtifact,
      souls_build_bundle_sha256: soulsBuild.receipt.sha256,
      souls_package_sha256: soulsBuild.package_sha256,
      souls_source_tree: soulsBuild.source_tree,
      protected: protectedRun.verified, cooperative: cooperativeRun.verified },
    sourceCommit, expiresAt: '2026-12-01T00:00:00Z',
    now: new Date('2026-11-22T07:00:00Z') };
}

test('synthetic v3 Statement joins distinct completed runs and frozen Soul digest', () => {
  const input = scenario();
  const statement = buildStatementV3(input);
  assert.equal(statement.predicateType,
    'https://true-line.ai/soul-contracts/adapter/v3/macos-measured-qualification');
  assert.equal(statement.subject[0].digest.sha256, hash(input.qualificationBytes).slice(7));
  assert.equal(statement.predicate.soul_artifact_sha256, soulArtifact);
  assert.notEqual(statement.predicate.host_reports.protected_report.completed_run.sha256,
    statement.predicate.host_reports.cooperative_report.completed_run.sha256);
});

test('v3 Statement refuses a changed reviewed asset index or byte verification', () => {
  const changedIndex = scenario();
  const index = JSON.parse(changedIndex.assetIndexBytes);
  index.assets['frozen-soul'].sha256 = `sha256:${'f'.repeat(64)}`;
  changedIndex.assetIndexBytes = Buffer.from(JSON.stringify(index, null, 2) + '\n');
  assert.throws(() => buildStatementV3(changedIndex));

  const changedVerification = scenario();
  changedVerification.privateAssetVerification.assets['protected-commitment'].byte_length++;
  assert.throws(() => buildStatementV3(changedVerification));

  const changedBytes = scenario();
  changedBytes.runs.cooperative.completedRun = Buffer.concat([
    changedBytes.runs.cooperative.completedRun, Buffer.from(' ')]);
  assert.throws(() => buildStatementV3(changedBytes));
});

test('v3 builder refuses absent Machine verification and legacy policy', () => {
  const input = scenario();
  assert.throws(() => buildStatementV3({ ...input, machineVerification: null }));
  assert.throws(() => buildStatementV3({ ...input, machineVerification: {
    ...input.machineVerification, schema_version: 2 } }));
  const changed = scenario();
  changed.policyEnvelopeBytes = jcs({ schema_version: 2, purpose: 'agent-bot/qualification-policy/v2' });
  assert.throws(() => buildStatementV3(changed));
});

test('v3 builder refuses changed report, completion, commitment and policy bytes', () => {
  for (const field of ['report', 'envelope', 'executionCompletion', 'completedRun']) {
    const input = scenario();
    input.runs.protected[field] = Buffer.concat([input.runs.protected[field], Buffer.from(' ')]);
    assert.throws(() => buildStatementV3(input), field);
  }
  const input = scenario();
  input.policyEnvelopeBytes = Buffer.concat([input.policyEnvelopeBytes, Buffer.from(' ')]);
  assert.throws(() => buildStatementV3(input));
});

test('v3 builder refuses reused completed-run reference and private Souls fields', () => {
  const input = scenario();
  const qualification = JSON.parse(input.qualificationBytes);
  qualification.conformance.cooperative_report.completed_run =
    qualification.conformance.protected_report.completed_run;
  input.qualificationBytes = jcs(qualification);
  input.machineVerification.qualification_sha256 = hash(input.qualificationBytes);
  assert.throws(() => buildStatementV3(input));
  const other = scenario();
  const altered = JSON.parse(other.qualificationBytes);
  altered.souls_build.private_token = 'DO_NOT_PUBLISH';
  other.qualificationBytes = jcs(altered);
  other.machineVerification.qualification_sha256 = hash(other.qualificationBytes);
  assert.throws(() => buildStatementV3(other));
});

test('v3 builder refuses a private public reference and future Machine record', () => {
  const input = scenario();
  const qualification = JSON.parse(input.qualificationBytes);
  qualification.conformance.protected_report.completed_run.uri = 's3://private-bucket/secret';
  input.qualificationBytes = jcs(qualification);
  input.machineVerification.qualification_sha256 = hash(input.qualificationBytes);
  assert.throws(() => buildStatementV3(input));

  const future = scenario();
  future.now = new Date('2026-11-22T06:02:30Z');
  assert.throws(() => buildStatementV3(future));
});

test('v3 builder refuses a post-sign commitment to different report bytes', () => {
  const input = scenario();
  const old = JSON.parse(input.runs.protected.completedRun);
  const payload = JSON.parse(Buffer.from(old.payload_base64url, 'base64url'));
  payload.report_payload_sha256 = `sha256:${'f'.repeat(64)}`;
  const changed = machineEnvelope('agent-bot/stage2a-report-commitment/v1', payload);
  input.runs.protected.completedRun = changed;
  input.machineVerification.protected.completed_run_sha256 = hash(changed);
  const qualification = JSON.parse(input.qualificationBytes);
  qualification.conformance.protected_report.completed_run = ref('protected-commitment', changed);
  input.qualificationBytes = jcs(qualification);
  input.machineVerification.qualification_sha256 = hash(input.qualificationBytes);
  assert.throws(() => buildStatementV3(input));
});
