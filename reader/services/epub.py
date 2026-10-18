"""EPUB → stable plain text. Never extract ZIP members or fetch book resources.

Spine indexes include empty covers. Display chapters may split at TOC anchors,
but cloud positions always use the original spine and text-block indexes.
"""
import bisect
import hashlib
import json
import os
import posixpath
import re
import stat
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from defusedxml import ElementTree

from ..utils import resolve_book_path, get_element_index

MAX_FILE_SIZE = 100 * 1024 * 1024
MAX_EXPANDED_SIZE = 200 * 1024 * 1024
MAX_MEMBER_SIZE = 16 * 1024 * 1024
MAX_ENTRIES = 5000
MAX_TEXT_SIZE = 20 * 1024 * 1024
OPF = '{http://www.idpf.org/2007/opf}'
DC = '{http://purl.org/dc/elements/1.1/}'


class EpubError(ValueError):
    pass


def text_units(text):
    """Browser string.length (UTF-16 units), not Python code points."""
    return len(text.encode('utf-16-le')) // 2


def cloud_offset(text, units):
    """Reeden schema v1 convention, shared with TXT: non-ASCII counts as 2."""
    consumed = 0
    for index, char in enumerate(text):
        width = 2 if ord(char) > 127 else 1
        if consumed + width > units:
            return index
        consumed += width
    return len(text)


def _member_path(base, href):
    parsed = urlsplit(href)
    path = unquote(parsed.path)
    if parsed.scheme or parsed.netloc or path.startswith('/') or '\\' in path or '\x00' in path:
        raise EpubError('EPUB 包含非法资源路径')
    result = posixpath.normpath(posixpath.join(posixpath.dirname(base), path)) if path else base
    if result == '..' or result.startswith('../'):
        raise EpubError('EPUB 资源路径超出书籍范围')
    return result, unquote(parsed.fragment)


