import { cp } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, '..');

const src = resolve(root, 'node_modules/iconify-icon/dist/iconify-icon.min.js');
const dest = resolve(root, 'reader/static/iconify-icon.min.js');

await cp(src, dest);
console.log('copied iconify-icon.min.js -> reader/static/');
