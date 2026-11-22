import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { buildPrivateAssetIndex, privateObjectKeyForAsset, verifyPrivateAssets } from './verify-private-assets.mjs';

const files = {
  qualification: 'qualification.json',
  'policy-envelope': 'policy-envelope.json',
  'protected-envelope': 'protected-envelope.json',
  'cooperative-envelope': 'cooperative-envelope.json',
  'souls-package': 'souls-package.tgz',
  'souls-build-bundle': 'souls-build-receipt.bundle.json',
};
const prefix = 'qualification://stage2a/';

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'private-assets-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const privateDir = path.join(dir, 'private');
  fs.mkdirSync(privateDir);
  const assets = {};
  for (const [role, file] of Object.entries(files)) {
    const bytes = Buffer.from(`exact-${role}\n`);
    fs.writeFileSync(path.join(privateDir, file), bytes);
    const assetSHA = 'sha256:' + createHash('sha256').update(bytes).digest('hex');
    assets[role] = {
      uri: prefix + role + '/' + assetSHA.slice(7),
      sha256: assetSHA,
      byte_length: bytes.length,
    };
  }
  const index = { schema_version: 1, assets };
  const indexPath = path.join(dir, 'private-assets.json');
  const write = () => fs.writeFileSync(indexPath, JSON.stringify(index, null, 2) + '\n');
  write();
  return { privateDir, index, indexPath, write };
}

test('all six exact private bytes match the reviewed index', t => {
  const f = fixture(t);
  const result = verifyPrivateAssets(f.indexPath, f.privateDir, prefix);
  assert.deepEqual(Object.keys(result.assets), Object.keys(files));
  assert.equal(result.assets.qualification.sha256, f.index.assets.qualification.sha256);
  assert.equal(result.index_sha256,
    'sha256:' + createHash('sha256').update(fs.readFileSync(f.indexPath)).digest('hex'));
  for (const role of Object.keys(files)) {
    assert.equal(privateObjectKeyForAsset(f.index, role, prefix),
      `v1/${role}/${f.index.assets[role].sha256.slice(7)}`);
  }
  assert.throws(() => privateObjectKeyForAsset(f.index, 'unexpected', prefix), /unknown private asset role/);
});

test('generator emits canonical six-role index from exact local file bytes', t => {
  const f = fixture(t);
  const generated = buildPrivateAssetIndex(f.privateDir, prefix);
  assert.deepEqual(generated, f.index);
  const cli = execFileSync(process.execPath,
    [fileURLToPath(new URL('./build-private-assets-index.mjs', import.meta.url)), f.privateDir, prefix]);
  assert.deepEqual(cli, Buffer.from(JSON.stringify(f.index, null, 2) + '\n'));
  fs.writeFileSync(f.indexPath, cli);
  const verified = verifyPrivateAssets(f.indexPath, f.privateDir, prefix);
  assert.equal(verified.index_sha256,
    'sha256:' + createHash('sha256').update(cli).digest('hex'));
  assert.deepEqual(verified.assets,
    Object.fromEntries(Object.entries(f.index.assets).map(([role, value]) =>
      [role, { sha256: value.sha256, byte_length: value.byte_length }])));
});

test('generator refuses missing, empty, nonregular, and symlinked private files', t => {
  const f = fixture(t);
  const target = path.join(f.privateDir, files.qualification);
  fs.unlinkSync(target);
  assert.throws(() => buildPrivateAssetIndex(f.privateDir, prefix), /qualification:/);
  fs.writeFileSync(target, '');
  assert.throws(() => buildPrivateAssetIndex(f.privateDir, prefix), /empty/);
  fs.unlinkSync(target);
  fs.mkdirSync(target);
  assert.throws(() => buildPrivateAssetIndex(f.privateDir, prefix), /regular file/);
  fs.rmdirSync(target);
  fs.symlinkSync(path.join(f.privateDir, files['policy-envelope']), target);
  assert.throws(() => buildPrivateAssetIndex(f.privateDir, prefix), /qualification:/);
  const linkedDir = path.join(path.dirname(f.privateDir), 'linked-private');
  fs.symlinkSync(f.privateDir, linkedDir);
  assert.throws(() => buildPrivateAssetIndex(linkedDir, prefix), /directory must be real/);
});

