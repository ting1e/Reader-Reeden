/* Scriptless book iframe; only this trusted parent controls navigation/progress. */
(function () {
  'use strict';
  const config = JSON.parse(document.getElementById('epub-config').textContent);
  const aliases = { 'epub-title': 'read-chapter-text', 'epub-progress': 'read-progress-text' };
  const $ = id => document.getElementById(aliases[id] || id);
  const primaryFrame = $('epub-frame'), shadowEl = $('epub-frame-shadow'), stage = $('epub-stage'), canvas = $('epub-canvas');
  // Two iframes: the active one is visible, the other renders the next chapter
  // out of sight so a forward turn becomes a swap instead of a reload.
  let frame = primaryFrame, shadowFrame = shadowEl;
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
  let renderId = 0, activeMark = null, readyMark = null, seq = 0;
  let shadow = null, shadowReady = null, revision = 0;
  let highlightedHref = null, navBuffer = 80, currentChapter = null;
  let committedSection = section, pageNavKey = null, pageNavSlotKey = null, pageNavSlot = 32;
  let stageSize = { w: 0, h: 0 };
  let pendingSnapshot = null, preloadTimer = null;
  // Sanitized chapter HTML is cached per section; it does not depend on the
  // reading style, which prepareDocument() applies at render time.
  const chapterCache = new Map(), chapterInflight = new Map(), preloadQueue = [];
  let cacheBytes = 0, preloadActive = 0;
  const CACHE_LIMIT = 5, CACHE_BYTES = 12 * 1024 * 1024, PRELOAD_CONCURRENCY = 2;
  let saving = false, saveAgain = false;
  let suspended = false, autoRead = null;
  let autoReadPausedUntil = 0, autoReadPrevMode = null, autoReadAdvancedAt = 0, autoReadProgressAt = 0;
  let presentationUntouched = true;
  const sectionFixed = index => Boolean(config.sections[index].fixed);
  const fixed = () => sectionFixed(section);
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
  function collectNodes(targetDoc, index) {
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
    walk(targetDoc.body); flush();
    const paragraphs = config.sections[index].paragraphs;
    return paragraphs.map((p, position) => {
      const match = extracted[position];
      return { ...p, nodes: match && match.text === p.text ? match.nodes : [], length: units(p.text) };
    });
  }
  function locateNodes() { nodes = collectNodes(doc, section); }
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
  // 与文本阅读模式（reader.js）保持一致的页码列表：首页 + 当前页窗口 + 末页，
  // 跳过的区间用省略号占位。修改此处请同步 reader.js 的 buildPageList()。
  function buildPageList(total, cur, budget) {
    if (total <= budget) {
      const all = [];
      for (let i = 1; i <= total; i++) all.push({ num: i, type: 'page' });
      return all;
    }
    const win = Math.max(1, budget - 4);
    const half = Math.floor((win - 1) / 2);
    let start = cur - half, end = start + win - 1;
    if (start < 2) { start = 2; end = start + win - 1; }
    if (end > total - 1) { end = total - 1; start = end - win + 1; if (start < 2) start = 2; }
    let leftEll = start > 2, rightEll = end < total - 1;
    // 利用剩余槽位扩张窗口
    while (2 + (leftEll ? 1 : 0) + (rightEll ? 1 : 0) + (end - start + 1) < budget) {
      const leftGap = start - 2, rightGap = (total - 1) - end;
      if (rightGap >= leftGap && rightGap > 0) { end++; if (end >= total - 1) rightEll = false; }
      else if (leftGap > 0) { start--; if (start <= 2) leftEll = false; }
      else break;
    }
    if (!leftEll && start === 3) start = 2;
    if (!rightEll && end === total - 2) end = total - 1;
    leftEll = start > 2; rightEll = end < total - 1;
    const pages = [{ num: 1, type: 'page' }];
    if (leftEll) pages.push({ num: '...', type: 'ellipsis' });
    for (let i = start; i <= end; i++) pages.push({ num: i, type: 'page' });
    if (rightEll) pages.push({ num: '...', type: 'ellipsis' });
    pages.push({ num: total, type: 'page' });
    return pages;
  }
  // 进度数字千分位：与 reader.js 的 fmtCn() 一致（每 4 位分组）。
  function fmtCn(n) {
    let s = String(Math.round(n)); const parts = [];
    while (s.length > 4) { parts.unshift(s.slice(-4)); s = s.slice(0, -4); }
    if (s) parts.unshift(s);
    return parts.join(',');
  }
  // 目录项类名与文本阅读模式 _chapter_list.html 保持一致
  const TOC_ITEM = 'epub-entry block w-full text-start text-sm leading-normal px-3 py-2 rounded-lg select-none list-none border-0 m-0 transition-colors duration-150 cursor-pointer [-webkit-tap-highlight-color:transparent]';
  const TOC_ACTIVE = ' active bg-base-content text-base-100 font-medium';
  const TOC_IDLE = ' text-base-content hover:bg-base-300';
  // EPUB 一节可能包含多个目录项，所以先按当前 section 的 href 圈定范围，
  // 再优先选中标题与当前章节一致的条目，找不到时退回该节第一个条目。
  function highlightToc() {
    const chapter = currentChapter;
    const href = config.sections[section]?.href;
    const key = href + '\u0000' + (chapter?.title || '');
    if (key === highlightedHref) return;
    highlightedHref = key;
    const items = document.querySelectorAll('.chapter_list_container .epub-entry');
    let fallback = null, match = null;
    items.forEach(el => {
      if (el.dataset.href !== href) return;
      if (!fallback) fallback = el;
      if (!match && chapter && el.textContent === chapter.title) match = el;
    });
    const active = match || fallback;
    items.forEach(el => { el.className = TOC_ITEM + (el === active ? TOC_ACTIVE : TOC_IDLE); });
    active?.scrollIntoView({ block: 'nearest' });
  }
  // 页码条：与文本阅读模式相同的「首页 + 窗口 + 末页 + 省略号」结构。
  // 页码、页数、容器宽度都没变时直接复用，避免自动阅读等高频刷新反复重建。
  function renderPageButtons() {
    const pages = document.querySelector('.pages-container');
    const count = fixed() ? config.sections.length : pageCount;
    const current = fixed() ? section : page;
    const hidden = mode() === 'slide';
    pages.hidden = hidden;
    const key = hidden ? 'hidden' : [count, current, pages.clientWidth].join('/');
    if (key === pageNavKey) return;
    pageNavKey = key;
    pages.replaceChildren();
    if (hidden || count <= 0) return;
    // 用最大页码做样本量单个槽位宽，多位数页码才不会被低估；同宽度同位数时复用结果
    const slotKey = pages.clientWidth + ':' + String(count).length;
    if (slotKey !== pageNavSlotKey) {
      const probe = () => {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'join-item btn btn-outline btn-sm';
        button.textContent = count; button.style.visibility = 'hidden';
        return button;
      };
      const first = probe(), second = probe();
      pages.append(first, second);
      const measured = second.offsetLeft - first.offsetLeft;
      first.remove(); second.remove();
      pageNavSlotKey = slotKey;
      pageNavSlot = measured > 1 ? measured : 32;
    }
    const maxVisible = Math.max(5, Math.floor(pages.clientWidth / pageNavSlot));
    for (const item of buildPageList(count, current + 1, maxVisible)) {
      if (item.type === 'ellipsis') {
        const dots = document.createElement('span');
        dots.className = 'join-item btn btn-sm page-ellipsis';
        dots.setAttribute('aria-hidden', 'true');
        dots.textContent = '…';
        pages.appendChild(dots);
        continue;
      }
      const index = item.num - 1;
      const button = document.createElement('button'); button.type = 'button';
      button.className = 'join-item btn btn-outline btn-sm' + (index === current ? ' btn-active active' : '');
      button.textContent = item.num;
      button.setAttribute('aria-label', '第 ' + item.num + ' 页');
      if (index === current) button.setAttribute('aria-current', 'page');
      button.onclick = () => { if (fixed()) load(index, {}, true); else { page = index; doc.scrollingElement.scrollLeft = page * frame.clientWidth; changed(); } };
      pages.appendChild(button);
    }
  }
  function progressLabel() {
    const p = position();
    const percent = ((section + p.fraction) / config.sections.length * 100).toFixed(2);
    $('epub-progress').textContent = percent + '%';
    const chapter = chapterPosition(p).chapter;
    currentChapter = chapter;
    $('epub-title').textContent = chapter.title;
    const rawWords = Math.min(config.wordCount, Math.max(0, p.raw));
    $('read-words-text').textContent = fmtCn(rawWords) + ' / ' + fmtCn(config.wordCount);
    renderPageButtons();
    highlightToc();
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
  function readerCss(index = section, width = frame.clientWidth || stage.clientWidth, height = frame.clientHeight || stage.clientHeight) {
    if (sectionFixed(index)) return 'html,body{margin:0!important;padding:0!important;overflow:hidden!important}';
    const current = $('epub-mode').value;
    let css = 'html{scroll-behavior:auto!important} body{margin:0;padding:20px;box-sizing:border-box;}';
    for (const font of config.fonts || []) css += `@font-face{font-family:${JSON.stringify(font.name)};src:url(${JSON.stringify(font.url)}) format(${JSON.stringify(font.format)})}`;
    css += 'img,svg{max-width:100%;height:auto;max-height:85vh;object-fit:contain;cursor:zoom-in} table{display:block;overflow:auto;break-inside:avoid;max-width:100%;max-height:85vh}';
    if (current === 'page') css += `html{overflow:hidden!important}body{height:${height}px!important;column-width:${Math.max(100, width - 40)}px!important;column-gap:40px!important;column-fill:auto!important;overflow:visible!important;}`;
    if (current === 'slide') css += 'html{overflow:auto!important}body{height:auto!important;columns:auto!important}';
    if ($('epub-style').value === 'custom') css += `body{font-family:${config.settings.fontFamily ? JSON.stringify(config.settings.fontFamily) : (config.language.startsWith('zh') ? 'system-ui,sans-serif' : 'Georgia,"Times New Roman",serif')}!important;font-size:${Math.max(12, Math.min(40, Number($('epub-font').value)))}px!important;line-height:${config.settings.lineHeight}!important;letter-spacing:${config.settings.letterSpacing}px!important;font-weight:${config.settings.fontWeight || 'normal'}!important;color:${config.settings.color || '#222'}!important;background:${readerBackground()}!important}p{font-size:inherit!important;line-height:inherit!important}`;
    return css;
  }
  const customStyle = (index = section) => !sectionFixed(index) && $('epub-style').value === 'custom';
  function applyLayoutStyle(targetDoc, index) {
    // Adopting a prerendered chapter usually needs no style change at all: the
    // srcdoc already carries the same CSS. Skipping the rebuild keeps the swap
    // from triggering a needless style recalc.
    const css = readerCss(index), custom = customStyle(index);
    const existing = targetDoc.getElementById('reader-layout');
    if (existing && existing.textContent === css) {
      let settled = true;
      for (const sheet of targetDoc.querySelectorAll('link[rel=stylesheet],style')) {
        if (sheet === existing) continue;
        if (sheet.disabled !== custom) { settled = false; break; }
      }
      if (settled) return;
    }
    existing?.remove();
    const style = targetDoc.createElement('style');
    style.id = 'reader-layout'; style.textContent = css;
    targetDoc.head.appendChild(style);
    for (const sheet of targetDoc.querySelectorAll('link[rel=stylesheet],style')) {
      if (sheet === style) continue;
      sheet.disabled = custom;
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
    applyLayoutStyle(doc, section);
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
  // 自动阅读期间用户操作 → 暂停，1s 后自动恢复（与文本阅读模式一致）。
  function pauseAutoRead() { if (autoRead) autoReadPausedUntil = Date.now() + 1000; }
  // 每个文档只绑定一次监听，避免预渲染文档被 adopt 时重复注册（会双跳页）。
  const wiredDocs = new WeakSet();
  function wireDocument() {
    if (wiredDocs.has(doc)) return;
    wiredDocs.add(doc);
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
    // 单击左键翻下一页、右键翻上一页（有选中文本时放行），与文本阅读模式一致。
    let clickStart = null;
    doc.addEventListener('mousedown', event => { if (mode() === 'page' && event.button === 0) clickStart = { x: event.clientX, y: event.clientY }; });
    doc.addEventListener('mouseup', event => {
      if (mode() !== 'page' || event.button !== 0) return;
      if (clickStart) {
        const dx = event.clientX - clickStart.x, dy = event.clientY - clickStart.y;
        clickStart = null;
        if (dx * dx + dy * dy > 25) return;                             // 拖选文本，不翻页
      }
      if (event.target.closest('a, img, table')) return;                // 链接/图片/表格交给各自的处理器
      turn(1);
    });
    doc.addEventListener('contextmenu', event => {
      if (mode() !== 'page') return;
      if (doc.getSelection()?.toString()) return;                       // 有选中文本：放行系统右键菜单
      if (event.target.closest('a, img, table')) return;
      event.preventDefault();
      turn(-1);
    });
    doc.addEventListener('pointerdown', pauseAutoRead, { passive: true });
    doc.addEventListener('wheel', pauseAutoRead, { passive: true });
    doc.addEventListener('touchmove', pauseAutoRead, { passive: true });
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
  const withTimeout = (promise, ms) => Promise.race([promise, new Promise(resolve => setTimeout(resolve, ms))]);
  const docMarker = root => {
    for (const node of root?.head?.childNodes || []) if (node.nodeType === 8 && node.data.startsWith('render ')) return Number(node.data.slice(7));
    return null;
  };
  const waitImages = targetDoc => Promise.all(Array.from(targetDoc.images).map(img => img.complete ? Promise.resolve() : new Promise(resolve => { img.addEventListener('load', resolve, { once: true }); img.addEventListener('error', resolve, { once: true }); })));
  async function loaded() {
    const target = frame.contentDocument;
    if (!target?.body) { finishLoad(); notifyError('图文加载失败，可切换纯文本阅读。'); return; }
    const marker = docMarker(target);
    // Ignore stale/double load events: the marker identifies the exact render
    // this event belongs to, so a superseded chapter can never drive state.
    if (marker === null || marker !== activeMark || marker === readyMark) return;
    doc = target; readyMark = marker;
    const token = ++generation;
    loading = true;
    try {
      await withTimeout(waitImages(doc), 6000);
      if (token !== generation) return;
      $('epub-status').textContent = fixed() ? '固定版式 · 按原页面缩放' : '';
      locateNodes(); wireDocument(); layout();
      if (doc.fonts?.ready) await withTimeout(doc.fonts.ready.catch(() => {}), 3000);
      if (token !== generation) return;
      layout(); restore({ ...(pending || {}), preferFraction: Number.isFinite(pending?.location?.fraction)
        && (!pending.location.width || pending.location.width === frame.clientWidth)
        && (!pending.location.height || pending.location.height === frame.clientHeight) });
      finishLoad();
      committedSection = section;
      if (dirty) changed();
      schedulePreload();
    } catch (_) { finishLoad(); notifyError('图文加载失败，可切换纯文本阅读。'); }
  }
  function finishLoad() {
    loading = false;
    $('epub-display').disabled = false;
    stage.setAttribute('aria-busy', 'false');
  }
  // Both frames stay mounted and swap roles, so dispatch by current role.
  const onFrameLoad = event => {
    if (event.target === frame) loaded();
    else if (event.target === shadowFrame) shadowLoaded();
  };
  primaryFrame.addEventListener('load', onFrameLoad);
  shadowEl.addEventListener('load', onFrameLoad);
  // Pre-render the sanitized chapter with the reader CSS already applied so the
  // iframe's first paint is final: no flash of the book's own styles.
  function prepareDocument(html, id, index = section, width, height) {
    try {
      const parsed = new DOMParser().parseFromString(html, 'text/html');
      const head = parsed.head;
      if (!head) return html;
      if (customStyle(index)) for (const node of parsed.querySelectorAll('link[rel=stylesheet],style')) node.remove();
      const style = parsed.createElement('style');
      style.id = 'reader-layout'; style.textContent = readerCss(index, width, height);
      head.appendChild(style);
      // A unique marker guarantees the iframe reloads even for identical content.
      head.appendChild(parsed.createComment('render ' + id));
      return '<!doctype html>' + parsed.documentElement.outerHTML;
    } catch (_) { return html; }
  }
  function cacheGet(index) {
    if (!chapterCache.has(index)) return null;
    const html = chapterCache.get(index);
    chapterCache.delete(index); chapterCache.set(index, html); // LRU touch
    return html;
  }
  function cacheSet(index, html) {
    if (chapterCache.has(index)) { cacheBytes -= chapterCache.get(index).length; chapterCache.delete(index); }
    chapterCache.set(index, html); cacheBytes += html.length;
    while (chapterCache.size > CACHE_LIMIT || cacheBytes > CACHE_BYTES) {
      if (chapterCache.size <= 1) break;
      const oldest = chapterCache.keys().next().value;
      cacheBytes -= chapterCache.get(oldest).length; chapterCache.delete(oldest);
    }
  }
  // One request per section, reused by navigation and preloading; one retry.
  function fetchChapter(index) {
    const cached = cacheGet(index);
    if (cached !== null) return Promise.resolve(cached);
    if (chapterInflight.has(index)) return chapterInflight.get(index);
    const request = (async () => {
      let lastError;
      for (let attempt = 0; attempt < 2; attempt++) {
        if (attempt) await new Promise(resolve => setTimeout(resolve, 400));
        try {
          const response = await fetch(config.sections[index].url, { credentials: 'same-origin' });
          if (!response.ok) throw Error('HTTP ' + response.status);
          const html = await response.text();
          cacheSet(index, html);
          return html;
        } catch (error) { lastError = error; }
      }
      throw lastError;
    })();
    chapterInflight.set(index, request);
    const settle = () => chapterInflight.delete(index);
    request.then(settle, settle);
    return request;
  }
  function pumpPreload() {
    while (preloadActive < PRELOAD_CONCURRENCY && preloadQueue.length) {
      const index = preloadQueue.shift();
      if (cacheGet(index) !== null || chapterInflight.has(index)) continue;
      preloadActive++;
      fetchChapter(index).then(() => {}, () => {}).then(() => { preloadActive--; pumpPreload(); });
    }
  }
  // Idle work: warm the HTML cache for the neighbouring chapters, then fully
  // render the next chapter in the hidden iframe.
  function schedulePreload() {
    clearTimeout(preloadTimer);
    preloadTimer = setTimeout(() => {
      if (loading) return;
      // Drop chapters queued for a position the reader has already left.
      const wanted = [section + 1, section - 1, section + 2].filter(index => index >= 0 && index < config.sections.length);
      for (let i = preloadQueue.length - 1; i >= 0; i--) if (!wanted.includes(preloadQueue[i])) preloadQueue.splice(i, 1);
      for (const index of wanted) {
        if (cacheGet(index) !== null || chapterInflight.has(index) || preloadQueue.includes(index)) continue;
        preloadQueue.push(index);
      }
      pumpPreload();
      prerenderNext();
    }, 500);
  }
  function invalidateShadow() { revision++; shadow = null; }
  function prerenderNext() {
    const index = section + 1;
    if (index >= config.sections.length) { shadow = null; return; }
    if (shadow && shadow.section === index && shadow.revision === revision && shadow.doc) return;
    const mark = ++seq;
    shadow = { mark, section: index, revision, doc: null, nodes: null, ready: false };
    renderShadow(mark, index);
  }
  async function renderShadow(mark, index) {
    let html;
    try { html = await fetchChapter(index); }
    catch (_) { if (shadow?.mark === mark) shadow = null; return; }
    if (shadow?.mark !== mark) return;
    shadowReady = null;
    // Drop any inline sizing left over from a fixed-layout chapter that used
    // this frame before, and measure against the box the frame will have once
    // it becomes visible instead of the currently displayed one.
    shadowFrame.style.transform = ''; shadowFrame.style.width = '100%'; shadowFrame.style.height = '100%';
    shadowFrame.srcdoc = prepareDocument(html, mark, index, stage.clientWidth, stage.clientHeight);
  }
  async function shadowLoaded() {
    const target = shadowFrame.contentDocument, current = shadow;
    if (!current || !target?.body) return;
    const marker = docMarker(target);
    if (marker === null || marker !== current.mark || marker === shadowReady) return;
    shadowReady = marker;
    current.doc = target;
    try {
      await withTimeout(waitImages(target), 6000);
      if (shadow !== current) return;
      if (target.fonts?.ready) await withTimeout(target.fonts.ready.catch(() => {}), 3000);
    } catch (_) { /* fall back to a partial prerender */ }
    if (shadow !== current || current.revision !== revision) { if (shadow === current) shadow = null; return; }
    current.nodes = collectNodes(target, current.section);
    current.ready = true;
  }
  // Single place that moves the reader between the two iframes, keeping the
  // classes, inert, aria-hidden and focus state in sync.
  function setActiveFrame(next) {
    const previous = frame;
    frame = next; shadowFrame = previous;
    frame.classList.remove('is-prerender'); frame.classList.add('is-active');
    frame.removeAttribute('inert'); frame.removeAttribute('aria-hidden'); frame.removeAttribute('tabindex');
    shadowFrame.classList.add('is-prerender'); shadowFrame.classList.remove('is-active');
    shadowFrame.setAttribute('inert', ''); shadowFrame.setAttribute('aria-hidden', 'true'); shadowFrame.setAttribute('tabindex', '-1');
    // The outgoing frame may still hold focus (setting inert releases it); keep
    // the keyboard working on the frame that is visible now.
    if (previous.contains(document.activeElement)) frame.focus({ preventScroll: true });
    return previous;
  }
  // Swap the prerendered chapter in without re-parsing, re-fetching or hiding
  // the content, so a forward turn shows a finished page immediately.
  function adoptShadow(target, userAction) {
    const current = shadow; shadow = null;
    save(); clearTimeout(saveTimer);
    renderId++; generation++;
    pendingSnapshot = { section, page, dirty, savedSignature, lastLocation, pending };
    setActiveFrame(shadowFrame);
    section = current.section; page = 0; dirty = userAction;
    savedSignature = ''; lastLocation = null; pending = { ...target };
    doc = current.doc; nodes = current.nodes || [];
    activeMark = null; readyMark = current.mark; loading = false;
    $('epub-title').textContent = config.toc.find(t => t.href === config.sections[section].href)?.title || config.title;
    $('epub-status').textContent = '';
    $('epub-display').disabled = false;
    stage.setAttribute('aria-busy', 'false');
    wireDocument(); layout(); restore({ ...pending });
    committedSection = section;
    currentRaw = (lastLocation || position()).raw;
    if (dirty) changed();
    schedulePreload();
  }
  function loadFailed(id) {
    if (id !== renderId) return;
    // Keep the chapter that is already displayed instead of switching to an
    // error page. `section` may already point at a chapter that never rendered
    // (rapid navigation), so roll back to the last committed one and re-derive
    // the page from the document that is actually on screen.
    if (pendingSnapshot) ({ dirty, savedSignature, lastLocation, pending } = pendingSnapshot);
    section = committedSection;
    finishLoad(); notifyError('章节加载失败，请重试');
    if (doc) {
      layout();
      if (mode() === 'page') page = Math.max(0, Math.min(pageCount - 1, Math.round(doc.scrollingElement.scrollLeft / Math.max(1, frame.clientWidth))));
      progressLabel();
    }
    schedulePreload();
  }
  async function renderSection(id, index) {
    let html;
    try { html = await fetchChapter(index); }
    catch (_) { return loadFailed(id); }
    if (id !== renderId) return;
    activeMark = ++seq; readyMark = null;
    frame.srcdoc = prepareDocument(html, activeMark, index);
  }
  function load(index, target = {}, userAction = false) {
    if (index < 0 || index >= config.sections.length) return;
    // A forward turn with no explicit anchor can use the prerendered chapter.
    if (index === section + 1 && !target.fragment && !target.location && !target.raw
      && shadow?.ready && shadow.section === index && shadow.revision === revision) return adoptShadow(target, userAction);
    invalidateShadow();
    save(); clearTimeout(saveTimer);
    const id = ++renderId; generation++;
    pendingSnapshot = { section, page, dirty, savedSignature, lastLocation, pending };
    section = index; page = 0; dirty = userAction; savedSignature = ''; lastLocation = null;
    pending = target; loading = true;
    $('epub-display').disabled = true;
    stage.setAttribute('aria-busy', 'true');
    $('epub-title').textContent = config.toc.find(t => t.href === config.sections[index].href)?.title || config.title;
    $('epub-status').textContent = '正在加载图文…';
    renderSection(id, index);
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
    if (mode() === 'slide') return;   // 滑动模式：键盘翻页禁用，交由正常滚动
    const rtl = config.direction === 'rtl';
    if (['ArrowRight','PageDown',' '].includes(event.key)) { event.preventDefault(); turn(rtl && event.key === 'ArrowRight' ? -1 : 1); }
    if (['ArrowLeft','PageUp'].includes(event.key)) { event.preventDefault(); turn(rtl && event.key === 'ArrowLeft' ? 1 : -1); }
    if (event.key === 'ArrowDown') { event.preventDefault(); load(section + 1, {}, true); }
    if (event.key === 'ArrowUp') { event.preventDefault(); load(section - 1, {}, true); }
  }
  document.addEventListener('keydown', keyboard);
  document.addEventListener('pointerdown', pauseAutoRead, { passive: true });
  document.addEventListener('wheel', pauseAutoRead, { passive: true });
  $('epub-prev').onclick = () => turn(-1); $('epub-next').onclick = () => turn(1);
  $('epub-prev-section').onclick = () => load(section - 1, {}, true);
  $('epub-next-section').onclick = () => load(section + 1, {}, true);
  function entry(body, label, action, href) {
    const button = document.createElement('button'); button.type = 'button'; button.className = TOC_ITEM + TOC_IDLE; button.textContent = label;
    if (href !== undefined) button.dataset.href = href;
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
    for (const item of items) entry(body, item.title, () => navigateLink({ path: item.href, fragment: item.fragment }, false), item.href);
    highlightedHref = null; highlightToc();
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
  // 书签项与文本阅读模式 _bookmark_list.html 的结构一致：图标 + 加粗标题 + 3 行预览。
  function bookmarkEntry(body, mark, action) {
    const button = document.createElement('button'); button.type = 'button';
    button.className = 'epub-bookmark w-full text-left cursor-pointer px-3 py-2 rounded-lg text-sm transition-colors duration-150 border-0 text-base-content/70 hover:bg-base-300';
    const head = document.createElement('div');
    head.className = 'flex items-center gap-2 mb-1 overflow-hidden';
    const icon = document.createElement('iconify-icon');
    icon.setAttribute('icon', 'bi:bookmark-fill'); icon.className = 'flex-shrink-0 text-xs';
    const title = document.createElement('span');
    title.className = 'font-bold text-sm truncate'; title.textContent = mark.chapter_title;
    head.append(icon, title);
    const content = document.createElement('div');
    content.className = 'break-words text-base-content/50 text-md pl-5 line-clamp-3'; content.textContent = mark.content;
    button.append(head, content);
    button.onclick = () => { if (searchModal.open) searchModal.close(); $('drawer-left').checked = false; action(); };
    body.appendChild(button);
  }
  function showBookmarks() {
    openSidebar('bookmarks');
    const body = document.querySelector('.bookmark_list_container'); body.replaceChildren();
    if (!config.bookmarks.length) {
      const empty = document.createElement('div');
      empty.className = 'text-center text-base-content/50 py-8';
      empty.innerHTML = '<iconify-icon icon="bi:bookmark" class="text-2xl"></iconify-icon><p class="mt-2 text-sm">暂无书签</p>';
      body.appendChild(empty);
      return;
    }
    for (const mark of config.bookmarks) {
      if (Number.isInteger(mark.epub_location?.section)) {
        bookmarkEntry(body, mark, () => load(mark.epub_location.section, { location: mark.epub_location }, true));
        continue;
      }
      const chapter = config.chapters.find(ch => ch.id === mark.chapter_id); if (!chapter) continue;
      let remaining = mark.words_read, raw = chapter.start;
      for (const s of config.sections) for (const p of s.paragraphs) {
        if (p.start < chapter.start || p.start >= chapter.end) continue;
        if (remaining > 0) { raw = p.start; if (remaining <= p.text.length) { raw += Array.from(p.text.slice(0, remaining)).length; remaining = 0; } else remaining -= p.text.length; }
      }
      const index = config.sections.findIndex(s => s.start <= raw && raw < s.end);
      bookmarkEntry(body, mark, () => load(Math.max(0, index), { raw }, true));
    }
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
    const p = lastLocation || position(), token = generation;
    remember(); invalidateShadow(); layout();
    await doc.fonts.ready.catch(() => {});
    if (token !== generation) return;   // 期间切了章，别用旧位置覆盖新章节
    layout(); restore({ raw: p.raw, location: p }); schedulePreload();
  };
  // Book styles are dropped/kept while pre-rendering, so switching between
  // 原书样式 and 自定义样式 reloads the chapter once, keeping the passage.
  $('epub-style').onchange = () => {
    const p = doc ? (lastLocation || position()) : null; remember(); invalidateShadow();
    if (p) load(section, { raw: p.raw, location: p }, false); else { layout(); schedulePreload(); }
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
    document.querySelectorAll('#chapter-scroll-container .epub-entry, #chapter-scroll-container .epub-bookmark').forEach(el => { el.hidden = !el.textContent.toLocaleLowerCase().includes(term); });
  };
  // 阅读区高度用实测的页码条高度预留，页码条换行时不会多留/少留空白。
  function measureNavHeight() {
    const measured = Math.ceil(document.querySelector('.page-nav')?.getBoundingClientRect().height || 0);
    if (measured > 0) navBuffer = measured;
    return navBuffer;
  }
  function applyDimensions() {
    const width = Number(config.settings.pageWidth), height = Number(config.settings.pageHeight);
    stage.style.maxWidth = width >= 200 ? width + 'px' : 'none';
    stage.style.marginInline = width >= 200 ? 'auto' : '';
    stage.style.height = height >= 200 ? height + 'px' : '';
    stage.style.maxHeight = height >= 200 ? `calc(100% - ${measureNavHeight()}px)` : '';
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
    invalidateShadow();
    if (doc) { layout(); restore({ raw: point.raw, location: point }); }
    schedulePreload();
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
  function restoreAutoReadMode() {
    if (!autoReadPrevMode) return;
    $('epub-mode').value = autoReadPrevMode; autoReadPrevMode = null; settingsChanged();
  }
  $('auto-read-toggle').onchange = () => {
    clearInterval(autoRead); autoRead = null;
    if ($('auto-read-toggle').checked && !fixed()) {
      autoReadPrevMode = mode();
      $('epub-mode').value = 'slide'; settingsChanged();
      autoReadPausedUntil = 0; autoReadAdvancedAt = 0; autoReadProgressAt = 0;
      autoRead = setInterval(() => {
        if (loading || suspended || !doc || Date.now() < autoReadPausedUntil) return;
        const scroll = doc.scrollingElement;
        if (!fixed()) {
          scroll.scrollTop += Math.max(1, Number($('auto-read-speed').value) / 10);
          // 位置定位会遍历文本节点，按 ~150ms 节流，停读时仍会精确保存
          if (Date.now() - autoReadProgressAt >= 150) { autoReadProgressAt = Date.now(); changed(); }
          else dirty = true;
        }
        if (scroll.scrollTop + frame.clientHeight >= scroll.scrollHeight - 2) {
          // 固定版式一节即一屏（无法滚动），停留一段时间再翻，否则每 50ms 跳过一页
          if (Date.now() - autoReadAdvancedAt < (fixed() ? 4000 : 0)) return;
          autoReadAdvancedAt = Date.now();
          if (section < config.sections.length - 1) load(section + 1, {}, true);
          else { clearInterval(autoRead); autoRead = null; $('auto-read-toggle').checked = false; restoreAutoReadMode(); }
        }
      }, 50);
    } else restoreAutoReadMode();
  };
  $('auto-read-speed').oninput = () => { $('auto-read-speed-val').textContent = (Number($('auto-read-speed').value) / 10).toFixed(1); };
  document.querySelector('main').style.background = config.settings.background;
  applyDimensions();
  new ResizeObserver(() => {
    clearTimeout(resizeTimer); resizeTimer = setTimeout(() => {
      if (loading || !doc) return;
      // Ignore notifications that do not actually change the reading area, so a
      // stale prerender is not thrown away for nothing.
      const w = stage.clientWidth, h = stage.clientHeight;
      if (w === stageSize.w && h === stageSize.h) return;
      stageSize = { w, h };
      const p = lastLocation || position();
      const unchanged = p.width === frame.clientWidth && p.height === frame.clientHeight;
      invalidateShadow(); applyDimensions(); layout(); restore({ raw: p.raw, location: p, preferFraction: unchanged }); schedulePreload();
    }, 120);
  }).observe(stage);
  // Both frames stay mounted and swap roles, so track the pointer on each.
  for (const element of [primaryFrame, shadowEl]) element.addEventListener('pointerleave', () => { if (doc && element === frame) lastLocation = position(); });
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
