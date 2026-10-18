from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin

from ..models import Book, Chapter, UserBookRecord, UserBookMark
from ..utils import can_access_book, get_or_create_user_setting, get_local_fonts
from ..services.epub import EpubError, read_cache
from ..services.epub_render import (
    RESOURCE_TYPES, chapter_location, clean_css, read_resource, render_document,
    render_metadata, resource_url,
)


def accessible_book(request, pk):
    book = get_object_or_404(Book, id=pk, format='epub')
    if not can_access_book(book, request.user):
        raise Http404
    return book


@xframe_options_sameorigin
def epub_resource(request, pk):
    book = accessible_book(request, pk)
    path = request.GET.get('path', '')
    try:
        raw, media = read_resource(book, path)
        if media not in RESOURCE_TYPES:
            raise Http404
        if media in ('application/xhtml+xml', 'text/html', 'image/svg+xml'):
            content = render_document(book, path)
            media = 'image/svg+xml' if media == 'image/svg+xml' else 'text/html'
        elif media == 'text/css':
            import tinycss2
            rules, encoding = tinycss2.parse_stylesheet_bytes(raw)
            content = clean_css(book, path, tinycss2.serialize(rules))
        else:
            content = raw
    except (EpubError, KeyError, ValueError, OSError):
        raise Http404
    response = HttpResponse(content, content_type=media)
    response['Content-Security-Policy'] = (
        "default-src 'none'; script-src 'none'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self'; font-src 'self'; connect-src 'none'; frame-src 'none'; "
        "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'; "
        "sandbox allow-same-origin"
    )
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response


def reader_context(request, book, chapter, words, explicit=False):
    metadata = render_metadata(book)
    data = read_cache(book)
    section_index, raw = chapter_location(book, chapter, words)
    record = None
    marks = []
    if request.user.is_authenticated:
        record = UserBookRecord.objects.filter(user_id=request.user.id, book_id=book.id).first()
        marks = list(UserBookMark.objects.filter(user_id=request.user.id, book_id=book.id)
                     .order_by('-add_time').values('chapter_id', 'words_read', 'chapter_title', 'content', 'epub_location'))
    location = record.epub_location if record and not explicit else {}
    if isinstance(location, dict) and type(location.get('section')) is int and 0 <= location['section'] < len(metadata['sections']):
        section_index = location['section']
    try:
        requested_section = int(request.GET.get('section', '-1'))
        if explicit and 0 <= requested_section < len(metadata['sections']):
            section_index = requested_section
    except ValueError:
        pass
    chapters = list(Chapter.objects.filter(book_id=book.id).order_by('index'))
    sections = []
    for section in metadata['sections']:
        starts = section['paragraphs']
        paragraphs = [{'start': start, 'text': data['text'][start:data['text'].find('\n', start)]}
                      for start in starts]
        sections.append({'index': section['index'], 'href': section['href'],
                         'url': resource_url(book, section['href']), 'fixed': section.get('fixed', metadata.get('fixed', False)),
                         'start': section['start'], 'end': section['end'], 'paragraphs': paragraphs})
    setting = get_or_create_user_setting(request.user) if request.user.is_authenticated else None
    config = {
        'bookId': book.id, 'title': book.name, 'section': section_index, 'raw': raw,
        'location': location, 'sections': sections, 'toc': metadata.get('toc', []),
        'chapters': [{'id': ch.id, 'title': ch.title, 'start': ch.start, 'end': ch.end,
                      'section': data['chapters'][ch.index].get('section')} for ch in chapters],
        'bookmarks': marks, 'language': metadata.get('language', ''),
        'direction': metadata.get('direction', 'ltr'),
        'saveUrl': reverse('reader:book_view'), 'bookmarkUrl': reverse('reader:bookmark_save'),
        'searchUrl': reverse('reader:book_view'),
        'resourceUrl': reverse('reader:epub_resource', args=[book.id]),
        'authenticated': request.user.is_authenticated, 'wordCount': book.word_count,
        'updateSettingsUrl': reverse('reader:user_settings_update'),
        'fonts': [{'name': font['name'], 'url': reverse('reader:font_file', args=[font['file_name']]),
                   'format': font['format']}
                  for font in (get_local_fonts(request.user.id) if request.user.is_authenticated else [])],
        'settings': {'fontSize': setting.font_size if setting else 18,
                     'lineHeight': max(1.2, setting.line_height) if setting else 1.6,
                     'mode': setting.read_mode if setting else 'page',
                     'background': setting.read_bg if setting else '#fff',
                     'color': setting.font_color if setting else '',
                     'fontFamily': setting.font_family if setting else '',
                     'fontWeight': setting.font_weight if setting else '',
                     'letterSpacing': setting.letter_spacing if setting else 0,
                     'pageWidth': setting.page_width if setting else 0,
                     'pageHeight': setting.page_height if setting else 0},
    }
    return {'epub_config': config, 'book': book}
