#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { validatePrivateAssetIndex } from './verify-private-assets.mjs';

const SHA = /^sha256:[0-9a-f]{64}$/;
const COMMIT = /^[0-9a-f]{40}$/;
const PREDICATE = 'https://true-line.ai/soul-contracts/adapter/v2/macos-protected-qualification';
const SOULS_PREDICATE = 'https://true-line.ai/soul-contracts/adapter/v2/souls-build';
const WORKFLOW = '.github/workflows/release-qualification.yml';
const PURPOSES = {
  protected_report: { version: 2, purpose: 'agent-bot/qualification-host-report/v2',
    payload: 'qualification/protected.report.json', signature: 'protected-envelope.json' },
  cooperative_report: { version: 1, purpose: 'agent-bot/qualification-host-report/v1',
    payload: 'qualification/cooperative.report.json', signature: 'cooperative-envelope.json' },
};

function digest(bytes) {
  return 'sha256:' + createHash('sha256').update(bytes).digest('hex');
}

function exactKeys(value, keys) {
  return value !== null && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).length === keys.length &&
    keys.every(key => Object.hasOwn(value, key));
}

function boundReport(value, binding, payloadSHA, envelopeSHA, envelopeBytes) {
  if (!Buffer.isBuffer(envelopeBytes) || envelopeBytes.length === 0 ||
      envelopeBytes.length > (2 << 20) || !SHA.test(payloadSHA) ||
      !SHA.test(envelopeSHA) || envelopeSHA !== digest(envelopeBytes) ||
      value?.report_schema_version !== binding.version ||
      value?.signature_purpose !== binding.purpose ||
      value?.payload?.uri !== binding.payload || value?.payload?.sha256 !== payloadSHA ||
      value?.signature?.uri !== binding.signature ||
      value?.signature?.sha256 !== envelopeSHA) {
    throw new Error('verified report and envelope do not match qualification.json');
  }
  return {
    report_schema_version: binding.version,
    signature_purpose: binding.purpose,
    payload: { uri: binding.payload, sha256: payloadSHA },
    signature: { uri: binding.signature, sha256: envelopeSHA },
  };
}

