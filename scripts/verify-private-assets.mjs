import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const files = Object.freeze({
  qualification: 'qualification.json',
  'policy-envelope': 'policy-envelope.json',
  'protected-envelope': 'protected-envelope.json',
  'cooperative-envelope': 'cooperative-envelope.json',
  'souls-package': 'souls-package.tgz',
  'souls-build-bundle': 'souls-build-receipt.bundle.json',
});

function requireKeys(value, keys, label) {
  const actual = value !== null && typeof value === 'object' && !Array.isArray(value)
    ? Object.keys(value).sort() : [];
  const expected = [...keys].sort();
  if (value === null || typeof value !== 'object' || Array.isArray(value) ||
      actual.length !== expected.length ||
      actual.some((key, index) => key !== expected[index])) {
    throw new Error(`${label} must have exactly: ${keys.join(', ')}`);
  }
}

export function validatePrivateAssetIndex(index, approvedPrefix) {
  requireKeys(index, ['schema_version', 'assets'], 'private asset index');
  if (index.schema_version !== 1) {
    throw new Error('unsupported private asset index version');
  }
  if (approvedPrefix !== 'qualification://stage2a/') {
    throw new Error('approved public logical asset URI prefix is required');
  }
  requireKeys(index.assets, Object.keys(files), 'private asset roles');
  const seenUris = new Set();
  for (const [role, asset] of Object.entries(index.assets)) {
    requireKeys(asset, ['uri', 'sha256', 'byte_length'], role);
    if (typeof asset.uri !== 'string' ||
        !asset.uri.startsWith(approvedPrefix) ||
        asset.uri.length <= approvedPrefix.length ||
        /[\s?#]/.test(asset.uri) ||
        seenUris.has(asset.uri)) {
      throw new Error(`${role}: URI is missing, duplicated, or outside approved prefix`);
    }
    seenUris.add(asset.uri);
    if (typeof asset.sha256 !== 'string' ||
        !/^sha256:[0-9a-f]{64}$/.test(asset.sha256)) {
      throw new Error(`${role}: invalid SHA-256 digest`);
    }
    if (asset.uri !== `${approvedPrefix}${role}/${asset.sha256.slice(7)}`) {
      throw new Error(`${role}: URI must contain only the public role and matching digest`);
    }
    if (!Number.isSafeInteger(asset.byte_length) || asset.byte_length < 1) {
      throw new Error(`${role}: invalid byte length`);
    }
  }
  return index;
}

// Candidate private R2 key mapping. The bucket and credentials stay in the
// release environment; only the role and content digest appear in public refs.
export function privateObjectKeyForAsset(index, role, approvedPrefix) {
  validatePrivateAssetIndex(index, approvedPrefix);
  if (!Object.hasOwn(files, role)) {
    throw new Error('unknown private asset role');
  }
  return `v1/${role}/${index.assets[role].sha256.slice(7)}`;
}

function inspectRegularFile(file) {
  const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    const before = fs.fstatSync(fd, { bigint: true });
    if (!before.isFile() || before.size < 1n ||
        before.size > BigInt(Number.MAX_SAFE_INTEGER)) {
      throw new Error('missing, empty, or oversized regular file');
    }
    const hash = createHash('sha256');
    const block = Buffer.allocUnsafe(1024 * 1024);
    let length = 0;
    for (;;) {
      const count = fs.readSync(fd, block, 0, block.length, null);
      if (count === 0) break;
      hash.update(block.subarray(0, count));
      length += count;
    }
    const after = fs.fstatSync(fd, { bigint: true });
    const currentPath = fs.lstatSync(file, { bigint: true });
    if (BigInt(length) !== before.size || !currentPath.isFile() ||
        before.dev !== after.dev || before.ino !== after.ino ||
        before.size !== after.size ||
        before.mtimeNs !== after.mtimeNs ||
        before.ctimeNs !== after.ctimeNs ||
        before.dev !== currentPath.dev || before.ino !== currentPath.ino) {
      throw new Error('private asset changed while being checked');
    }
    return { sha256: `sha256:${hash.digest('hex')}`, byte_length: length };
  } finally {
    fs.closeSync(fd);
  }
}

export function buildPrivateAssetIndex(privateDir, approvedPrefix) {
  if (!fs.lstatSync(privateDir).isDirectory()) {
    throw new Error('private asset directory must be real');
  }
  if (approvedPrefix !== 'qualification://stage2a/') {
    throw new Error('approved public logical asset URI prefix is required');
  }
  const assets = {};
  for (const [role, file] of Object.entries(files)) {
    let inspected;
    try {
      inspected = inspectRegularFile(path.join(privateDir, file));
    } catch (error) {
      throw new Error(`${role}: ${error.message}`);
    }
    assets[role] = {
      uri: `${approvedPrefix}${role}/${inspected.sha256.slice(7)}`,
      sha256: inspected.sha256,
      byte_length: inspected.byte_length,
    };
  }
  return validatePrivateAssetIndex({ schema_version: 1, assets }, approvedPrefix);
}

export function verifyPrivateAssets(indexPath, privateDir, approvedPrefix) {
  const indexStat = fs.lstatSync(indexPath);
  const dirStat = fs.lstatSync(privateDir);
  if (!indexStat.isFile() || !dirStat.isDirectory()) {
    throw new Error('index must be a regular file and private directory must be real');
  }
  const rawIndex = fs.readFileSync(indexPath);
  const parsedIndex = JSON.parse(rawIndex.toString('utf8'));
  if (!rawIndex.equals(Buffer.from(JSON.stringify(parsedIndex, null, 2) + '\n'))) {
    throw new Error('private asset index must use canonical two-space JSON');
  }
  const index = validatePrivateAssetIndex(
    parsedIndex, approvedPrefix);
  const verified = {};
  for (const [role, file] of Object.entries(files)) {
    const asset = index.assets[role];
    let inspected;
    try {
      inspected = inspectRegularFile(path.join(privateDir, file));
    } catch (error) {
      throw new Error(`${role}: ${error.message}`);
    }
    if (inspected.byte_length !== asset.byte_length) {
      throw new Error(`${role}: byte length mismatch`);
    }
    if (inspected.sha256 !== asset.sha256) {
      throw new Error(`${role}: SHA-256 mismatch`);
    }
    verified[role] = inspected;
  }
  return {
    schema_version: 1,
    index_sha256: 'sha256:' + createHash('sha256').update(rawIndex).digest('hex'),
    assets: verified,
  };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  if (process.argv.length !== 5) {
    throw new Error('usage: verify-private-assets.mjs INDEX PRIVATE_DIR APPROVED_URI_PREFIX');
  }
  process.stdout.write(JSON.stringify(verifyPrivateAssets(
    process.argv[2], process.argv[3], process.argv[4])) + '\n');
}
