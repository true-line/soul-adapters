#!/usr/bin/env node
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const SHA = /^sha256:[0-9a-f]{64}$/;
const COMMIT = /^[0-9a-f]{40}$/;
const PREDICATE = 'https://true-line.ai/soul-contracts/adapter/v1/macos-protected-qualification';
const WORKFLOW = '.github/workflows/release-qualification.yml';

function digest(bytes) {
  return 'sha256:' + createHash('sha256').update(bytes).digest('hex');
}

export function buildStatement(qualificationBytes, preflight, policyEnvelope, sourceCommit, expiresAt, now = new Date()) {
  if (!Buffer.isBuffer(qualificationBytes) || qualificationBytes.length === 0 ||
      qualificationBytes.length > 1 << 20 || !COMMIT.test(sourceCommit) ||
      !preflight || preflight.qualification_sha256 !== digest(qualificationBytes) ||
      preflight.protected_profile !== 'managed-protected' ||
      preflight.cooperative_profile !== 'managed-cooperative') {
    throw new Error('release inputs do not match verified qualification bytes');
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
  let policy;
  try {
    policy = JSON.parse(policyBytes.toString('utf8'));
  } catch {
    throw new Error('owner policy payload is invalid JSON');
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
  if (qualification.profile_in_force !== 'managed-protected') {
    throw new Error('qualification.json does not name the protected profile');
  }
  const protectedSHA = preflight.protected_report_sha256;
  const cooperativeSHA = preflight.cooperative_report_sha256;
  if (!SHA.test(protectedSHA) || !SHA.test(cooperativeSHA) || protectedSHA === cooperativeSHA ||
      qualification.conformance?.report?.sha256 !== protectedSHA) {
    throw new Error('verified protected report does not match qualification.json');
  }
  return {
    _type: 'https://in-toto.io/Statement/v1',
    subject: [{ name: 'qualification.json', digest: { sha256: digest(qualificationBytes).slice(7) } }],
    predicateType: PREDICATE,
    predicate: {
      host_reports: [
        { uri: 'qualification/protected.report.json', sha256: protectedSHA },
        { uri: 'qualification/cooperative.report.json', sha256: cooperativeSHA },
      ],
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
  if (process.argv.length !== 7) {
    throw new Error('usage: qualification-statement.mjs QUALIFICATION PREFLIGHT POLICY_ENVELOPE SOURCE_COMMIT EXPIRES_AT');
  }
  const [qualificationPath, preflightPath, policyPath, commit, expires] = process.argv.slice(2);
  const result = buildStatement(
    readFileSync(qualificationPath),
    JSON.parse(readFileSync(preflightPath, 'utf8')),
    JSON.parse(readFileSync(policyPath, 'utf8')),
    commit,
    expires,
  );
  process.stdout.write(JSON.stringify(result) + '\n');
}