class _TextBlocks(HTMLParser):
    BLOCKS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'dt', 'dd',
              'blockquote', 'pre', 'section', 'article', 'header', 'footer', 'tr', 'figure', 'figcaption'}
    SKIP = {'head', 'script', 'style', 'svg', 'math', 'iframe', 'object', 'audio', 'video', 'noscript'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.anchors = {}
        self.parts = []
        self.skipped = []
        self.heading = ''
        self.heading_parts = None
        self.pending_anchors = []

    def flush(self):
        # Only HTML collapsible whitespace; full-width spaces and NBSP are book
        # content, and removing them would change cloud element positions.
        text = re.sub(r'[ \t\r\n\f]+', ' ', ''.join(self.parts)).strip(' \t\r\n\f')
        if text:
            for anchor in self.pending_anchors:
                self.anchors[anchor] = len(self.blocks)
            self.blocks.append(text)
        self.parts = []
        self.pending_anchors = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.skipped:
            if tag in self.SKIP:
                self.skipped.append(tag)
            return
        if tag in self.SKIP:
            self.skipped.append(tag)
            return
        if tag in self.BLOCKS or tag == 'br':
            self.flush()
        anchor = attrs.get('id') or attrs.get('name')
        if anchor:
            self.anchors[anchor] = len(self.blocks)
            self.pending_anchors.append(anchor)
        if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            self.heading_parts = []
        if tag in {'td', 'th'}:
            self.parts.append(' ')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.skipped:
            if tag == self.skipped[-1]:
                self.skipped.pop()
            return
        if tag in self.BLOCKS:
            self.flush()
        if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'} and self.heading_parts is not None:
            if not self.heading:
                self.heading = ' '.join(''.join(self.heading_parts).split())
            self.heading_parts = None

    def handle_data(self, data):
        if not self.skipped:
            self.parts.append(data)
            if self.heading_parts is not None:
                self.heading_parts.append(data)


def parse_epub(path):
    """Read a bounded EPUB 2/3 package into a deterministic, versioned cache."""
    if os.path.getsize(path) > MAX_FILE_SIZE:
        raise EpubError('EPUB 文件超过 100MB 限制')
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_EXPANDED_SIZE:
                raise EpubError('EPUB 解压体积或文件数量超过限制')
            names = set()
            for info in infos:
                name = info.filename
                _member_path('', name)
                if '..' in name.split('/'):
                    raise EpubError('EPUB 包含非法资源路径')
                if name in names or info.flag_bits & 1 or stat.S_ISLNK(info.external_attr >> 16):
                    raise EpubError('EPUB 包含重复、加密或链接文件')
                if info.file_size > MAX_MEMBER_SIZE:
                    raise EpubError('EPUB 单个资源超过 16MB 限制')
                if info.file_size > 1024 * 1024 and info.file_size > max(1, info.compress_size) * 1000:
                    raise EpubError('EPUB 资源压缩比异常')
                names.add(name)

            def read(name):
                data = archive.read(name)
                if len(data) > MAX_MEMBER_SIZE:
                    raise EpubError('EPUB 资源过大')
                return data

            def xml(name):
                return ElementTree.fromstring(read(name), forbid_entities=True, forbid_external=True)

            def html(name):
                raw = read(name)
                encoding = re.search(rb'<\?xml[^>]*encoding=[\'"]([^\'"]+)', raw[:200])
                text = raw.decode(encoding.group(1).decode('ascii') if encoding else 'utf-8-sig')
                # HTMLParser doesn't resolve DTDs or fetch resources; entity declarations
                # are rejected rather than silently changing the extracted text.
                if '<!ENTITY' in text.upper():
                    raise EpubError('EPUB 不允许自定义 XML 实体')
                parser = _TextBlocks()
                parser.feed(text)
                parser.flush()
                return parser

            if read('mimetype').strip() != b'application/epub+zip':
                raise EpubError('不是有效的 EPUB 文件')
            container = xml('META-INF/container.xml')
            package = next(e.get('full-path') for e in container.iter() if e.tag.endswith('}rootfile'))
            package, _ = _member_path('', package)
            root = xml(package)
            metadata = root.find(OPF + 'metadata')
            if metadata is None:
                raise EpubError('EPUB 缺少元数据')
            fixed = any(meta.get('property') == 'rendition:layout' and (meta.text or '').strip() == 'pre-paginated'
                        or meta.get('name') == 'fixed-layout' and meta.get('content') == 'true'
                        for meta in metadata.findall(OPF + 'meta'))
            if 'META-INF/encryption.xml' in names:
                encryption = xml('META-INF/encryption.xml')
                font_algorithms = {'http://www.idpf.org/2008/embedding', 'http://ns.adobe.com/pdf/enc#RC'}
                if any(e.get('Algorithm') not in font_algorithms for e in encryption.iter()
                       if e.tag.endswith('}EncryptionMethod')):
                    raise EpubError('暂不支持 DRM 加密 EPUB')
            manifest = {e.get('id'): e for e in root.findall(OPF + 'manifest/' + OPF + 'item')}
            spine = root.find(OPF + 'spine')
            if spine is None:
                raise EpubError('EPUB 缺少阅读顺序')

            # EPUB 3 nav or EPUB 2 NCX. Ignore remote links, never fetch them.
            toc = []
            nav = next((e for e in manifest.values() if 'nav' in e.get('properties', '').split()), None)
            if nav is not None:
                nav_path, _ = _member_path(package, nav.get('href', ''))
                nav_root = xml(nav_path)
                nav_element = next((e for e in nav_root.iter() if e.tag.rsplit('}', 1)[-1] == 'nav'
                                    and 'toc' in e.get('{http://www.idpf.org/2007/ops}type', '').split()), None)
                if nav_element is not None:
                    for e in nav_element.iter():
                        if e.tag.rsplit('}', 1)[-1] == 'a' and e.get('href'):
                            toc.append((nav_path, e.get('href'), ' '.join(''.join(e.itertext()).split())))
            if not toc and spine.get('toc') in manifest:
                ncx_path, _ = _member_path(package, manifest[spine.get('toc')].get('href', ''))
                for point in xml(ncx_path).iter():
                    if point.tag.rsplit('}', 1)[-1] == 'navPoint':
                        label = next((' '.join(''.join(e.itertext()).split()) for e in point
                                      if e.tag.rsplit('}', 1)[-1] == 'navLabel'), '')
                        href = next((e.get('src') for e in point if e.tag.rsplit('}', 1)[-1] == 'content'), '')
                        toc.append((ncx_path, href, label))
            toc_by_path = {}
            for base, href, label in toc:
                if urlsplit(href).scheme or urlsplit(href).netloc:
                    continue
                target, fragment = _member_path(base, href)
                toc_by_path.setdefault(target, []).append((fragment, label))

            sections, chapters, chunks = [], [], []
            offset = 0
            for section_index, ref in enumerate(spine.findall(OPF + 'itemref')):
                item = manifest.get(ref.get('idref'))
                if item is None or item.get('media-type') not in ('application/xhtml+xml', 'text/html'):
                    raise EpubError('EPUB 阅读顺序引用了不支持的正文资源')
                source, _ = _member_path(package, item.get('href', ''))
                parsed = html(source)
                starts = []
                for block in parsed.blocks:
                    starts.append(offset)
                    chunks.append(block + '\n')
                    offset += len(block) + 1
                if offset > MAX_TEXT_SIZE:
                    raise EpubError('EPUB 正文超过大小限制')
                section_start = starts[0] if starts else offset
                sections.append({'index': section_index, 'href': source, 'start': section_start,
                                 'end': offset, 'paragraphs': starts,
                                 'fixed': (fixed or 'rendition:layout-pre-paginated' in ref.get('properties', '').split())
                                          and 'rendition:layout-reflowable' not in ref.get('properties', '').split()})
                if not starts:
                    if sections[-1]['fixed']:
                        chapters.append({'title': next((label for _, label in toc_by_path.get(source, [])),
                                                       '第 %d 页' % (section_index + 1)),
                                         'start': offset, 'end': offset, 'section': section_index})
                    continue  # Keep its original spine slot but don't show an empty chapter.
                cuts = {}
                for fragment, label in toc_by_path.get(source, []):
                    block_index = parsed.anchors.get(fragment) if fragment else 0
                    if block_index is not None and block_index < len(starts):
                        cuts.setdefault(starts[block_index], label)
                cuts.setdefault(section_start, parsed.heading or '第 %d 节' % (section_index + 1))
                points = sorted(cuts)
                for index, start in enumerate(points):
                    chapters.append({'title': cuts[start][:256], 'start': start,
                                     'end': points[index + 1] if index + 1 < len(points) else offset,
                                     'section': section_index})
            text = ''.join(chunks)
            if not text.strip() and not any(s['fixed'] for s in sections):
                raise EpubError('EPUB 没有可阅读的文字正文')
            return {'version': 1, 'title': metadata.findtext(DC + 'title', '').strip(),
                    'author': metadata.findtext(DC + 'creator', '').strip(), 'text': text,
                    'textHash': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                    'sections': sections, 'chapters': chapters,
                    'fixed': fixed, 'language': metadata.findtext(DC + 'language', '').strip(),
                    'direction': spine.get('page-progression-direction', 'ltr'),
                    'resources': { _member_path(package, item.get('href', ''))[0]: item.get('media-type', '')
                                   for item in manifest.values()
                                   if not urlsplit(item.get('href', '')).scheme and not urlsplit(item.get('href', '')).netloc },
                    'toc': [{'href': path, 'fragment': fragment, 'title': label}
                            for path, entries in toc_by_path.items() for fragment, label in entries]}
    except EpubError:
        raise
    except Exception as exc:
        raise EpubError('EPUB 文件损坏或结构不受支持') from exc


def write_cache(path, data):
    """Atomic sidecar publication; no partially written reader content."""
    fd, temporary = tempfile.mkstemp(prefix='.epub-', dir=Path(path).parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def read_cache(book, _cache={}):
    path = resolve_book_path(book.content_path)
    key = (path, os.stat(path).st_mtime_ns)
    data = _cache.get(key)
    if data is None:
        with open(path, encoding='utf-8') as stream:
            data = json.load(stream)
        _cache.clear()
        _cache[key] = data
    return data


def raw_offset(text, words):
    """Chapter browser offset (excluding newlines) → normalized text offset."""
    remaining = max(0, int(words))
    for index, char in enumerate(text):
        if char == '\n':
            continue
        width = 2 if ord(char) > 0xffff else 1
        if remaining < width:
            return index
        remaining -= width
    return len(text)


def export_position(book, chapter, words, location=None):
    data = read_cache(book)
    raw = chapter.start + raw_offset(data['text'][chapter.start:chapter.end], words)
    populated = [s for s in data['sections'] if s['paragraphs']]
    section = next((s for s in populated if raw < s['end']), populated[-1] if populated else data['sections'][0])
    if location is not None:
        section = data['sections'][location['section']]
    paragraph, element = 0, 0
    if section['paragraphs']:
        paragraph = max(0, bisect.bisect_right(section['paragraphs'], raw) - 1)
        start = section['paragraphs'][paragraph]
        end = data['text'].find('\n', start)
        element = get_element_index(data['text'][start:min(raw, end)])
    position = {'sectionIndex': section['index'], 'paragraphIndex': paragraph, 'elementIndex': element}
    return {**position,
            # Reeden's EPUB samples use sectionIndex / section count, even for
            # nonzero paragraph/element positions. Keep that cloud convention.
            'readProgress': int(section['index'] / len(data['sections']) * 10000),
            'readerEpub': {'version': data['version'], 'textHash': data['textHash'],
                           'chapterIndex': chapter.index, 'words': max(0, int(words)),
                           'position': position, 'location': location or {}}}


def import_position(book, record):
    """Return (chapter, browser offset, exact). Invalid indexes use percentage only."""
    from ..models import Chapter
    data = read_cache(book)
    chapters = list(Chapter.objects.filter(book_id=book.id).order_by('index'))
    exact = True
    try:
        own = record.get('readerEpub')
        if (isinstance(own, dict) and own.get('textHash') == data['textHash']
                and own.get('version') == data['version']
                and own.get('position') == {k: record.get(k) for k in ('sectionIndex', 'paragraphIndex', 'elementIndex')}):
            ch_index, words = own['chapterIndex'], own['words']
            if type(ch_index) is int and type(words) is int and 0 <= ch_index < len(chapters):
                ch = chapters[ch_index]
                if 0 <= words <= text_units(data['text'][ch.start:ch.end].replace('\n', '')):
                    return ch, words, True
        indexes = [record[k] for k in ('sectionIndex', 'paragraphIndex', 'elementIndex')]
        if any(type(v) is not int or v < 0 for v in indexes):
            raise ValueError
        section_index, paragraph_index, element_index = indexes
        section = data['sections'][section_index]
        if not section['paragraphs'] and paragraph_index == element_index == 0:
            chapter_index = next((i for i, ch in enumerate(data['chapters']) if ch.get('section') == section_index), 0)
            return chapters[chapter_index], 0, True
        start = section['paragraphs'][paragraph_index]
        end = data['text'].find('\n', start)
        line = data['text'][start:end]
        if element_index > get_element_index(line):
            raise ValueError
        raw = start + cloud_offset(line, element_index)
    except (KeyError, IndexError, TypeError, ValueError):
        exact = False
        try:
            percent = min(10000, max(0, int(record.get('readProgress', 0))))
        except (TypeError, ValueError, OverflowError):
            percent = 0
        fractional_section = percent / 10000 * len(data['sections'])
        section_index = min(int(fractional_section), len(data['sections']) - 1)
        section = data['sections'][section_index]
        raw = section['start'] + int((section['end'] - section['start']) * (fractional_section - section_index))
    chapter = next((ch for ch in chapters if raw < ch.end), chapters[-1])
    raw = max(chapter.start, min(raw, chapter.end))
    words = text_units(data['text'][chapter.start:raw].replace('\n', ''))
    return chapter, words, exact


def calculate_progress(book, chapter, words):
    data = read_cache(book)
    raw = chapter.start + raw_offset(data['text'][chapter.start:chapter.end], words)
    sections = [s for s in data['sections'] if s['paragraphs']]
    if not sections:
        section_index = data['chapters'][chapter.index].get('section', 0)
        return int(section_index / len(data['sections']) * 10000)
    section = next((s for s in sections if raw < s['end']), sections[-1])
    fraction = min(1, max(0, (raw - section['start']) / max(1, section['end'] - section['start'])))
    if raw >= len(data['text']):
        return 10000
    return int((section['index'] + fraction) / len(data['sections']) * 10000)


def characters_at_progress(book, progress):
    """Statistics must count text, not treat unequal EPUB sections as equal size."""
    data = read_cache(book)
    fraction = max(0, min(10000, progress)) / 10000 * len(data['sections'])
    index = min(int(fraction), len(data['sections']) - 1)
    section = data['sections'][index]
    return section['start'] + int((section['end'] - section['start']) * (fraction - index))
