import { createHash } from 'node:crypto';
import { validatePrivateAssetIndexV3, V3_FILES } from './verify-private-assets-v3.mjs';

// Source-only v3 Statement construction. The release workflow must obtain an
// authenticated v3 Machine verification result before calling this function.
// No released authenticated v3 preflight CLI exists, so this has no release CLI.
const SHA = /^sha256:[0-9a-f]{64}$/;
const BARE_SHA = /^[0-9a-f]{64}$/;
const COMMIT = /^[0-9a-f]{40}$/;
const PURPOSE = 'agent-bot/qualification-host-report/v3';
const PREDICATE = 'https://true-line.ai/soul-contracts/adapter/v3/macos-measured-qualification';
const WORKFLOW = '.github/workflows/release-qualification.yml';

const digest = bytes => 'sha256:' + createHash('sha256').update(bytes).digest('hex');

function exactKeys(value, keys, label) {
  if (value === null || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).length !== keys.length ||
      !keys.every(key => Object.hasOwn(value, key))) {
    throw new Error(`${label} has missing or extra fields`);
  }
}

function jsonBytes(bytes, label) {
  if (!Buffer.isBuffer(bytes) || bytes.length === 0 || bytes.length > (2 << 20)) {
    throw new Error(`${label} bytes are missing or oversized`);
  }
  try {
    return JSON.parse(bytes.toString('utf8'));
  } catch {
    throw new Error(`${label} is invalid JSON`);
  }
}

