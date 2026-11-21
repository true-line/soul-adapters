import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { verifyPrivateAssets } from './verify-private-assets.mjs';

const files = {
  qualification: 'qualification.json',
  'policy-envelope': 'policy-envelope.json',
  'protected-envelope': 'protected-envelope.json',
  'cooperative-envelope': 'cooperative-envelope.json',
  'souls-package': 'souls-package.tgz',
  'souls-build-bundle': 'souls-build-receipt.bundle.json',
};
const prefix = 's3://approved-stage2a/immutable/';

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'private-assets-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const privateDir = path.join(dir, 'private');
  fs.mkdirSync(privateDir);
  const assets = {};
  for (const [role, file] of Object.entries(files)) {
    const bytes = Buffer.from(`exact-${role}\n`);
    fs.writeFileSync(path.join(privateDir, file), bytes);
    assets[role] = {
      uri: prefix + file,
      sha256: 'sha256:' + createHash('sha256').update(bytes).digest('hex'),
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
  f.index.assets['souls-build-bundle'].uri = 's3://other/receipt';
  f.write();
  assert.throws(() => verifyPrivateAssets(f.indexPath, f.privateDir, prefix), /outside approved prefix/);
  f.index.assets['souls-build-bundle'].uri = prefix + 'receipt';
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
    /approved private asset URI prefix/);
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
