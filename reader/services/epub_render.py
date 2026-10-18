"""Untrusted EPUB markup/resources live in a scriptless, CSP-restricted iframe.

Only manifest resources are addressable. CSS URLs (including nested functions and
@imports) are rewritten to permission-checked local endpoints. No network fetches.
The old plain-text cache remains the canonical progress/search index.
"""
import functools
import os
import re
import zipfile
from urllib.parse import quote
from xml.etree import ElementTree as ET

import html5lib
import tinycss2
from django.urls import reverse

from .epub import EpubError, MAX_MEMBER_SIZE, _member_path, parse_epub, read_cache

HTML_TAGS = set('html head body title p div span a h1 h2 h3 h4 h5 h6 br hr img picture '
                'figure figcaption table caption colgroup col thead tbody tfoot tr td th '
                'ul ol li dl dt dd blockquote pre code em strong b i u s small sub sup '
                'section article aside header footer nav main abbr cite q ruby rt rp wbr '
                'link style meta'.split())
SVG_TAGS = set('svg g path rect circle ellipse line polyline polygon text tspan defs '
               'linearGradient radialGradient stop clipPath mask pattern image use symbol title desc'.split())
ATTRIBUTES = set('id class title lang dir role aria-label aria-hidden epub:type type '
                 'href src alt width height colspan rowspan scope headers span start reversed '
                 'style rel media name content viewBox preserveAspectRatio xmlns x y x1 y1 x2 y2 '
                 'd points cx cy r rx ry fill stroke stroke-width opacity transform offset '
                 'stop-color stop-opacity clip-path mask patternUnits gradientUnits gradientTransform '
                 'font-size font-family text-anchor'.split())
DROP_CONTENT = {'script', 'iframe', 'object', 'embed', 'form', 'input', 'button', 'textarea',
                'select', 'audio', 'video', 'source', 'base', 'foreignObject', 'animate', 'set'}
RESOURCE_TYPES = {'text/css', 'application/xhtml+xml', 'text/html', 'image/jpeg', 'image/png',
                  'image/gif', 'image/webp', 'image/avif', 'image/svg+xml', 'image/bmp',
                  'font/ttf', 'font/otf', 'font/woff', 'font/woff2', 'application/font-sfnt',
                  'application/vnd.ms-opentype', 'application/font-woff'}


@functools.lru_cache(maxsize=2)
def _metadata(path, modified):
    data = parse_epub(path)
    data.pop('text')
    return data


def render_metadata(book):
    """Upgrade metadata in memory only, never reindex old chapters/bookmarks."""
    data = read_cache(book)
    if 'resources' in data:
        return data
    rich = _metadata(book.abs_path(), os.stat(book.abs_path()).st_mtime_ns)
    sections = [{**new, **old} for old, new in zip(data['sections'], rich['sections'])]
    return {**data, **rich, 'text': data['text'], 'textHash': data['textHash'],
            'sections': sections, 'chapters': data['chapters']}


def resource_url(book, path):
    return reverse('reader:epub_resource', args=[book.id]) + '?path=' + quote(path, safe='')


def safe_url(book, base, href, navigation=False):
    try:
        path, fragment = _member_path(base, href)
    except (ValueError, TypeError):
        return ''
    resources = render_metadata(book)['resources']
    if path not in resources:
        return ''
    media = resources[path]
    if navigation and media not in ('application/xhtml+xml', 'text/html'):
        return ''
    if not navigation and media not in RESOURCE_TYPES:
        return ''
    return resource_url(book, path) + ('#' + quote(fragment, safe='') if fragment else '')


def _css_tokens(book, base, tokens):
    """Return serialized tokens, rejecting malformed/active/external values."""
    result = []
    for token in tokens:
        if token.type in ('error', 'bad-url', 'bad-string'):
            return None
        if token.type == 'url':
            url = '#' + quote(token.value[1:], safe='') if token.value.startswith('#') else safe_url(book, base, token.value)
            if not url:
                return None
            result.append('url("' + url + '")')
        elif token.type == 'function':
            if token.lower_name in ('expression', 'image-set', '-webkit-image-set'):
                return None
            if token.lower_name == 'url':
                args = [v for v in token.arguments if v.type not in ('whitespace', 'comment')]
                if len(args) != 1 or args[0].type not in ('string', 'url'):
                    return None
                url = '#' + quote(args[0].value[1:], safe='') if args[0].value.startswith('#') else safe_url(book, base, args[0].value)
                if not url:
                    return None
                result.append('url("' + url + '")')
            else:
                value = _css_tokens(book, base, token.arguments)
                if value is None:
                    return None
                result.append(token.name + '(' + value + ')')
        elif hasattr(token, 'content'):
            value = _css_tokens(book, base, token.content)
            if value is None:
                return None
            opening, closing = {'[] block': ('[', ']'), '() block': ('(', ')'), '{} block': ('{', '}')}[token.type]
            result.append(opening + value + closing)
        else:
            result.append(token.serialize())
    return ''.join(result)


def clean_declarations(book, base, text):
    declarations = []
    for item in tinycss2.parse_declaration_list(text, skip_comments=True, skip_whitespace=True):
        if item.type != 'declaration' or item.lower_name in ('behavior', '-moz-binding'):
            continue
        value = _css_tokens(book, base, item.value)
        if value is not None:
            declarations.append(item.name + ':' + value + (' !important' if item.important else '') + ';')
    return ''.join(declarations).replace('<', r'\3c ')