test('altered bytes are rejected even when length is unchanged', t => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.privateDir, files.qualification),
    Buffer.from('other-qualification\n'));
  assert.throws(() => verifyPrivateAssets(f.indexPath, f.privateDir, prefix),
    /qualification: (SHA-256|missing regular file or byte length) mismatch/);
});

test('missing and extra roles, duplicate URIs, and unapproved URI are rejected', t => {
  const f = fixture(t);
  delete f.index.assets['souls-build-bundle'];
  f.write();
  assert.throws(() => verifyPrivateAssets(f.indexPath, f.privateDir, prefix), /roles/);
  f.index.assets['souls-build-bundle'] = { ...f.index.assets.qualification };
  f.write();
  assert.throws(() => verifyPrivateAssets(f.indexPath, f.privateDir, prefix), /duplicated/);
  f.index.assets['souls-build-bundle'].uri = 'r2://private-bucket/receipt';
  f.write();
  assert.throws(() => verifyPrivateAssets(f.indexPath, f.privateDir, prefix), /outside approved prefix/);
  f.index.assets['souls-build-bundle'].uri = prefix + 'souls-build-bundle/' +
    f.index.assets['souls-build-bundle'].sha256.slice(7);
  f.index.assets.unexpected = { ...f.index.assets.qualification };
  f.write();
  assert.throws(() => verifyPrivateAssets(f.indexPath, f.privateDir, prefix), /roles/);
});

test('wrong size and symlinked private file are rejected', t => {
  const f = fixture(t);
  f.index.assets.qualification.byte_length += 1;
  f.write();
  assert.throws(() => verifyPrivateAssets(f.indexPath, f.privateDir, prefix), /byte length mismatch/);
  f.index.assets.qualification.byte_length -= 1;
  f.write();
  const file = path.join(f.privateDir, files.qualification);
  fs.renameSync(file, file + '.original');
  fs.symlinkSync(file + '.original', file);
  assert.throws(() => verifyPrivateAssets(f.indexPath, f.privateDir, prefix));
});

test('missing approved prefix refuses verification', t => {
  const f = fixture(t);
  assert.throws(() => verifyPrivateAssets(f.indexPath, f.privateDir, ''),
    /approved public logical asset URI prefix/);
});

test('public URI must use exact role and content digest without a private locator', t => {
  const f = fixture(t);
  const original = f.index.assets['souls-build-bundle'].uri;
  for (const uri of [
    prefix + 'souls-package/' + f.index.assets['souls-build-bundle'].sha256.slice(7),
    prefix + 'souls-build-bundle/' + '0'.repeat(64),
    original + '?token=PRIVATE_SENTINEL',
    original + '/..',
  ]) {
    f.index.assets['souls-build-bundle'].uri = uri;
    f.write();
    assert.throws(() => verifyPrivateAssets(f.indexPath, f.privateDir, prefix),
      /URI must contain only the public role and matching digest|outside approved prefix/);
  }
});

test('duplicate JSON keys and alternate encodings refuse review', t => {
  const f = fixture(t);
  const raw = fs.readFileSync(f.indexPath, 'utf8');
  fs.writeFileSync(f.indexPath, raw.replace('  "assets": {',
    '  "schema_version": 1,\n  "assets": {'));
  assert.throws(() => verifyPrivateAssets(f.indexPath, f.privateDir, prefix),
    /canonical two-space JSON/);
  fs.writeFileSync(f.indexPath, JSON.stringify(f.index));
  assert.throws(() => verifyPrivateAssets(f.indexPath, f.privateDir, prefix),
    /canonical two-space JSON/);
});
