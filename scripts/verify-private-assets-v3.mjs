import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { inspectRegularFile, requireKeys } from './verify-private-assets.mjs';

// Draft v3 role list. It keeps the frozen .soul transport separate from the
// Souls runtime package and names both sides of each Machine completion chain.
export const V3_FILES = Object.freeze({
  qualification: 'qualification.json',
  'policy-envelope': 'policy-envelope.json',
  'protected-report': 'protected-report.json',
  'protected-envelope': 'protected-envelope.json',
  'protected-execution': 'protected-execution.json',
  'protected-commitment': 'protected-commitment.json',
  'cooperative-report': 'cooperative-report.json',
  'cooperative-envelope': 'cooperative-envelope.json',
  'cooperative-execution': 'cooperative-execution.json',
  'cooperative-commitment': 'cooperative-commitment.json',
  'souls-package': 'souls-package.tgz',
  'souls-build-bundle': 'souls-build-receipt.bundle.json',
  'frozen-soul': 'frozen-soul.soul',
});

const SHA = /^sha256:[0-9a-f]{64}$/;
const PREFIX = 'qualification://stage2a/';

export function validatePrivateAssetIndexV3(index, approvedPrefix) {
  requireKeys(index, ['schema_version', 'assets'], 'v3 private asset index');
  if (index.schema_version !== 2 || approvedPrefix !== PREFIX) {
    throw new Error('v3 private asset index version or prefix is not approved');
  }
  requireKeys(index.assets, Object.keys(V3_FILES), 'v3 private asset roles');
  const seen = new Set();
  for (const [role, asset] of Object.entries(index.assets)) {
    requireKeys(asset, ['uri', 'sha256', 'byte_length'], `${role} asset`);
    if (!SHA.test(asset.sha256) ||
        asset.uri !== `${PREFIX}${role}/${asset.sha256.slice(7)}` ||
        seen.has(asset.uri) ||
        !Number.isSafeInteger(asset.byte_length) || asset.byte_length < 1) {
      throw new Error(`${role} asset reference is malformed`);
    }
    seen.add(asset.uri);
  }
  return index;
}

export function buildPrivateAssetIndexV3(privateDir, approvedPrefix) {
  if (!fs.lstatSync(privateDir).isDirectory()) {
    throw new Error('v3 private asset directory must be real');
  }
  if (approvedPrefix !== PREFIX) {
    throw new Error('approved v3 logical URI prefix is required');
  }
  const assets = {};
  for (const [role, filename] of Object.entries(V3_FILES)) {
    let inspected;
    try {
      inspected = inspectRegularFile(path.join(privateDir, filename));
    } catch (error) {
      throw new Error(`${role}: ${error.message}`);
    }
    assets[role] = {
      uri: `${PREFIX}${role}/${inspected.sha256.slice(7)}`,
      sha256: inspected.sha256,
      byte_length: inspected.byte_length,
    };
  }
  return validatePrivateAssetIndexV3({ schema_version: 2, assets }, approvedPrefix);
}

export function verifyPrivateAssetsV3(indexPath, privateDir, approvedPrefix) {
  if (!fs.lstatSync(indexPath).isFile() || !fs.lstatSync(privateDir).isDirectory()) {
    throw new Error('v3 index must be a regular file and private directory must be real');
  }
  const raw = fs.readFileSync(indexPath);
  const parsed = JSON.parse(raw.toString('utf8'));
  if (!raw.equals(Buffer.from(JSON.stringify(parsed, null, 2) + '\n'))) {
    throw new Error('v3 index is not canonical two-space JSON');
  }
  const index = validatePrivateAssetIndexV3(parsed, approvedPrefix);
  const verified = {};
  for (const [role, filename] of Object.entries(V3_FILES)) {
    let inspected;
    try {
      inspected = inspectRegularFile(path.join(privateDir, filename));
    } catch (error) {
      throw new Error(`${role}: ${error.message}`);
    }
    if (inspected.sha256 !== index.assets[role].sha256 ||
        inspected.byte_length !== index.assets[role].byte_length) {
      throw new Error(`${role} exact bytes differ from reviewed v3 index`);
    }
    verified[role] = inspected;
  }
  return { schema_version: 2,
    index_sha256: 'sha256:' + createHash('sha256').update(raw).digest('hex'),
    assets: verified };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const [action, first, second, prefix] = process.argv.slice(2);
  if (action === 'build' && first && second === PREFIX && !prefix) {
    process.stdout.write(JSON.stringify(buildPrivateAssetIndexV3(first, second), null, 2) + '\n');
  } else if (action === 'verify' && first && second && prefix) {
    process.stdout.write(JSON.stringify(verifyPrivateAssetsV3(first, second, prefix)) + '\n');
  } else {
    throw new Error('usage: verify-private-assets-v3.mjs build PRIVATE_DIR PREFIX | verify INDEX PRIVATE_DIR PREFIX');
  }
}