function jcs(value) {
  if (Array.isArray(value)) return `[${value.map(jcs).join(',')}]`;
  if (value !== null && typeof value === 'object') {
    return `{${Object.keys(value).sort().map(key =>
      `${JSON.stringify(key)}:${jcs(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

function canonical(bytes, value, label) {
  if (bytes.some(byte => byte > 0x7f) || !bytes.equals(Buffer.from(jcs(value)))) {
    throw new Error(`${label} is not canonical ASCII JCS`);
  }
}

function decodedBytes(encoded, label) {
  if (typeof encoded !== 'string' || !/^[A-Za-z0-9_-]+$/.test(encoded)) {
    throw new Error(`${label} is not base64url`);
  }
  const bytes = Buffer.from(encoded, 'base64url');
  if (bytes.toString('base64url') !== encoded) {
    throw new Error(`${label} is not canonical base64url`);
  }
  return bytes;
}

function ref(value, label) {
  exactKeys(value, ['uri', 'sha256'], label);
  if (typeof value.uri !== 'string' || !value.uri || !SHA.test(value.sha256)) {
    throw new Error(`${label} is malformed`);
  }
  return { uri: value.uri, sha256: value.sha256 };
}

function publicRef(value, label) {
  const checked = ref(value, label);
  if (!/^qualification:\/\/stage2a\/[A-Za-z0-9][A-Za-z0-9_-]*\/[0-9a-f]{64}$/.test(checked.uri) ||
      !checked.uri.endsWith('/' + checked.sha256.slice(7))) {
    throw new Error(`${label} has an unreviewed public URI`);
  }
  return checked;
}

function instant(value, label) {
  if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z$/.test(value)) {
    throw new Error(`${label} is not a UTC instant`);
  }
  const time = Date.parse(value);
  if (!Number.isFinite(time)) throw new Error(`${label} is invalid`);
  return time;
}

function machinePayload(bytes, purpose, label) {
  const envelope = jsonBytes(bytes, label);
  canonical(bytes, envelope, label);
  exactKeys(envelope, ['schema_version', 'purpose', 'issuer_public_key_base64url',
    'payload_sha256', 'payload_base64url', 'signature_base64url'], label);
  if (envelope.schema_version !== 1 || envelope.purpose !== purpose ||
      !SHA.test(envelope.payload_sha256) ||
      decodedBytes(envelope.issuer_public_key_base64url, `${label} issuer`).length !== 32 ||
      decodedBytes(envelope.signature_base64url, `${label} signature`).length !== 64) {
    throw new Error(`${label} has wrong Machine envelope shape`);
  }
  const payloadBytes = decodedBytes(envelope.payload_base64url, `${label} payload`);
  if (digest(payloadBytes) !== envelope.payload_sha256) {
    throw new Error(`${label} payload digest differs`);
  }
  // Signature and issuer authority are checked by Machine's independent v3
  // verifier. Exact payload links are checked again here before publication.
  const payload = jsonBytes(payloadBytes, `${label} payload`);
  canonical(payloadBytes, payload, `${label} payload`);
  return payload;
}

function publicSoulsBuild(value, policy) {
  const keys = ['source_repository', 'source_commit', 'source_tree', 'builder_sha256',
    'toolchain_sha256', 'workflow', 'run_id', 'run_attempt', 'package_sha256', 'receipt'];
  exactKeys(value, keys, 'Souls build');
  const receipt = publicRef(value.receipt, 'Souls build receipt');
  const workflow = policy.souls_build_workflow;
  if (value.source_repository !== 'true-line/agent-bot-souls' ||
      !COMMIT.test(value.source_commit) || !COMMIT.test(value.source_tree) ||
      !SHA.test(value.builder_sha256) || !SHA.test(value.toolchain_sha256) ||
      !SHA.test(value.package_sha256) ||
      !/^\.github\/workflows\/[A-Za-z0-9._-]+\.ya?ml$/.test(value.workflow) ||
      !/^[1-9][0-9]{0,19}$/.test(value.run_id) ||
      !Number.isSafeInteger(value.run_attempt) || value.run_attempt < 1 ||
      workflow?.repository !== value.source_repository ||
      workflow.workflow_path !== value.workflow ||
      workflow.source_commit !== value.source_commit ||
      workflow.source_tree !== value.source_tree ||
      workflow.builder_sha256 !== value.builder_sha256 ||
      workflow.toolchain_sha256 !== value.toolchain_sha256 ||
      workflow.receipt_predicate_type !==
        'https://true-line.ai/soul-contracts/adapter/v2/souls-build' ||
      workflow.bundle_media_type !== 'application/vnd.dev.sigstore.bundle.v0.3+json' ||
      workflow.dsse_payload_type !== 'application/vnd.in-toto+json' ||
      workflow.oidc_issuer !== 'https://token.actions.githubusercontent.com' ||
      workflow.certificate_identity !==
        `https://github.com/true-line/agent-bot-souls/${value.workflow}@refs/heads/main` ||
      !SHA.test(workflow.trust_root_sha256)) {
    throw new Error('Souls build differs from owner-pinned workflow');
  }
  return {
    source_repository: value.source_repository,
    source_commit: value.source_commit,
    source_tree: value.source_tree,
    builder_sha256: value.builder_sha256,
    toolchain_sha256: value.toolchain_sha256,
    workflow: value.workflow,
    run_id: value.run_id,
    run_attempt: value.run_attempt,
    package_sha256: value.package_sha256,
    receipt,
  };
}

function reviewedAssets(indexBytes, verification) {
  const index = jsonBytes(indexBytes, 'reviewed v3 private asset index');
  if (!indexBytes.equals(Buffer.from(JSON.stringify(index, null, 2) + '\n'))) {
    throw new Error('reviewed v3 private asset index is not canonical');
  }
  validatePrivateAssetIndexV3(index, 'qualification://stage2a/');
  exactKeys(verification, ['schema_version', 'index_sha256', 'assets'],
    'v3 private asset verification');
  if (verification.schema_version !== 2 || verification.index_sha256 !== digest(indexBytes)) {
    throw new Error('v3 private asset verification differs from reviewed index');
  }
  exactKeys(verification.assets, Object.keys(V3_FILES), 'verified v3 asset roles');
  for (const role of Object.keys(V3_FILES)) {
    exactKeys(verification.assets[role], ['sha256', 'byte_length'], `${role} verification`);
    if (verification.assets[role].sha256 !== index.assets[role].sha256 ||
        verification.assets[role].byte_length !== index.assets[role].byte_length) {
      throw new Error(`${role} verification differs from reviewed v3 index`);
    }
  }
  return {
    matchBytes(role, bytes) {
      if (!Buffer.isBuffer(bytes) || digest(bytes) !== index.assets[role].sha256 ||
          bytes.length !== index.assets[role].byte_length) {
        throw new Error(`${role} bytes differ from reviewed v3 index`);
      }
    },
    matchRef(role, value) {
      if (value.uri !== index.assets[role].uri ||
          value.sha256 !== index.assets[role].sha256) {
        throw new Error(`${role} reference differs from reviewed v3 index`);
      }
    },
    digest(role) { return index.assets[role].sha256; },
  };
}

function boundRun(role, claim, files, verified, soulArtifact, expiry, now) {
  const label = `${role} report`;
  exactKeys(claim, ['report_schema_version', 'signature_purpose', 'payload',
    'signature', 'completed_run'], label);
  exactKeys(verified, ['profile', 'report_sha256', 'envelope_sha256',
    'execution_completion_sha256', 'completed_run_sha256'], `${role} verification`);
  if (claim.report_schema_version !== 3 || claim.signature_purpose !== PURPOSE ||
      verified.profile !== (role === 'protected' ? 'managed-protected' : 'managed-cooperative')) {
    throw new Error(`${label} has wrong v3 profile or purpose`);
  }
  const payload = publicRef(claim.payload, `${label} payload`);
  const signature = publicRef(claim.signature, `${label} signature`);
  const completedRun = publicRef(claim.completed_run, `${label} completed run`);
  if (new Set([payload.uri, signature.uri, completedRun.uri]).size !== 3 ||
      new Set([payload.sha256, signature.sha256, completedRun.sha256]).size !== 3) {
    throw new Error(`${label} public references must be distinct`);
  }
  const report = jsonBytes(files.report, `${label} payload`);
  const envelope = jsonBytes(files.envelope, `${label} envelope`);
  canonical(files.report, report, `${label} payload`);
  canonical(files.envelope, envelope, `${label} envelope`);
  if (payload.sha256 !== digest(files.report) || signature.sha256 !== digest(files.envelope) ||
      completedRun.sha256 !== digest(files.completedRun) ||
      payload.sha256 !== verified.report_sha256 ||
      signature.sha256 !== verified.envelope_sha256 ||
      completedRun.sha256 !== verified.completed_run_sha256 ||
      verified.execution_completion_sha256 !== digest(files.executionCompletion)) {
    throw new Error(`${label} exact bytes differ from Machine verification`);
  }
  exactKeys(envelope, ['schema_version', 'purpose', 'host_id', 'signer_id',
    'report_sha256', 'report_base64url', 'signature_base64url'], `${label} envelope`);
  if (envelope.schema_version !== 3 || envelope.purpose !== PURPOSE ||
      envelope.report_sha256 !== payload.sha256 ||
      decodedBytes(envelope.signature_base64url, `${label} host signature`).length !== 64 ||
      !decodedBytes(envelope.report_base64url, `${label} encoded report`).equals(files.report)) {
    throw new Error(`${label} host envelope does not name the report`);
  }
  if (report.schema_version !== 3 || report.profile_in_force !== verified.profile ||
      report.measurement_binding?.soul_artifact_sha256 !== soulArtifact ||
      report.run_evidence?.execution_completion?.sha256 !== verified.execution_completion_sha256) {
    throw new Error(`${label} measured run differs from qualification`);
  }
  const pre = machinePayload(files.executionCompletion,
    'agent-bot/stage2a-execution-completion/v1', `${label} execution completion`);
  const post = machinePayload(files.completedRun,
    'agent-bot/stage2a-report-commitment/v1', `${label} completed run`);
  exactKeys(pre, ['schema_version', 'kind', 'report_candidate_sha256',
    'reservation_sha256', 'finalization_sha256', 'challenge_sha256',
    'measurement_binding_sha256', 'completed_at'], `${label} execution payload`);
  exactKeys(post, ['schema_version', 'kind', 'execution_completion_sha256',
    'report_payload_sha256', 'report_envelope_sha256', 'committed_at'],
    `${label} commitment payload`);
  const reservation = ref(report.run_evidence?.reservation, `${label} reservation`);
  const finalization = ref(report.run_evidence?.finalization, `${label} finalization`);
  const execution = ref(report.run_evidence?.execution_completion, `${label} execution`);
  if (new Set([reservation.uri, finalization.uri, execution.uri]).size !== 3 ||
      new Set([reservation.sha256, finalization.sha256, execution.sha256]).size !== 3) {
    throw new Error(`${label} lifecycle references must be distinct`);
  }
  const candidate = structuredClone(report);
  delete candidate.run_evidence.execution_completion;
  if (pre.schema_version !== 1 || pre.kind !== 'stage2a-execution-completion' ||
      post.schema_version !== 1 || post.kind !== 'stage2a-report-commitment' ||
      pre.report_candidate_sha256 !== digest(Buffer.from(jcs(candidate))) ||
      pre.measurement_binding_sha256 !== digest(Buffer.from(jcs(report.measurement_binding))) ||
      pre.reservation_sha256 !== report.run_evidence?.reservation?.sha256 ||
      pre.finalization_sha256 !== report.run_evidence?.finalization?.sha256 ||
      pre.challenge_sha256 !== report.run_evidence?.challenge_sha256 ||
      post.execution_completion_sha256 !== verified.execution_completion_sha256 ||
      post.report_payload_sha256 !== payload.sha256 ||
      post.report_envelope_sha256 !== signature.sha256) {
    throw new Error(`${label} completion chain differs from signed report`);
  }
  const completedAt = instant(pre.completed_at, `${label} completed_at`);
  const recordedAt = instant(report.recorded_at, `${label} recorded_at`);
  const committedAt = instant(post.committed_at, `${label} committed_at`);
  if (completedAt > recordedAt || recordedAt > committedAt || committedAt >= expiry ||
      completedAt > now || recordedAt > now || committedAt > now) {
    throw new Error(`${label} completion chronology is invalid`);
  }
  return { report_schema_version: 3, signature_purpose: PURPOSE,
    payload, signature, completed_run: completedRun };
}

export function buildStatementV3({ qualificationBytes, policyEnvelopeBytes, runs,
  machineVerification, assetIndexBytes, privateAssetVerification,
  sourceCommit, expiresAt, now = new Date() }) {
  if (!COMMIT.test(sourceCommit) || !machineVerification ||
      machineVerification.schema_version !== 3 ||
      machineVerification.verification_contract !== 'agent-bot/machine-qualification-preflight/v3' ||
      !runs || !runs.protected || !runs.cooperative) {
    throw new Error('authenticated v3 Machine verification is required');
  }
  const qualification = jsonBytes(qualificationBytes, 'qualification');
  const envelope = jsonBytes(policyEnvelopeBytes, 'owner policy envelope');
  const assets = reviewedAssets(assetIndexBytes, privateAssetVerification);
  assets.matchBytes('qualification', qualificationBytes);
  assets.matchBytes('policy-envelope', policyEnvelopeBytes);
  const policyBytes = decodedBytes(envelope.policy_base64url, 'owner policy');
  const policy = jsonBytes(policyBytes, 'owner policy');
  if (machineVerification.qualification_sha256 !== digest(qualificationBytes) ||
      machineVerification.policy_envelope_sha256 !== digest(policyEnvelopeBytes) ||
      machineVerification.policy_sha256 !== digest(policyBytes) ||
      envelope.schema_version !== 3 || envelope.purpose !== 'agent-bot/qualification-policy/v3' ||
      envelope.policy_sha256 !== digest(policyBytes) ||
      policy.contract !== 'control/v3' || policy.kind !== 'qualification-trust-policy' ||
      policy.frozen_soul_release_provenance_enabled !== false ||
      policy.frozen_soul_build_workflow !== undefined ||
      policy.frozen_soul_mapping?.receipt_sha256 !== undefined ||
      qualification.schema_version !== 3 ||
      qualification.profile_in_force !== 'managed-protected' ||
      !BARE_SHA.test(qualification.soul_artifact_sha256) ||
      policy.frozen_soul_mapping?.artifact_sha256 !== qualification.soul_artifact_sha256 ||
      machineVerification.soul_artifact_sha256 !== qualification.soul_artifact_sha256) {
    throw new Error('verified v3 policy or qualification does not match exact bytes');
  }
  const release = policy.release_workflow;
  if (release?.repository !== 'true-line/soul-adapters' ||
      release.workflow_path !== WORKFLOW || release.source_commit !== sourceCommit ||
      release.oidc_issuer !== 'https://token.actions.githubusercontent.com' ||
      release.certificate_identity !==
        `https://github.com/true-line/soul-adapters/${WORKFLOW}@refs/heads/main`) {
    throw new Error('owner policy does not pin this release workflow');
  }
  const expiry = instant(expiresAt, 'Statement expiry');
  if (expiry <= now.getTime() ||
      expiresAt !== new Date(expiry).toISOString().replace('.000Z', 'Z')) {
    throw new Error('Statement expiry is not a future canonical UTC instant');
  }
  const protectedReport = boundRun('protected', qualification.conformance?.protected_report,
    runs.protected, machineVerification.protected, qualification.soul_artifact_sha256,
    expiry, now.getTime());
  const cooperativeReport = boundRun('cooperative', qualification.conformance?.cooperative_report,
    runs.cooperative, machineVerification.cooperative, qualification.soul_artifact_sha256,
    expiry, now.getTime());
  for (const field of ['payload', 'signature', 'completed_run']) {
    const left = protectedReport[field];
    const right = cooperativeReport[field];
    if (left.uri === right.uri || left.sha256 === right.sha256) {
      throw new Error(`protected and cooperative ${field} must be distinct`);
    }
  }
  const protectedRaw = jsonBytes(runs.protected.report, 'protected report');
  const cooperativeRaw = jsonBytes(runs.cooperative.report, 'cooperative report');
  for (const field of ['reservation', 'finalization']) {
    const left = ref(protectedRaw.run_evidence?.[field], `protected ${field}`);
    const right = ref(cooperativeRaw.run_evidence?.[field], `cooperative ${field}`);
    if (left.uri === right.uri || left.sha256 === right.sha256) {
      throw new Error(`protected and cooperative ${field} must be distinct`);
    }
  }
  if (protectedRaw.run_evidence?.challenge_sha256 ===
      cooperativeRaw.run_evidence?.challenge_sha256) {
    throw new Error('protected and cooperative challenges must be distinct');
  }
  const qualificationAt = instant(qualification.recorded_at, 'qualification recorded_at');
  if (qualificationAt < instant(protectedRaw.recorded_at, 'protected recorded_at') ||
      qualificationAt < instant(cooperativeRaw.recorded_at, 'cooperative recorded_at') ||
      qualificationAt >= expiry || qualificationAt > now.getTime()) {
    throw new Error('qualification chronology is invalid');
  }
  const soulsBuild = publicSoulsBuild(qualification.souls_build, policy);
  assets.matchRef('souls-build-bundle', soulsBuild.receipt);
  if (assets.digest('souls-package') !== soulsBuild.package_sha256 ||
      assets.digest('frozen-soul') !== `sha256:${qualification.soul_artifact_sha256}`) {
    throw new Error('Souls package or frozen artifact differs from reviewed v3 index');
  }
  for (const [role, run, claim] of [
    ['protected', runs.protected, protectedReport],
    ['cooperative', runs.cooperative, cooperativeReport],
  ]) {
    assets.matchBytes(`${role}-report`, run.report);
    assets.matchBytes(`${role}-envelope`, run.envelope);
    assets.matchBytes(`${role}-execution`, run.executionCompletion);
    assets.matchBytes(`${role}-commitment`, run.completedRun);
    assets.matchRef(`${role}-report`, claim.payload);
    assets.matchRef(`${role}-envelope`, claim.signature);
    assets.matchRef(`${role}-commitment`, claim.completed_run);
  }
  if (machineVerification.souls_build_bundle_sha256 !== soulsBuild.receipt.sha256 ||
      machineVerification.souls_package_sha256 !== soulsBuild.package_sha256 ||
      machineVerification.souls_source_tree !== soulsBuild.source_tree) {
    throw new Error('Souls build differs from authenticated Machine verification');
  }
  return {
    _type: 'https://in-toto.io/Statement/v1',
    subject: [{ name: 'qualification.json', digest: { sha256: digest(qualificationBytes).slice(7) } }],
    predicateType: PREDICATE,
    predicate: {
      host_reports: { protected_report: protectedReport, cooperative_report: cooperativeReport },
      souls_build: soulsBuild,
      soul_artifact_sha256: qualification.soul_artifact_sha256,
      expires_at: expiresAt,
      release: { source_repository: 'true-line/soul-adapters', source_commit: sourceCommit,
        workflow: WORKFLOW },
    },
  };
}
