import { cp } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, '..');

const src = resolve(root, 'node_modules/iconify-icon/dist/iconify-icon.mjs');
const dest = resolve(root, 'reader/static/iconify-icon.mjs');

await cp(src, dest);
console.log('copied iconify-icon.mjs -> reader/static/');