export function buildStatement(qualificationBytes, preflight, policyEnvelopeBytes,
    protectedEnvelopeBytes, cooperativeEnvelopeBytes, assetIndexBytes, approvedAssetPrefix,
    sourceCommit, expiresAt, now = new Date()) {
  if (!Buffer.isBuffer(qualificationBytes) || qualificationBytes.length === 0 ||
      qualificationBytes.length > (1 << 20) || !COMMIT.test(sourceCommit) ||
      !preflight || preflight.schema_version !== 2 ||
      preflight.qualification_sha256 !== digest(qualificationBytes) ||
      preflight.protected_profile !== 'managed-protected' ||
      preflight.cooperative_profile !== 'managed-cooperative') {
    throw new Error('release inputs do not match verified qualification bytes');
  }
  if (!Buffer.isBuffer(policyEnvelopeBytes) || policyEnvelopeBytes.length === 0 ||
      policyEnvelopeBytes.length > (2 << 20) ||
      preflight.policy_envelope_sha256 !== digest(policyEnvelopeBytes)) {
    throw new Error('owner policy envelope differs from authenticated preflight');
  }
  let policyEnvelope;
  try {
    policyEnvelope = JSON.parse(policyEnvelopeBytes.toString('utf8'));
  } catch {
    throw new Error('owner policy envelope is invalid JSON');
  }
  // Machine preflight authenticates the envelope; this comparison binds its
  // decoded release identity to the current workflow before statement signing.
  const encoded = policyEnvelope?.policy_base64url;
  if (typeof encoded !== 'string' || !/^[A-Za-z0-9_-]+$/.test(encoded)) {
    throw new Error('verified owner policy is required');
  }
  const policyBytes = Buffer.from(encoded, 'base64url');
  if (policyBytes.toString('base64url') !== encoded) {
    throw new Error('owner policy encoding is not canonical');
  }
  if (preflight.policy_sha256 !== digest(policyBytes) ||
      policyEnvelope.policy_sha256 !== preflight.policy_sha256) {
    throw new Error('owner policy differs from authenticated preflight');
  }
  let policy;
  try {
    policy = JSON.parse(policyBytes.toString('utf8'));
  } catch {
    throw new Error('owner policy payload is invalid JSON');
  }
  if (policyEnvelope.schema_version !== 2 ||
      policyEnvelope.purpose !== 'agent-bot/qualification-policy/v2' ||
      policy.contract !== 'control/v2' || policy.kind !== 'qualification-trust-policy') {
    throw new Error('verified v2 owner policy is required');
  }
  const release = policy.release_workflow;
  if (release?.repository !== 'true-line/soul-adapters' || release.workflow_path !== WORKFLOW ||
      release.source_commit !== sourceCommit ||
      release.oidc_issuer !== 'https://token.actions.githubusercontent.com' ||
      release.certificate_identity !==
        'https://github.com/true-line/soul-adapters/' + WORKFLOW + '@refs/heads/main') {
    throw new Error('verified owner policy does not pin this release workflow');
  }
  const expiry = new Date(expiresAt);
  const normalized = expiry.toISOString();
  const wholeSeconds = normalized.endsWith('.000Z') ? normalized.replace('.000Z', 'Z') : normalized;
  if (!Number.isFinite(expiry.getTime()) || expiry <= now ||
      (expiresAt !== normalized && expiresAt !== wholeSeconds)) {
    throw new Error('qualification expiry must be a future UTC instant');
  }
  let qualification;
  try {
    qualification = JSON.parse(qualificationBytes.toString('utf8'));
  } catch {
    throw new Error('qualification.json is invalid JSON');
  }
  if (qualification.schema_version !== 2 || qualification.profile_in_force !== 'managed-protected') {
    throw new Error('qualification.json does not name the protected profile');
  }
  const protectedSHA = preflight.protected_report_sha256;
  const cooperativeSHA = preflight.cooperative_report_sha256;
  if (!SHA.test(protectedSHA) || !SHA.test(cooperativeSHA) || protectedSHA === cooperativeSHA) {
    throw new Error('verified reports are missing or identical');
  }
  const hostReports = {
    protected_report: boundReport(qualification.conformance?.protected_report,
      PURPOSES.protected_report, protectedSHA, preflight.protected_envelope_sha256,
      protectedEnvelopeBytes),
    cooperative_report: boundReport(qualification.conformance?.cooperative_report,
      PURPOSES.cooperative_report, cooperativeSHA, preflight.cooperative_envelope_sha256,
      cooperativeEnvelopeBytes),
  };
  const soulsBuild = qualification.souls_build;
  if (!exactKeys(soulsBuild, ['source_repository', 'source_commit', 'source_tree',
    'builder_sha256', 'toolchain_sha256', 'workflow', 'run_id', 'run_attempt',
    'package_sha256', 'receipt']) ||
      !exactKeys(soulsBuild.receipt, ['uri', 'sha256']) ||
      soulsBuild.source_repository !== 'true-line/agent-bot-souls' ||
      !COMMIT.test(soulsBuild.source_commit) || !COMMIT.test(soulsBuild.source_tree) ||
      !SHA.test(soulsBuild.builder_sha256) || !SHA.test(soulsBuild.toolchain_sha256) ||
      !SHA.test(soulsBuild.package_sha256) || !SHA.test(soulsBuild.receipt?.sha256) ||
      typeof soulsBuild.receipt?.uri !== 'string' || !soulsBuild.receipt.uri ||
      typeof soulsBuild.workflow !== 'string' ||
      !/^\.github\/workflows\/[A-Za-z0-9._-]+\.ya?ml$/.test(soulsBuild.workflow) ||
      !/^[1-9][0-9]{0,19}$/.test(soulsBuild.run_id) ||
      !Number.isSafeInteger(soulsBuild.run_attempt) || soulsBuild.run_attempt < 1) {
    throw new Error('Souls build provenance is missing or malformed');
  }
  if (!Buffer.isBuffer(assetIndexBytes) || assetIndexBytes.length === 0 ||
      assetIndexBytes.length > (1 << 20)) {
    throw new Error('reviewed private asset index is required');
  }
  let assetIndex;
  try {
    assetIndex = JSON.parse(assetIndexBytes.toString('utf8'));
  } catch {
    throw new Error('reviewed private asset index is invalid JSON');
  }
  if (!assetIndexBytes.equals(Buffer.from(JSON.stringify(assetIndex, null, 2) + '\n'))) {
    throw new Error('reviewed private asset index is not canonical');
  }
  validatePrivateAssetIndex(assetIndex, approvedAssetPrefix);
  if (soulsBuild.receipt.uri !== assetIndex.assets['souls-build-bundle'].uri ||
      soulsBuild.receipt.sha256 !== assetIndex.assets['souls-build-bundle'].sha256 ||
      soulsBuild.package_sha256 !== assetIndex.assets['souls-package'].sha256) {
    throw new Error('Souls build does not match reviewed private asset index');
  }
  // Machine emits these fields after authenticating the separate build receipt
  // and measuring the Souls package. A claim in qualification.json alone is
  // insufficient to sign a release Statement.
  if (preflight.souls_build_bundle_sha256 !== soulsBuild.receipt.sha256 ||
      preflight.souls_package_sha256 !== soulsBuild.package_sha256 ||
      preflight.souls_source_tree !== soulsBuild.source_tree) {
    throw new Error('Souls build does not match authenticated preflight');
  }
  const soulsWorkflow = policy.souls_build_workflow;
  if (soulsWorkflow?.repository !== soulsBuild.source_repository ||
      soulsWorkflow.workflow_path !== soulsBuild.workflow ||
      soulsWorkflow.source_commit !== soulsBuild.source_commit ||
      soulsWorkflow.source_tree !== soulsBuild.source_tree ||
      soulsWorkflow.builder_sha256 !== soulsBuild.builder_sha256 ||
      soulsWorkflow.toolchain_sha256 !== soulsBuild.toolchain_sha256 ||
      soulsWorkflow.oidc_issuer !== 'https://token.actions.githubusercontent.com' ||
      soulsWorkflow.certificate_identity !==
        'https://github.com/true-line/agent-bot-souls/' + soulsBuild.workflow + '@refs/heads/main' ||
      soulsWorkflow.receipt_predicate_type !== SOULS_PREDICATE ||
      soulsWorkflow.bundle_media_type !== 'application/vnd.dev.sigstore.bundle.v0.3+json' ||
      soulsWorkflow.dsse_payload_type !== 'application/vnd.in-toto+json' ||
      !SHA.test(soulsWorkflow.trust_root_sha256)) {
    throw new Error('owner policy does not pin the authenticated Souls build');
  }
  return {
    _type: 'https://in-toto.io/Statement/v1',
    subject: [{ name: 'qualification.json', digest: { sha256: digest(qualificationBytes).slice(7) } }],
    predicateType: PREDICATE,
    predicate: {
      host_reports: hostReports,
      souls_build: {
        source_repository: soulsBuild.source_repository,
        source_commit: soulsBuild.source_commit,
        source_tree: soulsBuild.source_tree,
        builder_sha256: soulsBuild.builder_sha256,
        toolchain_sha256: soulsBuild.toolchain_sha256,
        workflow: soulsBuild.workflow,
        run_id: soulsBuild.run_id,
        run_attempt: soulsBuild.run_attempt,
        package_sha256: soulsBuild.package_sha256,
        receipt: { uri: soulsBuild.receipt.uri, sha256: soulsBuild.receipt.sha256 },
      },
      expires_at: expiresAt,
      release: {
        source_repository: 'true-line/soul-adapters',
        source_commit: sourceCommit,
        workflow: WORKFLOW,
      },
    },
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (process.argv.length !== 11) {
    throw new Error('usage: qualification-statement.mjs QUALIFICATION PREFLIGHT POLICY_ENVELOPE PROTECTED_ENVELOPE COOPERATIVE_ENVELOPE PRIVATE_ASSET_INDEX APPROVED_ASSET_PREFIX SOURCE_COMMIT EXPIRES_AT');
  }
  const [qualificationPath, preflightPath, policyPath, protectedPath, cooperativePath,
    assetIndexPath, approvedAssetPrefix, commit, expires] = process.argv.slice(2);
  const result = buildStatement(
    readFileSync(qualificationPath),
    JSON.parse(readFileSync(preflightPath, 'utf8')),
    readFileSync(policyPath),
    readFileSync(protectedPath),
    readFileSync(cooperativePath),
    readFileSync(assetIndexPath),
    approvedAssetPrefix,
    commit,
    expires,
  );
  process.stdout.write(JSON.stringify(result) + '\n');
}
