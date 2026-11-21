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

export function buildStatement(qualificationBytes, preflight, sourceCommit, expiresAt, now = new Date()) {
  if (!Buffer.isBuffer(qualificationBytes) || qualificationBytes.length === 0 ||
      qualificationBytes.length > 1 << 20 || !COMMIT.test(sourceCommit) ||
      !preflight || preflight.qualification_sha256 !== digest(qualificationBytes) ||
      preflight.protected_profile !== 'managed-protected' ||
      preflight.cooperative_profile !== 'managed-cooperative') {
    throw new Error('release inputs do not match verified qualification bytes');
  }
  const expiry = new Date(expiresAt);
  if (!Number.isFinite(expiry.getTime()) || expiry <= now || expiry.toISOString() !== expiresAt) {
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
  if (process.argv.length !== 6) {
    throw new Error('usage: qualification-statement.mjs QUALIFICATION PREFLIGHT SOURCE_COMMIT EXPIRES_AT');
  }
  const [qualificationPath, preflightPath, commit, expires] = process.argv.slice(2);
  const result = buildStatement(
    readFileSync(qualificationPath),
    JSON.parse(readFileSync(preflightPath, 'utf8')),
    commit,
    expires,
  );
  process.stdout.write(JSON.stringify(result) + '\n');
}
