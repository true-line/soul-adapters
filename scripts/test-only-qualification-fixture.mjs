#!/usr/bin/env node
// Test-only fixture producer. Never write private keys or install this policy.
import assert from 'node:assert/strict';
import {
  createHash, generateKeyPairSync, sign,
} from 'node:crypto';
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';

const [protectedPath, cooperativePath, trustedRootPath, outputDir] = process.argv.slice(2);
const sourceCommit = process.env.GITHUB_SHA;
const sourceRef = process.env.GITHUB_REF;
if (process.argv.length !== 6 || !/^[0-9a-f]{40}$/.test(sourceCommit ?? '') ||
    !/^refs\/pull\/[0-9]+\/merge$/.test(sourceRef ?? '')) {
  throw new Error('usage: GITHUB_SHA and PR GITHUB_REF required for test-only fixture generation');
}
const WORKFLOW = '.github/workflows/qualification-fixture.yml';
const REPOSITORY = 'true-line/soul-adapters';
const IDENTITY = 'https://github.com/' + REPOSITORY + '/' + WORKFLOW + '@' + sourceRef;
const ISSUER = 'https://token.actions.githubusercontent.com';
const HOST_PURPOSE = 'agent-bot/qualification-host-report/v1';
const POLICY_PURPOSE = 'agent-bot/qualification-policy/v1';
const PREDICATE = 'https://true-line.ai/soul-contracts/adapter/v1/macos-protected-qualification';
const hash = bytes => 'sha256:' + createHash('sha256').update(bytes).digest('hex');
const b64url = bytes => Buffer.from(bytes).toString('base64url');
const utc = date => date.toISOString().replace('.000Z', 'Z');
const now = new Date();
const recordedAt = utc(new Date(now.getTime() - 60000));
const expiry = utc(new Date(now.getTime() + 3600000));
const policyExpiry = utc(new Date(now.getTime() + 7200000));

function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value !== null && typeof value === 'object') {
    return '{' + Object.keys(value).sort().map(
      key => JSON.stringify(key) + ':' + canonical(value[key]),
    ).join(',') + '}';
  }
  return JSON.stringify(value);
}
function bytes(value) {
  const text = canonical(value);
  if (!/^[\x00-\x7f]*$/.test(text)) throw new Error('fixture must use ASCII JCS subset');
  return Buffer.from(text);
}
function save(name, value) {
  const raw = bytes(value);
  writeFileSync(outputDir + '/' + name, raw);
  return raw;
}
function ref(uri, sha256) { return { uri, sha256 }; }

const protectedReport = JSON.parse(readFileSync(protectedPath, 'utf8'));
const cooperativeReport = JSON.parse(readFileSync(cooperativePath, 'utf8'));
protectedReport.recorded_at = recordedAt;
cooperativeReport.recorded_at = recordedAt;
protectedReport.run_id = 'fixture-protected-' + sourceCommit.slice(0, 12);
cooperativeReport.run_id = 'fixture-cooperative-' + sourceCommit.slice(0, 12);
assert.equal(protectedReport.profile_in_force, 'managed-protected');
assert.equal(cooperativeReport.profile_in_force, 'managed-cooperative');
assert.equal(protectedReport.host.id, cooperativeReport.host.id);
assert.notEqual(protectedReport.run_id, cooperativeReport.run_id);

mkdirSync(outputDir, { recursive: true });
mkdirSync(outputDir + '/qualification', { recursive: true });
const { publicKey: hostPublic, privateKey: hostPrivate } = generateKeyPairSync(
  'ec', { namedCurve: 'prime256v1' },
);
const hostSPKI = hostPublic.export({ type: 'spki', format: 'der' });
function signHost(report, stem) {
  const raw = bytes(report);
  writeFileSync(outputDir + '/qualification/' + stem + '.report.json', raw);
  const preimage = Buffer.concat([Buffer.from(HOST_PURPOSE), Buffer.from([0]), raw]);
  const signature = sign('sha256', preimage, {
    key: hostPrivate, dsaEncoding: 'ieee-p1363',
  });
  assert.equal(signature.length, 64);
  save(stem + '-envelope.json', {
    schema_version: 1,
    purpose: HOST_PURPOSE,
    host_id: report.host.id,
    signer_id: 'fixture-designated-host',
    report_sha256: hash(raw),
    report_base64url: b64url(raw),
    signature_base64url: b64url(signature),
  });
  return raw;
}
const protectedRaw = signHost(protectedReport, 'protected');
const cooperativeRaw = signHost(cooperativeReport, 'cooperative');

