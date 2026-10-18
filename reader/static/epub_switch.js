/* Presentation changes are navigation, not reading-progress updates. */
(function () {
  'use strict';
  const config = JSON.parse(document.getElementById('epub-switch-config').textContent);
  const select = document.getElementById('epub-display');
  const storageKey = 'epub-display-' + config.bookId;
  try { localStorage.setItem(storageKey, config.display); } catch (_) {}
  if (config.display === 'plain') {
    window.epubPresentation = {
      capture: function () {
        // The first displayed page may start before the exact restored passage.
        // Until navigation, carry the original offset through a round trip.
        const words = epub_progress_readonly ? initial_last_words : currentWordsRead();
        return { chapter: chapter_id, words: words, section: null };
      },
      suspend: function () { epub_progress_readonly = true; }
    };
  }
  select.addEventListener('change', function () {
    if (select.value === config.display) return;
    const adapter = window.epubPresentation;
    if (!adapter) { select.value = config.display; return; }
    const point = adapter.capture();
    adapter.suspend();
    const url = new URL(config.url, location.origin);
    url.searchParams.set('book_id', config.bookId);
    url.searchParams.set('display', select.value);
    url.searchParams.set('chapter_id', point.chapter);
    url.searchParams.set('offset', Math.max(0, point.words));
    if (Number.isInteger(point.section)) url.searchParams.set('section', point.section);
    try { localStorage.setItem(storageKey, select.value); } catch (_) {}
    location.assign(url);
  });
})();
