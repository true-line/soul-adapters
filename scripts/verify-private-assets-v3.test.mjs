import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { buildPrivateAssetIndexV3, validatePrivateAssetIndexV3,
  verifyPrivateAssetsV3, V3_FILES } from './verify-private-assets-v3.mjs';

const prefix = 'qualification://stage2a/';

function staged(run) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'adapter-v3-assets-'));
  const dir = path.join(root, 'private');
  fs.mkdirSync(dir, { mode: 0o700 });
  for (const [role, filename] of Object.entries(V3_FILES)) {
    fs.writeFileSync(path.join(dir, filename), Buffer.from(`synthetic ${role}\n`), { mode: 0o600 });
  }
  try { return run(root, dir); } finally { fs.rmSync(root, { recursive: true, force: true }); }
}

test('v3 index keeps both completed runs and frozen Soul bytes distinct', () => staged((root, dir) => {
  const index = buildPrivateAssetIndexV3(dir, prefix);
  assert.equal(index.schema_version, 2);
  assert.equal(Object.keys(index.assets).length, 13);
  assert.notEqual(index.assets['frozen-soul'].sha256, index.assets['souls-package'].sha256);
  assert.notEqual(index.assets['protected-commitment'].sha256,
    index.assets['cooperative-commitment'].sha256);
  const indexPath = path.join(root, 'private-assets-v3.json');
  fs.writeFileSync(indexPath, JSON.stringify(index, null, 2) + '\n');
  const verified = verifyPrivateAssetsV3(indexPath, dir, prefix);
  assert.equal(verified.schema_version, 2);
  assert.equal(verified.assets['frozen-soul'].sha256, index.assets['frozen-soul'].sha256);
}));

test('v3 index refuses missing, symlinked, or changed private bytes', () => staged((root, dir) => {
  const index = buildPrivateAssetIndexV3(dir, prefix);
  const indexPath = path.join(root, 'private-assets-v3.json');
  fs.writeFileSync(indexPath, JSON.stringify(index, null, 2) + '\n');
  const file = path.join(dir, V3_FILES['protected-commitment']);
  fs.writeFileSync(file, Buffer.from('synthetic protected-execution\n'));
  assert.throws(() => verifyPrivateAssetsV3(indexPath, dir, prefix));
  fs.rmSync(file);
  assert.throws(() => verifyPrivateAssetsV3(indexPath, dir, prefix));
  fs.symlinkSync(path.join(dir, V3_FILES['cooperative-commitment']), file);
  assert.throws(() => verifyPrivateAssetsV3(indexPath, dir, prefix));
  assert.throws(() => buildPrivateAssetIndexV3(dir, prefix));
}));

test('v3 index refuses missing roles, private URIs, and alternate JSON', () => staged((root, dir) => {
  const index = buildPrivateAssetIndexV3(dir, prefix);
  const missing = structuredClone(index);
  delete missing.assets['frozen-soul'];
  assert.throws(() => validatePrivateAssetIndexV3(missing, prefix));
  const privateURI = structuredClone(index);
  privateURI.assets['protected-report'].uri = 's3://private-bucket/report';
  assert.throws(() => validatePrivateAssetIndexV3(privateURI, prefix));
  assert.throws(() => validatePrivateAssetIndexV3(index, 's3://private-bucket/'));
  const indexPath = path.join(root, 'private-assets-v3.json');
  fs.writeFileSync(indexPath, JSON.stringify(index));
  assert.throws(() => verifyPrivateAssetsV3(indexPath, dir, prefix));
  fs.writeFileSync(indexPath, JSON.stringify(index, null, 2) + '\n ');
  assert.throws(() => verifyPrivateAssetsV3(indexPath, dir, prefix));
}));
