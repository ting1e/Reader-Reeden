import { readFile, writeFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = resolve(__dirname, '..');

const ICONS_JSON = resolve(root, 'node_modules/@iconify-json/bi/icons.json');
const OUT = resolve(root, 'reader/static/icons-bundle.js');

const SCAN_FILES = [
  'reader/context_processors.py',
  'reader/static/reader.js',
  'reader/static/book_sort.js',
  'reader/templates/base.html',
  'reader/templates/_header.html',
  'reader/templates/_search.html',
  'reader/templates/_bookmark_list.html',
  'reader/templates/_chapter_list.html',
  'reader/templates/_chapter_view.html',
  'reader/templates/book_admin.html',
  'reader/templates/book_list_admin.html',
  'reader/templates/book_list_detail.html',
  'reader/templates/book_view.html',
  'reader/templates/bookmark_admin.html',
  'reader/templates/bookshelf.html',
  'reader/templates/bookshelf_remote.html',
  'reader/templates/font_admin.html',
  'reader/templates/index.html',
  'reader/templates/reading_stats.html',
  'reader/templates/reading_stats_admin.html',
  'reader/templates/setup_admin.html',
  'reader/templates/upload_file.html',
  'reader/templates/user_settings.html',
];

const NAME_RE = /bi:[a-z0-9-]+/g;

const found = new Set();
for (const rel of SCAN_FILES) {
  let txt;
  try {
    txt = await readFile(resolve(root, rel), 'utf8');
  } catch {
    continue;
  }
  for (const m of txt.matchAll(NAME_RE)) found.add(m[0]);
}

const names = [...found].sort();
if (names.length === 0) {
  console.error('no bi: icons found');
  process.exit(1);
}

const collection = JSON.parse(await readFile(ICONS_JSON, 'utf8'));
const icons = {};
let missing = [];
for (const full of names) {
  const name = full.slice(3);
  const data = collection.icons[name];
  if (!data) {
    missing.push(full);
    continue;
  }
  icons[name] = data;
}

if (missing.length) {
  console.error('missing icons:', missing.join(', '));
  process.exit(1);
}

const bundle = {
  prefix: 'bi',
  icons,
  aliases: Object.fromEntries(
    Object.entries(collection.aliases || {}).filter(([k]) => names.includes('bi:' + k))
  ),
  width: collection.width,
  height: collection.height,
};

const js = `import { addCollection } from './iconify-icon.min.js';
addCollection(${JSON.stringify(bundle)});
`;

await writeFile(OUT, js, 'utf8');
console.log(`bundled ${names.length} bi icons -> reader/static/icons-bundle.js`);