const {
  gates: _gates, checks, outcome, run_id, suite_revision, ...qualificationCommon
} = protectedReport;
const qualification = {
  ...qualificationCommon,
  conformance: {
    suite_revision, run_id, outcome, checks,
    report: ref('qualification/protected.report.json', hash(protectedRaw)),
  },
};
const qualificationRaw = save('qualification.json', qualification);
const trustedRootRaw = readFileSync(trustedRootPath);
copyFileSync(trustedRootPath, outputDir + '/trusted-root.json');
const { publicKey: ownerPublic, privateKey: ownerPrivate } = generateKeyPairSync('ed25519');
const ownerSPKI = ownerPublic.export({ type: 'spki', format: 'der' });
const ownerRoot = ownerSPKI.subarray(-32);
assert.equal(ownerRoot.length, 32);
writeFileSync(outputDir + '/owner-root.hex', ownerRoot.toString('hex') + '\n');
writeFileSync(outputDir + '/owner-key-id.txt', 'fixture-owner-root\n');
const policy = {
  contract: 'control/v1',
  kind: 'qualification-trust-policy',
  policy_revision: 1,
  owner_key_id: 'fixture-owner-root',
  issued_at: recordedAt,
  expires_at: policyExpiry,
  release_workflow: {
    id: 'fixture-pr-workflow',
    repository: REPOSITORY,
    workflow_path: WORKFLOW,
    source_commit: sourceCommit,
    oidc_issuer: ISSUER,
    certificate_identity: IDENTITY,
    trust_root_sha256: hash(trustedRootRaw),
  },
  evidence_signers: [{
    id: 'fixture-designated-host',
    host_id: protectedReport.host.id,
    algorithm: 'ES256',
    public_key_spki_der_base64: hostSPKI.toString('base64'),
  }],
  revocation_snapshot: {
    updated_at: utc(now),
    revoked_attestation_sha256: [],
    revoked_evidence_signer_ids: [],
    revoked_release_workflow_ids: [],
  },
  max_revocation_age_seconds: 3600,
};
const policyRaw = bytes(policy);
const policySignature = sign(null, Buffer.concat([
  Buffer.from(POLICY_PURPOSE), Buffer.from([0]), policyRaw,
]), ownerPrivate);
save('policy-envelope.json', {
  schema_version: 1,
  purpose: POLICY_PURPOSE,
  owner_key_id: policy.owner_key_id,
  policy_sha256: hash(policyRaw),
  policy_base64url: b64url(policyRaw),
  signature_base64url: b64url(policySignature),
});
const statement = {
  _type: 'https://in-toto.io/Statement/v1',
  subject: [{
    name: 'qualification.json',
    digest: { sha256: hash(qualificationRaw).slice(7) },
  }],
  predicateType: PREDICATE,
  predicate: {
    host_reports: [
      ref('qualification/protected.report.json', hash(protectedRaw)),
      ref('qualification/cooperative.report.json', hash(cooperativeRaw)),
    ],
    expires_at: expiry,
    release: {
      source_repository: REPOSITORY,
      source_commit: sourceCommit,
      workflow: WORKFLOW,
    },
  },
};
save('statement.json', statement);
save('source.json', {
  test_only: true,
  release_source_commit: sourceCommit,
  workflow_ref: sourceRef,
  workflow: WORKFLOW,
  certificate_identity: IDENTITY,
  oidc_issuer: ISSUER,
  contracts_fixture_source: process.env.CONTRACTS_COMMIT ?? '',
  generated_at: utc(now),
});
process.stdout.write(JSON.stringify({
  qualification_sha256: hash(qualificationRaw),
  protected_report_sha256: hash(protectedRaw),
  cooperative_report_sha256: hash(cooperativeRaw),
  owner_root_sha256: hash(ownerRoot),
  sigstore_root_sha256: hash(trustedRootRaw),
}) + '\n');
