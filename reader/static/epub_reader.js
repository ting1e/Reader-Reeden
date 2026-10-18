/* Scriptless book iframe; only this trusted parent controls navigation/progress. */
(function () {
  'use strict';
  const config = JSON.parse(document.getElementById('epub-config').textContent);
  const aliases = { 'epub-title': 'read-chapter-text', 'epub-progress': 'read-progress-text' };
  const $ = id => document.getElementById(aliases[id] || id);
  const frame = $('epub-frame'), stage = $('epub-stage'), canvas = $('epub-canvas');
  const searchModal = document.querySelector('.myModal'), searchResults = document.querySelector('.search-res'), note = $('epub-note');
  const controls = { 'epub-prev': '.prev-page', 'epub-next': '.next-page',
    'epub-prev-section': '.prev-chapter', 'epub-next-section': '.next-chapter',
    'epub-toc': '.chapter_list_btn', 'epub-bookmarks': '.bookmark-btn' };
  for (const [id, selector] of Object.entries(controls)) document.querySelector(selector).id = id;
  const csrf = document.querySelector('[name=csrfmiddlewaretoken]').value;
  const key = 'epub-settings-' + config.bookId;
  let prefs = {};
  try { prefs = JSON.parse(localStorage.getItem(key) || '{}'); } catch (_) {}
  if (!config.authenticated) {
    try {
      const guest = JSON.parse(localStorage.getItem('reader_setting') || '{}');
      const map = { font_size: 'fontSize', line_height: 'lineHeight', read_mode: 'mode', read_bg: 'background',
        font_color: 'color', font_family: 'fontFamily', font_weight: 'fontWeight', letter_spacing: 'letterSpacing',
        page_width: 'pageWidth', page_height: 'pageHeight' };
      for (const [from, to] of Object.entries(map)) if (guest[from] !== undefined) config.settings[to] = guest[from];
    } catch (_) {}
  }
  $('epub-mode').value = config.settings.mode;
  $('epub-style').value = prefs.style || 'original';
  $('epub-font').value = config.settings.fontSize;
  let section = config.section, doc = null, page = 0, pageCount = 1, loading = false;
  let dirty = false, saveTimer, resizeTimer, nodes = [], currentRaw = config.raw, generation = 0;
  let pending = { raw: config.raw, location: config.location }, noteTarget = null;
  let savedSignature = '', lastLocation = null;
  let layoutStyle = null, renderId = 0;
  let saving = false, saveAgain = false;
  let suspended = false, autoRead = null;
  let presentationUntouched = true;
  const fixed = () => Boolean(config.sections[section].fixed);
  const mode = () => fixed() ? 'fixed' : $('epub-mode').value;
  const normalize = text => text.replace(/[ \t\r\n\f]+/g, ' ').replace(/^[ \t\r\n\f]+|[ \t\r\n\f]+$/g, '');
  const units = text => text.length;

  function resource(path, fragment) {
    return config.resourceUrl + '?path=' + encodeURIComponent(path) + (fragment ? '#' + encodeURIComponent(fragment) : '');
  }
  function notifyError(message) { $('epub-status').textContent = message; }
  function remember() {
    localStorage.setItem(key, JSON.stringify({ mode: $('epub-mode').value, style: $('epub-style').value, fontSize: Number($('epub-font').value) }));
  }
  function locateNodes() {
    // Match the stable plain-text blocks without putting wrappers into book markup.
    // DOM text nodes are grouped using the same boundaries as the import parser.
    const blocks = new Set(['P','DIV','H1','H2','H3','H4','H5','H6','LI','DT','DD','BLOCKQUOTE','PRE','SECTION','ARTICLE','HEADER','FOOTER','TR','FIGURE','FIGCAPTION']);
    const skip = new Set(['HEAD','SCRIPT','STYLE','SVG','MATH','IFRAME','OBJECT','AUDIO','VIDEO','NOSCRIPT']);
    let parts = [], textNodes = [], extracted = [];
    function flush() {
      const text = normalize(parts.join(''));
      if (text) extracted.push({ text, nodes: textNodes.slice() });
      parts = []; textNodes = [];
    }
    function walk(node) {
      if (node.nodeType === 3) { parts.push(node.data); textNodes.push(node); return; }
      if (node.nodeType !== 1 || skip.has(node.tagName)) return;
      if (blocks.has(node.tagName) || node.tagName === 'BR') flush();
      if (node.tagName === 'TD' || node.tagName === 'TH') parts.push(' ');
      for (const child of node.childNodes) walk(child);
      if (blocks.has(node.tagName)) flush();
    }
    walk(doc.body); flush();
    const paragraphs = config.sections[section].paragraphs;
    nodes = paragraphs.map((p, index) => {
      const match = extracted[index];
      return { ...p, nodes: match && match.text === p.text ? match.nodes : [], length: units(p.text) };
    });
  }
  function visiblePoint(entry) {
    for (const node of entry.nodes) {
      if (!node.parentElement || getComputedStyle(node.parentElement).display === 'none') continue;
      const range = doc.createRange(); range.selectNodeContents(node);
      for (const box of range.getClientRects()) {
        if (box.bottom > 0 && box.right > 0 && box.top < frame.clientHeight && box.left < frame.clientWidth) {
          const x = Math.max(1, box.left + 1), y = Math.max(1, box.top + Math.min(8, box.height / 2));
          const caret = doc.caretRangeFromPoint?.(x, y);
          if (caret && entry.nodes.includes(caret.startContainer)) {
            let prefix = '';
            for (const textNode of entry.nodes) {
              if (textNode === caret.startContainer) { prefix += textNode.data.slice(0, caret.startOffset); break; }
              prefix += textNode.data;
            }
            return entry.start + Math.min(Array.from(normalize(prefix)).length, Array.from(entry.text).length);
          }
          return entry.start;
        }
      }
    }
    return null;
  }
  function position() {
    if (!doc) return lastLocation || { raw: currentRaw, section, fraction: 0 };
    const scrolling = doc.scrollingElement;
    let fraction = mode() === 'page' ? page / Math.max(1, pageCount) : scrolling.scrollTop / Math.max(1, scrolling.scrollHeight - scrolling.clientHeight);
    if (fixed()) fraction = 0;
    let raw = config.sections[section].start, anchor = '';
    let nearest = null;
    for (const entry of nodes) {
      const point = visiblePoint(entry);
      if (point !== null) { nearest = entry; raw = point; break; }
    }
    if (nearest) {
      anchor = nearest.nodes[0]?.parentElement?.closest('[id]')?.id || '';
    }
    const result = { raw, section, fraction: Math.max(0, Math.min(1, fraction)), anchor,
      width: frame.clientWidth, height: frame.clientHeight, mode: mode() };
    lastLocation = result;
    return result;
  }
  function chapterPosition(location) {
    const chapters = config.chapters;
    const chapter = (fixed() ? chapters.find(ch => ch.section === location.section) : null)
      || chapters.find(ch => ch.start <= location.raw && location.raw < ch.end)
      || chapters.find(ch => ch.start === config.sections[location.section].start) || chapters[chapters.length - 1];
    let words = 0;
    for (const s of config.sections) for (const p of s.paragraphs) {
      if (p.start >= chapter.start && p.start < location.raw) words += Array.from(p.text).slice(0, location.raw - p.start).join('').length;
    }
    return { chapter, words };
  }
  function progressLabel() {
    const p = position();
    const percent = ((section + p.fraction) / config.sections.length * 100).toFixed(2);
    $('epub-progress').textContent = percent + '%';
    const chapter = chapterPosition(p).chapter;
    $('epub-title').textContent = chapter.title;
    const rawWords = Math.min(config.wordCount, Math.max(0, p.raw));
    $('read-words-text').textContent = rawWords + ' / ' + config.wordCount;
    const pages = document.querySelector('.pages-container'); pages.replaceChildren();
    pages.hidden = mode() === 'slide';
    const count = fixed() ? config.sections.length : pageCount;
    const current = fixed() ? section : page;
    const maxVisible = Math.max(3, Math.floor(pages.clientWidth / 34));
    const start = Math.max(0, Math.min(current - Math.floor(maxVisible / 2), count - maxVisible));
    for (let i = start; i < Math.min(count, start + maxVisible); i++) {
      const button = document.createElement('button'); button.type = 'button';
      button.className = 'join-item btn btn-outline btn-sm' + (i === current ? ' btn-active active' : '');
      button.textContent = i + 1; button.setAttribute('aria-label', '第 ' + (i + 1) + ' 页');
      button.onclick = () => { if (fixed()) load(i, {}, true); else { page = i; doc.scrollingElement.scrollLeft = page * frame.clientWidth; changed(); } };
      pages.appendChild(button);
    }
    $('epub-prev-section').disabled = section === 0;
    $('epub-next-section').disabled = section === config.sections.length - 1;
  }
  function save(beacon = false) {
    if (suspended || !dirty || loading || !doc || !config.authenticated) return;
    if (saving && !beacon) { saveAgain = true; return; }
    const location = position(), { chapter, words } = chapterPosition(location);
    const signature = JSON.stringify([location.section, location.raw, location.fraction]);
    if (signature === savedSignature) return;
    const data = new URLSearchParams({ book_id: config.bookId, chapter_id: chapter.id, words, csrfmiddlewaretoken: csrf,
      epub_location: JSON.stringify(location) });
    if (beacon && navigator.sendBeacon) { navigator.sendBeacon(config.saveUrl, data); return; }
    saving = true;
    fetch(config.saveUrl, { method: 'POST', body: data, credentials: 'same-origin' })
      .then(r => { if (!r.ok) throw Error(); return r.json(); })
      .then(r => { if (!r.success) throw Error(); savedSignature = signature; })
      .catch(() => notifyError('进度保存失败，请稍后重试。'))
      .finally(() => { saving = false; if (saveAgain) { saveAgain = false; save(); } });
  }
  function changed() {
    presentationUntouched = false;
    dirty = true; currentRaw = position().raw; progressLabel();
    clearTimeout(saveTimer); saveTimer = setTimeout(() => save(), 600);
  }
  const readerBackground = () => getComputedStyle(document.querySelector('main')).backgroundColor;
  // Single source of truth for the reader's injected CSS. It is used both when
  // pre-rendering a chapter into the iframe (first paint) and for live updates.
  function readerCss() {
    const width = frame.clientWidth || stage.clientWidth;
    const height = frame.clientHeight || stage.clientHeight;
    if (fixed()) return 'html,body{margin:0!important;padding:0!important;overflow:hidden!important}';
    let css = 'html{scroll-behavior:auto!important} body{margin:0;padding:20px;box-sizing:border-box;}';
    for (const font of config.fonts || []) css += `@font-face{font-family:${JSON.stringify(font.name)};src:url(${JSON.stringify(font.url)}) format(${JSON.stringify(font.format)})}`;
    css += 'img,svg{max-width:100%;height:auto;max-height:85vh;object-fit:contain;cursor:zoom-in} table{display:block;overflow:auto;break-inside:avoid;max-width:100%;max-height:85vh}';
    if (mode() === 'page') css += `html{overflow:hidden!important}body{height:${height}px!important;column-width:${Math.max(100, width - 40)}px!important;column-gap:40px!important;column-fill:auto!important;overflow:visible!important;}`;
    if (mode() === 'slide') css += 'html{overflow:auto!important}body{height:auto!important;columns:auto!important}';
    if ($('epub-style').value === 'custom') css += `body{font-family:${config.settings.fontFamily ? JSON.stringify(config.settings.fontFamily) : (config.language.startsWith('zh') ? 'system-ui,sans-serif' : 'Georgia,"Times New Roman",serif')}!important;font-size:${Math.max(12, Math.min(40, Number($('epub-font').value)))}px!important;line-height:${config.settings.lineHeight}!important;letter-spacing:${config.settings.letterSpacing}px!important;font-weight:${config.settings.fontWeight || 'normal'}!important;color:${config.settings.color || '#222'}!important;background:${readerBackground()}!important}p{font-size:inherit!important;line-height:inherit!important}`;
    return css;
  }
  const customStyle = () => !fixed() && $('epub-style').value === 'custom';
  function addLayoutStyle() {
    layoutStyle?.remove();
    const style = doc.createElement('style'); style.id = 'reader-layout'; layoutStyle = style;
    style.textContent = readerCss(); doc.head.appendChild(style);
    for (const sheet of doc.querySelectorAll('link[rel=stylesheet],style')) {
      if (sheet === layoutStyle) continue;
      sheet.disabled = customStyle();
      if (sheet.sheet) sheet.sheet.disabled = sheet.disabled;
    }
  }
  function fixedSize() {
    const content = doc.querySelector('meta[name=viewport]')?.content || '';
    const width = Number(content.match(/width\s*=\s*([\d.]+)/i)?.[1]) || 800;
    const height = Number(content.match(/height\s*=\s*([\d.]+)/i)?.[1]) || 1200;
    const zoom = $('epub-zoom').value;
    const scale = zoom === 'fit' ? Math.min(stage.clientWidth / width, stage.clientHeight / height) : Number(zoom);
    canvas.style.width = width * scale + 'px'; canvas.style.height = height * scale + 'px';
    frame.style.width = width + 'px'; frame.style.height = height + 'px'; frame.style.transform = `scale(${scale})`;
  }
  function layout() {
    if (!doc) return;
    document.querySelector('main').classList.toggle('read-mode-slide', mode() === 'slide');
    frame.style.transform = ''; frame.style.width = '100%'; frame.style.height = '100%';
    frame.style.background = customStyle() ? readerBackground() : '';
    canvas.style.width = '100%'; canvas.style.height = '100%';
    addLayoutStyle();
    if (fixed()) fixedSize();
    pageCount = fixed() || mode() === 'slide' ? 1 : Math.max(1, Math.ceil(doc.scrollingElement.scrollWidth / frame.clientWidth));
    page = Math.min(page, pageCount - 1);
    $('epub-mode').disabled = fixed(); $('epub-style').disabled = fixed(); $('epub-zoom').disabled = !fixed();
    $('epub-font').disabled = fixed() || $('epub-style').value === 'original';
    $('auto-read-toggle').disabled = fixed();
    document.querySelectorAll('.mode-setting').forEach(button => {
      button.disabled = fixed(); button.classList.toggle('bg-base-300', button.dataset.mode === mode());
    });
    $('epub-display').disabled = loading;
  }
  function restore(target) {
    if (!doc) return;
    let element = target.fragment ? doc.getElementById(target.fragment) : null;
    if (!element && target.location?.anchor) element = doc.getElementById(target.location.anchor);
    const entry = nodes.find(p => p.start <= target.raw && target.raw < p.start + Array.from(p.text).length + 1)
      || nodes.find(p => p.start >= target.raw);
    let textRect = null;
    if (!element && entry?.nodes.length && !target.preferFraction) {
      const wanted = Math.max(0, target.raw - entry.start);
      let prefix = '', found = false;
      for (const textNode of entry.nodes) {
        if (Array.from(normalize(prefix + textNode.data)).length >= wanted) {
          let low = 0, high = textNode.data.length;
          while (low < high) {
            const mid = Math.floor((low + high) / 2);
            if (Array.from(normalize(prefix + textNode.data.slice(0, mid))).length >= wanted) high = mid;
            else low = mid + 1;
          }
          const range = doc.createRange(); range.setStart(textNode, low); range.setEnd(textNode, Math.min(low + 1, textNode.data.length));
          textRect = range.getBoundingClientRect(); found = true;
        }
        if (found) break;
        prefix += textNode.data;
      }
      element = entry.nodes[0].parentElement;
    }
    if (element && !fixed()) {
      const box = textRect || element.getBoundingClientRect();
      if (mode() === 'page') {
        page = Math.max(0, Math.min(pageCount - 1, Math.floor((box.left + doc.scrollingElement.scrollLeft) / frame.clientWidth)));
        doc.scrollingElement.scrollLeft = page * frame.clientWidth;
      } else doc.scrollingElement.scrollTop += box.top - 20;
    } else if (Number.isFinite(target.location?.fraction) && !fixed()) {
      if (mode() === 'page') { page = Math.min(pageCount - 1, Math.floor(target.location.fraction * pageCount)); doc.scrollingElement.scrollLeft = page * frame.clientWidth; }
      else doc.scrollingElement.scrollTop = target.location.fraction * (doc.scrollingElement.scrollHeight - frame.clientHeight);
    }
    progressLabel();
  }
  function urlTarget(href) {
    try {
      const url = new URL(href, location.href);
      if (url.origin !== location.origin || url.pathname !== config.resourceUrl) return null;
      return { path: url.searchParams.get('path'), fragment: decodeURIComponent(url.hash.slice(1)) };
    } catch (_) { return null; }
  }
  function navigateLink(target, isNote) {
    const index = config.sections.findIndex(s => s.href === target.path);
    if (isNote) {
      noteTarget = { ...target, section: index };
      $('epub-note-frame').src = resource(target.path, target.fragment);
      $('epub-note-jump').disabled = index < 0;
      note.showModal(); return;
    }
    if (index < 0) { navigateLink(target, true); return; }
    if (index === section) { restore({ fragment: target.fragment }); changed(); }
    else load(index, { fragment: target.fragment }, true);
  }
  function wireDocument() {
    doc.addEventListener('click', event => {
      $('offcanvassetting').style.display = 'none';
      const link = event.target.closest('a[href]');
      if (link) {
        event.preventDefault();
        const target = urlTarget(link.href || link.getAttribute('href'));
        if (target) navigateLink(target, /noteref|doc-noteref/.test((link.getAttribute('epub:type') || '') + ' ' + (link.getAttribute('role') || '')) || Boolean(link.querySelector('sup')));
        return;
      }
      const image = event.target.closest('img');
      if (image) {
        const clone = document.createElement('img'); clone.src = image.src; clone.alt = image.alt;
        $('epub-image-body').replaceChildren(clone); $('epub-image').showModal();
      }
    });
    doc.addEventListener('keydown', keyboard);
    let wheelLocked = false, touchStart = null;
    doc.addEventListener('wheel', event => {
      if (event.target.closest('table') || fixed()) return;
      if (mode() === 'page') {
        event.preventDefault();
        if (!wheelLocked && event.deltaY) {
          wheelLocked = true; turn(event.deltaY > 0 ? 1 : -1);
          setTimeout(() => { wheelLocked = false; }, 250);
        }
      } else dirty = true;
    }, { passive: false });
    doc.addEventListener('touchstart', event => { touchStart = event.touches[0]?.clientX; }, { passive: true });
    doc.addEventListener('touchmove', () => { if (mode() === 'slide') dirty = true; }, { passive: true });
    doc.addEventListener('touchend', event => {
      if (mode() !== 'page' || event.target.closest('table') || touchStart === null) return;
      const distance = event.changedTouches[0]?.clientX - touchStart;
      if (Math.abs(distance) > 50) turn(distance < 0 ? 1 : -1);
      touchStart = null;
    }, { passive: true });
    doc.addEventListener('scroll', () => { if (!loading && dirty && mode() === 'slide') changed(); });
    // Layout wrappers would change the canonical block index. Keep the original
    // table DOM; the trusted stylesheet handles wide-table overflow instead.
  }
  async function loaded() {
    const token = ++generation;
    try {
      doc = frame.contentDocument;
      layoutStyle = doc?.getElementById('reader-layout') || null;
      if (!doc?.body) throw Error('正文不可访问');
      loading = true;
      await Promise.all(Array.from(doc.images).map(img => img.complete ? Promise.resolve() : new Promise(resolve => { img.addEventListener('load', resolve, { once: true }); img.addEventListener('error', resolve, { once: true }); })));
      if (token !== generation) return;
      $('epub-status').textContent = fixed() ? '固定版式 · 按原页面缩放' : '';
      locateNodes(); wireDocument(); layout();
      await doc.fonts.ready;
      if (token !== generation) return;
      layout(); restore({ ...(pending || {}), preferFraction: Number.isFinite(pending?.location?.fraction)
        && (!pending.location.width || pending.location.width === frame.clientWidth)
        && (!pending.location.height || pending.location.height === frame.clientHeight) });
      loading = false;
      $('epub-display').disabled = false;
      stage.setAttribute('aria-busy', 'false');
    if (dirty) changed();
    } catch (_) { loading = false; $('epub-display').disabled = false; stage.setAttribute('aria-busy', 'false'); notifyError('图文加载失败，可切换纯文本阅读。'); }
  }
  frame.addEventListener('load', loaded);
  // Pre-render the sanitized chapter with the reader CSS already applied so the
  // iframe's first paint is final: no flash of the book's own styles.
  function prepareDocument(html, id) {
    try {
      const parsed = new DOMParser().parseFromString(html, 'text/html');
      const head = parsed.head;
      if (!head) return html;
      if (customStyle()) for (const node of parsed.querySelectorAll('link[rel=stylesheet],style')) node.remove();
      const style = parsed.createElement('style');
      style.id = 'reader-layout'; style.textContent = readerCss();
      head.appendChild(style);
      // A unique marker guarantees the iframe reloads even for identical content.
      head.appendChild(parsed.createComment('render ' + id));
      return '<!doctype html>' + parsed.documentElement.outerHTML;
    } catch (_) { return html; }
  }
  async function renderSection(id, url) {
    try {
      const response = await fetch(url, { credentials: 'same-origin' });
      if (!response.ok) throw Error();
      const html = await response.text();
      if (id !== renderId) return;
      frame.srcdoc = prepareDocument(html, id);
    } catch (_) {
      if (id !== renderId) return;
      frame.src = url;
    }
  }
  function load(index, target = {}, userAction = false) {
    if (index < 0 || index >= config.sections.length) return;
    save(); clearTimeout(saveTimer);
    const id = ++renderId; generation++;
    section = index; page = 0; dirty = userAction; savedSignature = ''; lastLocation = null;
    pending = target; loading = true;
    $('epub-display').disabled = true;
    stage.setAttribute('aria-busy', 'true');
    $('epub-title').textContent = config.toc.find(t => t.href === config.sections[index].href)?.title || config.title;
    $('epub-status').textContent = '正在加载图文…';
    renderSection(id, config.sections[index].url);
  }
  function turn(direction) {
    if (loading) return;
    if (mode() === 'page' && page + direction >= 0 && page + direction < pageCount) {
      page += direction; doc.scrollingElement.scrollLeft = page * frame.clientWidth; changed();
    } else if (mode() === 'slide') {
      const scroll = doc.scrollingElement;
      if ((direction > 0 && scroll.scrollTop + frame.clientHeight < scroll.scrollHeight - 5) || (direction < 0 && scroll.scrollTop > 0)) {
        scroll.scrollTop += direction * frame.clientHeight * .85; changed();
      } else load(section + direction, {}, true);
    } else load(section + direction, direction < 0 ? { location: { fraction: 1 } } : {}, true);
  }
  function keyboard(event) {
    if (['INPUT','SELECT','TEXTAREA'].includes(event.target.tagName) || document.querySelector('dialog[open]')) return;
    if (['ArrowRight','PageDown',' '].includes(event.key)) { event.preventDefault(); turn(config.direction === 'rtl' && event.key === 'ArrowRight' ? -1 : 1); }
    if (['ArrowLeft','PageUp'].includes(event.key)) { event.preventDefault(); turn(config.direction === 'rtl' && event.key === 'ArrowLeft' ? 1 : -1); }
  }
  document.addEventListener('keydown', keyboard);
  $('epub-prev').onclick = () => turn(-1); $('epub-next').onclick = () => turn(1);
  $('epub-prev-section').onclick = () => load(section - 1, {}, true);
  $('epub-next-section').onclick = () => load(section + 1, {}, true);
  function entry(body, label, action) {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'epub-entry'; button.textContent = label;
    button.onclick = () => { if (searchModal.open) searchModal.close(); $('drawer-left').checked = false; action(); }; body.appendChild(button);
  }
  function openSidebar(tab) {
    $('drawer-left').checked = true;
    $('tab-chapters').classList.toggle('hidden', tab !== 'chapters');
    $('tab-bookmarks').classList.toggle('hidden', tab !== 'bookmarks');
    document.querySelectorAll('[data-tab]').forEach(el => el.classList.toggle('tab-active', el.dataset.tab === tab));
  }
  $('epub-toc').onclick = event => {
    event.preventDefault(); openSidebar('chapters');
    const body = document.querySelector('.chapter_list_container'); body.replaceChildren();
    const items = config.toc.length ? config.toc : config.sections.map(s => ({ href: s.href, title: `第 ${s.index + 1} 节` }));
    for (const item of items) entry(body, item.title, () => navigateLink({ path: item.href, fragment: item.fragment }, false));
  };
  // 全文搜索：结果结构与高亮复用 TXT 模式（_search.html + reader.js），样式保持一致。
  function escapeHtml(text) {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function highlightSearchResults(keyword) {
    if (!keyword) return;
    const escaped = keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const regex = new RegExp(escaped, 'g');
    searchResults.querySelectorAll('.search-snippet').forEach(snippet => {
      const text = snippet.textContent;
      if (!text) return;
      regex.lastIndex = 0;
      if (!regex.test(text)) return;
      regex.lastIndex = 0;
      let html = '', last = 0, match;
      while ((match = regex.exec(text)) !== null) {
        if (match.index > last) html += escapeHtml(text.slice(last, match.index));
        html += '<mark class="search-res-mark">' + escapeHtml(match[0]) + '</mark>';
        last = match.index + match[0].length;
        if (match[0].length === 0) regex.lastIndex++;
      }
      if (last < text.length) html += escapeHtml(text.slice(last));
      snippet.innerHTML = html;
    });
  }
  let searchKeyword = '';
  function runSearch(input) {
    const keyword = (input?.value || '').trim();
    if (!keyword) return;
    searchKeyword = keyword;
    let chapterId = '';
    try { chapterId = chapterPosition(lastLocation || position()).chapter.id; } catch (_) {}
    const open = () => { if (!searchModal.open) searchModal.showModal(); };
    const data = new URLSearchParams({ book_id: config.bookId, chapter_id: chapterId, kwd: keyword, csrfmiddlewaretoken: csrf });
    fetch(config.searchUrl, { method: 'POST', body: data })
      .then(response => response.text())
      .then(html => {
        searchResults.innerHTML = html;
        document.querySelectorAll('.content-search').forEach(field => { field.value = keyword; });
        highlightSearchResults(keyword);
        searchResults.querySelector('.list-group-item.active')?.scrollIntoView({ block: 'nearest' });
        open();
      })
      .catch(() => { searchResults.innerHTML = '<div class="text-center text-base-content/50 py-8 text-sm">搜索失败，请重试</div>'; open(); });
  }
  document.querySelectorAll('.content-search').forEach(input => {
    input.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); runSearch(input); } });
    input.addEventListener('input', () => document.querySelectorAll('.content-search').forEach(other => { if (other !== input) other.value = input.value; }));
  });
  document.querySelectorAll('.search-btn').forEach(button => button.addEventListener('click', event => {
    event.preventDefault();
    const input = button.closest('label')?.querySelector('.content-search') || document.querySelector('.content-search');
    const keyword = (input?.value || '').trim();
    if (keyword && keyword !== searchKeyword) { runSearch(input); return; }
    if (!searchModal.open) searchModal.showModal();
  }));
  function showBookmarks() {
    openSidebar('bookmarks');
    const body = document.querySelector('.bookmark_list_container'); body.replaceChildren();
    for (const mark of config.bookmarks) {
      if (Number.isInteger(mark.epub_location?.section)) {
        entry(body, mark.chapter_title + ' · ' + mark.content.slice(0, 100), () => load(mark.epub_location.section, { location: mark.epub_location }, true));
        continue;
      }
      const chapter = config.chapters.find(ch => ch.id === mark.chapter_id); if (!chapter) continue;
      let remaining = mark.words_read, raw = chapter.start;
      for (const s of config.sections) for (const p of s.paragraphs) {
        if (p.start < chapter.start || p.start >= chapter.end) continue;
        if (remaining > 0) { raw = p.start; if (remaining <= p.text.length) { raw += Array.from(p.text.slice(0, remaining)).length; remaining = 0; } else remaining -= p.text.length; }
      }
      const index = config.sections.findIndex(s => s.start <= raw && raw < s.end);
      entry(body, mark.chapter_title + ' · ' + mark.content.slice(0, 100), () => load(Math.max(0, index), { raw }, true));
    }
    if (!config.bookmarks.length) body.textContent = '暂无书签';
  }
  $('epub-bookmarks').onclick = async event => {
    event.preventDefault();
    if (!config.authenticated) { notifyError('请先登录后添加书签'); return; }
    if (!doc || loading) return;
    const location = position(), { chapter, words } = chapterPosition(location);
    const content = doc.getSelection()?.toString() || nodes.find(p => p.start >= location.raw)?.text || chapter.title;
    const data = new URLSearchParams({ book_id: config.bookId, chapter_id: chapter.id, words_read: words, chapter_title: chapter.title, content: content.slice(0, 1000), csrfmiddlewaretoken: csrf, epub_location: JSON.stringify(location) });
    try {
      const response = await fetch(config.bookmarkUrl, { method: 'POST', body: data });
      if (!response.ok || (await response.text()) !== 'ok') throw Error();
      config.bookmarks.unshift({ chapter_id: chapter.id, words_read: words, chapter_title: chapter.title, content, epub_location: location });
      $('epub-status').textContent = '书签已保存';
      if ($('drawer-left').checked && !$('tab-bookmarks').classList.contains('hidden')) showBookmarks();
    } catch (_) { notifyError('书签保存失败'); }
  };
  $('epub-note-frame').onload = () => {
    const noteDoc = $('epub-note-frame').contentDocument; if (!noteDoc) return;
    if (noteTarget?.fragment) noteDoc.getElementById(noteTarget.fragment)?.scrollIntoView();
    noteDoc.addEventListener('click', e => { if (e.target.closest('a')) { e.preventDefault(); note.close(); } });
  };
  $('epub-note-jump').onclick = () => { note.close(); if (noteTarget) navigateLink(noteTarget, false); };
  for (const id of ['epub-mode','epub-font','epub-zoom']) $(id).onchange = async () => {
    const p = lastLocation || position(); remember(); layout(); await doc.fonts.ready; layout(); restore({ raw: p.raw, location: p });
  };
  // Book styles are dropped/kept while pre-rendering, so switching between
  // 原书样式 and 自定义样式 reloads the chapter once, keeping the passage.
  $('epub-style').onchange = () => {
    const p = doc ? (lastLocation || position()) : null; remember();
    if (p) load(section, { raw: p.raw, location: p }, false); else layout();
  };
  // Shared TXT shell controls; only the content engine differs.
  document.querySelector('.setting-btn').onclick = event => {
    event.preventDefault();
    $('offcanvassetting').style.display = $('offcanvassetting').style.display === 'none' ? '' : 'none';
  };
  document.querySelector('.setting-close').onclick = () => { $('offcanvassetting').style.display = 'none'; };
  document.querySelector('.chapter-list-show').onclick = () => $('epub-toc').onclick({ preventDefault() {} });
  document.querySelector('.bookmark-show').onclick = showBookmarks;
  $('sidebar-search').oninput = event => {
    const term = event.target.value.toLocaleLowerCase();
    document.querySelectorAll('#chapter-scroll-container .epub-entry').forEach(el => { el.hidden = !el.textContent.toLocaleLowerCase().includes(term); });
  };
  function applyDimensions() {
    const width = Number(config.settings.pageWidth), height = Number(config.settings.pageHeight);
    stage.style.maxWidth = width >= 200 ? width + 'px' : 'none';
    stage.style.marginInline = width >= 200 ? 'auto' : '';
    stage.style.height = height >= 200 ? height + 'px' : '';
    stage.style.maxHeight = height >= 200 ? 'calc(100% - 80px)' : '';
    stage.style.flex = height >= 200 ? 'none' : '1 1 0%';
    document.querySelector('.page-nav').style.marginTop = height >= 200 ? 'auto' : '';
  }
  let settingsTimer;
  function persistSettings() {
    const s = config.settings;
    const values = { font_size: s.fontSize, read_bg: s.background || '#fff', read_mode: s.mode,
      font_family: s.fontFamily, font_color: s.color, font_weight: s.fontWeight,
      line_height: s.lineHeight, letter_spacing: s.letterSpacing,
      setting_page_width: s.pageWidth, setting_page_height: s.pageHeight, csrfmiddlewaretoken: csrf };
    if (!config.authenticated) {
      try { localStorage.setItem('reader_setting', JSON.stringify({ ...values, page_width: s.pageWidth, page_height: s.pageHeight })); } catch (_) {}
      return;
    }
    clearTimeout(settingsTimer);
    settingsTimer = setTimeout(() => fetch(config.updateSettingsUrl, { method: 'POST', body: new URLSearchParams(values) })
      .catch(() => notifyError('阅读设置保存失败')), 250);
  }
  function settingsChanged() {
    const point = lastLocation || (doc ? position() : null);
    config.settings.fontSize = Number($('epub-font').value);
    config.settings.mode = $('epub-mode').value;
    document.querySelector('.font-value').textContent = config.settings.fontSize;
    document.querySelector('main').style.background = config.settings.background;
    applyDimensions(); persistSettings();
    if (doc) { layout(); restore({ raw: point.raw, location: point }); }
  }
  document.querySelector('.font-value').textContent = config.settings.fontSize;
  for (const [id, property] of [['setting-letter-spacing','letterSpacing'],['setting-line-height','lineHeight'],
    ['setting-page-width','pageWidth'],['setting-page-height','pageHeight']]) {
    $(id).value = config.settings[property];
    $(id).oninput = () => {
      config.settings[property] = Number($(id).value);
      $(id + '-val').textContent = $(id).value;
      settingsChanged();
    };
  }
  for (const button of document.querySelectorAll('.mode-setting')) button.onclick = () => {
    $('epub-mode').value = button.dataset.mode; settingsChanged();
  };
  document.querySelector('.inc-font').onclick = () => { $('epub-font').value = Math.min(40, Number($('epub-font').value) + 1); settingsChanged(); };
  document.querySelector('.dec-font').onclick = () => { $('epub-font').value = Math.max(12, Number($('epub-font').value) - 1); settingsChanged(); };
  for (const option of document.querySelectorAll('.font-setting-option,.font-weight-option')) option.onclick = () => {
    const font = option.classList.contains('font-setting-option');
    config.settings[font ? 'fontFamily' : 'fontWeight'] = option.dataset.value;
    document.querySelector(font ? '.font-setting-label' : '.font-weight-label').textContent = option.textContent;
    document.activeElement.blur(); settingsChanged();
  };
  $('enable-font-color').checked = Boolean(config.settings.color);
  $('setting-font-color').value = config.settings.color || '#333333';
  for (const id of ['enable-font-color','setting-font-color']) $(id).onchange = () => {
    config.settings.color = $('enable-font-color').checked ? $('setting-font-color').value : ''; settingsChanged();
  };
  for (const swatch of document.querySelectorAll('.bg-setting')) swatch.onclick = () => {
    config.settings.background = swatch.dataset.bg || getComputedStyle(swatch).backgroundColor; settingsChanged();
  };
  $('auto-read-toggle').onchange = () => {
    clearInterval(autoRead); autoRead = null;
    if ($('auto-read-toggle').checked && !fixed()) {
      $('epub-mode').value = 'slide'; settingsChanged();
      autoRead = setInterval(() => {
        if (loading || suspended || !doc) return;
        const scroll = doc.scrollingElement;
        scroll.scrollTop += Math.max(1, Number($('auto-read-speed').value) / 10);
        changed();
        if (scroll.scrollTop + frame.clientHeight >= scroll.scrollHeight - 2) {
          if (section < config.sections.length - 1) load(section + 1, {}, true);
          else { clearInterval(autoRead); $('auto-read-toggle').checked = false; }
        }
      }, 50);
    }
  };
  $('auto-read-speed').oninput = () => { $('auto-read-speed-val').textContent = (Number($('auto-read-speed').value) / 10).toFixed(1); };
  document.querySelector('main').style.background = config.settings.background;
  applyDimensions();
  new ResizeObserver(() => {
    clearTimeout(resizeTimer); resizeTimer = setTimeout(() => {
      if (!loading && doc) {
        const p = lastLocation || position();
        const unchanged = p.width === frame.clientWidth && p.height === frame.clientHeight;
        layout(); restore({ raw: p.raw, location: p, preferFraction: unchanged });
      }
    }, 120);
  }).observe(stage);
  frame.addEventListener('pointerleave', () => { if (doc) lastLocation = position(); });
  window.addEventListener('pagehide', () => save(true));
  window.epubPresentation = {
    capture: () => {
      const point = position();
      if (presentationUntouched) point.raw = config.raw;
      const value = chapterPosition(point);
      return { chapter: value.chapter.id, words: value.words, section: point.section };
    },
    suspend: () => { suspended = true; dirty = false; clearTimeout(saveTimer); }
  };
  load(section, pending, false);
})();
