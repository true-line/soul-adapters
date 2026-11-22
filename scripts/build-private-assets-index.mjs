#!/usr/bin/env node
import { pathToFileURL } from 'node:url';
import { buildPrivateAssetIndex } from './verify-private-assets.mjs';

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  if (process.argv.length !== 4) {
    throw new Error('usage: build-private-assets-index.mjs PRIVATE_DIR APPROVED_URI_PREFIX');
  }
  const index = buildPrivateAssetIndex(process.argv[2], process.argv[3]);
  process.stdout.write(JSON.stringify(index, null, 2) + '\n');
}
