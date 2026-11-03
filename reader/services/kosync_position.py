"""Translate Reader-Reeden positions to/from KOReader CRe XPointers."""
import re

from ..models import Chapter
from ..utils import read_book_text

TXT_DOCFRAGMENT = '/body/DocFragment[1]'
P_INDEX = re.compile(r'/p\[(\d+)\]')
DOCFRAGMENT_INDEX = re.compile(r'/DocFragment\[(\d+)\]')
TEXT_OFFSET = re.compile(r'text\(\)(?:\[\d+\])?\.(\d+)')
ELEMENT_INDEX = re.compile(r'/([A-Za-z][A-Za-z0-9_-]*)\[(\d+)\]/text\((?:\[\d+\])?\)')


def _last_p_index(progress):
    matches = P_INDEX.findall(progress or '')
    return int(matches[-1]) if matches else None


def _epub_section(data, section_index):
    try:
        return data['sections'][section_index]
    except (KeyError, IndexError, TypeError):
        return None


def _epub_block_index(section, tag, tag_index):
    """Map a tag-specific XPointer index back to the section's block index."""
    tags = section.get('blockTags') or []
    if not tags:
        return tag_index - 1 if tag == 'p' else None
    count = 0
    for index, value in enumerate(tags):
        if (value or 'p') == tag:
            count += 1
            if count == tag_index:
                return index
    return None


def _chapter_raw_offset(text, words):
    """Text-only character offset in a chapter (newlines ignored)."""
    remaining = max(0, int(words))
    for index, char in enumerate(text):
        if char == '\n':
            continue
        if remaining <= 0:
            return index
        remaining -= 1
    return len(text)


def _txt_paragraphs(data):
    """Non-empty lines with their absolute character offsets."""
    paragraphs = []
    position = 0
    for line in data.splitlines(keepends=True):
        text = line.rstrip('\r\n')
        if text.strip():
            paragraphs.append((position, text))
        position += len(line)
    if not paragraphs:
        paragraphs.append((0, data))
    return paragraphs


def _percentage(book, chapter, words):
    from .progress import calculate_read_progress
    return calculate_read_progress(book, chapter, words) / 10000.0


def txt_to_koreader_progress(book, chapter, words):
    data = read_book_text(book)
    chapter_text = data[chapter.start:chapter.end]
    raw = chapter.start + _chapter_raw_offset(chapter_text, words)
    paragraphs = _txt_paragraphs(data)
    index = 0
    for i, (start, _text) in enumerate(paragraphs):
        if start <= raw:
            index = i
        else:
            break
    start, text = paragraphs[index]
    offset = max(0, min(raw, start + len(text)) - start)
    return {
        'progress': '%s/body/p[%d]/text().%d' % (TXT_DOCFRAGMENT, index + 1, offset),
        'percentage': _percentage(book, chapter, words),
    }


def koreader_progress_to_txt(book, progress, percentage=None):
    data = read_book_text(book)
    chapters = list(Chapter.objects.filter(book_id=book.id).order_by('index'))
    if not chapters:
        raise ValueError('book has no chapters')
    exact = True
    raw = None
    p_match = _last_p_index(progress)
    offset_match = TEXT_OFFSET.search(progress or '')
    if p_match is not None and offset_match:
        paragraphs = _txt_paragraphs(data)
        index = p_match - 1
        if 0 <= index < len(paragraphs):
            start, text = paragraphs[index]
            offset = max(0, int(offset_match.group(1)))
            raw = start + min(offset, len(text))
    if raw is None:
        exact = False
        try:
            fraction = min(1.0, max(0.0, float(percentage)))
        except (TypeError, ValueError):
            fraction = 0.0
        raw = int(len(data) * fraction)
    raw = max(0, min(raw, len(data)))
    chapter = next((ch for ch in chapters if raw < ch.end), chapters[-1])
    raw = max(chapter.start, min(raw, chapter.end))
    text = data[chapter.start:raw].replace('\n', '')
    return chapter, len(text), exact


def epub_to_koreader_progress(book, chapter, words, location=None):
    from .epub import export_position, read_cache

    position = export_position(book, chapter, words, location)
    section_index = position['sectionIndex']
    paragraph = position['paragraphIndex']
    element = position['elementIndex']
    section = _epub_section(read_cache(book), section_index) or {}
    tags = section.get('blockTags') or []
    tag = (tags[paragraph] if paragraph < len(tags) else '') or 'p'
    if tags:
        tag_index = 1 + sum(1 for value in tags[:paragraph] if (value or 'p') == tag)
    else:
        tag_index = paragraph + 1
    return {
        'progress': '/body/DocFragment[%d]/body/%s[%d]/text().%d' % (
            section_index + 1, tag, tag_index, element),
        'percentage': _percentage(book, chapter, words),
        'position': position,
    }


def koreader_progress_to_epub(book, progress, percentage=None):
    from .epub import import_position, read_cache

    section_match = DOCFRAGMENT_INDEX.search(progress or '')
    offset_match = TEXT_OFFSET.search(progress or '')
    element_match = ELEMENT_INDEX.search(progress or '')
    record = {}
    if section_match and offset_match:
        section_index = max(0, int(section_match.group(1)) - 1)
        paragraph_index = None
        if element_match:
            tag, tag_index = element_match.group(1), int(element_match.group(2))
            section = _epub_section(read_cache(book), section_index)
            if section is not None:
                paragraph_index = _epub_block_index(section, tag, tag_index)
        if paragraph_index is None:
            p_index = _last_p_index(progress)
            if p_index is not None:
                paragraph_index = max(0, p_index - 1)
        if paragraph_index is not None:
            record = {
                'sectionIndex': section_index,
                'paragraphIndex': paragraph_index,
                'elementIndex': max(0, int(offset_match.group(1))),
            }
    try:
        fraction = min(1.0, max(0.0, float(percentage)))
    except (TypeError, ValueError):
        fraction = 0.0
    record['readProgress'] = int(fraction * 10000)
    chapter, words, exact = import_position(book, record)
    return chapter, words, exact, record