def clean_css(book, base, text):
    output = []
    for rule in tinycss2.parse_stylesheet(text, skip_comments=True, skip_whitespace=True):
        if rule.type == 'qualified-rule':
            selector = _css_tokens(book, base, rule.prelude)
            if selector is not None:
                output.append(selector + '{' + clean_declarations(book, base, rule.content) + '}')
        elif rule.type == 'at-rule':
            name = rule.lower_at_keyword
            if name == 'import':
                tokens = [t for t in rule.prelude if t.type not in ('whitespace', 'comment')]
                if tokens:
                    if tokens[0].type == 'string':
                        url = safe_url(book, base, tokens[0].value)
                        value = 'url("' + url + '")' if url else None
                    else:
                        value = _css_tokens(book, base, tokens[:1])
                    media = _css_tokens(book, base, tokens[1:])
                    if value and media is not None:
                        output.append('@import ' + value + ' ' + media + ';')
            elif name in ('media', 'supports', 'layer') and rule.content is not None:
                prelude = _css_tokens(book, base, rule.prelude)
                if prelude is not None:
                    output.append('@' + name + ' ' + prelude + '{' + clean_css(book, base, tinycss2.serialize(rule.content)) + '}')
            elif name in ('font-face', 'page') and rule.content is not None:
                output.append('@' + name + ' ' + tinycss2.serialize(rule.prelude) + '{' + clean_declarations(book, base, rule.content) + '}')
    return '\n'.join(output).replace('<', r'\3c ')


def read_resource(book, path):
    metadata = render_metadata(book)
    if path not in metadata['resources']:
        raise EpubError('书内资源不存在')
    with zipfile.ZipFile(book.abs_path()) as archive:
        info = archive.getinfo(path)
        if info.file_size > MAX_MEMBER_SIZE or info.flag_bits & 1:
            raise EpubError('书内资源过大或已加密')
        return archive.read(info), metadata['resources'][path]


def _clean_tree(book, base, node):
    for child in list(node):
        if not isinstance(child.tag, str):
            node.remove(child)
            continue
        tag = child.tag.rsplit('}', 1)[-1]
        if tag in DROP_CONTENT or tag not in HTML_TAGS | SVG_TAGS:
            # Keep ordinary unsupported elements' text, but drop executable subtrees.
            if tag not in DROP_CONTENT:
                child.tag = 'span'
            else:
                tail = child.tail or ''
                index = list(node).index(child)
                if index:
                    node[index - 1].tail = (node[index - 1].tail or '') + tail
                else:
                    node.text = (node.text or '') + tail
                node.remove(child)
                continue
        _clean_tree(book, base, child)
    tag = node.tag.rsplit('}', 1)[-1]
    attributes = {}
    for key, value in node.attrib.items():
        name = key.rsplit('}', 1)[-1]
        if key.startswith('{http://www.idpf.org/2007/ops}'):
            name = 'epub:type'
        if name not in ATTRIBUTES or name.lower().startswith('on'):
            continue
        if name == 'style':
            value = clean_declarations(book, base, value)
        elif name in ('src', 'href'):
            value = safe_url(book, base, value, navigation=(tag == 'a'))
        elif name in ('fill', 'stroke', 'clip-path', 'mask') and ('url' in value.lower() or '\\' in value):
            value = _css_tokens(book, base, tinycss2.parse_component_value_list(value)) or ''
        if value:
            attributes[name] = value
    node.attrib.clear()
    node.attrib.update(attributes)
    if tag == 'meta' and node.get('name', '').lower() != 'viewport':
        node.attrib.clear()
    if tag == 'link' and node.get('rel', '').lower() != 'stylesheet':
        node.attrib.clear()
    if tag == 'style':
        node.text = clean_css(book, base, node.text or '')


def render_document(book, path):
    raw, media = read_resource(book, path)
    if media not in ('application/xhtml+xml', 'text/html', 'image/svg+xml'):
        raise EpubError('不是书籍正文')
    if media == 'image/svg+xml':
        # Parse as XML so SVG namespaces/case survive. External entities forbidden.
        from defusedxml import ElementTree
        root = ElementTree.fromstring(raw)
        if root.tag.rsplit('}', 1)[-1] != 'svg':
            raise EpubError('SVG 资源结构无效')
        _clean_tree(book, path, root)
        return ET.tostring(root, encoding='unicode')
    encoding = re.search(rb'<\?xml[^>]*encoding=[\'"]([^\'"]+)', raw[:200])
    decoded = raw.decode(encoding.group(1).decode('ascii') if encoding else 'utf-8-sig')
    doc = html5lib.parse(decoded, namespaceHTMLElements=False)
    _clean_tree(book, path, doc)
    head = doc.find('head')
    if head is not None:
        meta = ET.Element('meta', {'charset': 'utf-8'})
        head.insert(0, meta)
    metadata = render_metadata(book)
    doc.set('lang', doc.get('lang') or metadata.get('language') or 'en')
    return '<!doctype html>' + html5lib.serialize(doc, omit_optional_tags=False, quote_attr_values='always')


def chapter_location(book, chapter, words):
    from .epub import raw_offset
    data = read_cache(book)
    raw = chapter.start + raw_offset(data['text'][chapter.start:chapter.end], words)
    saved_section = data['chapters'][chapter.index].get('section')
    if saved_section is not None and chapter.start == chapter.end:
        return saved_section, raw
    return next((s['index'] for s in data['sections'] if s['start'] <= raw < s['end']),
                data['sections'][-1]['index'] if raw else 0), raw
