// ===== 基础引用 & 全局状态 =====
var drawerCheckbox = document.getElementById('drawer-left');
var searchModal = document.querySelector('.myModal');
var addFont = 0;

var read_mode = user_setting_mode || 'page';

var page_width = $('article').width() + parseInt($('article').css('column-gap'));
var page_num = parseInt(($('#marker').offset().left - $('article').offset().left) / page_width + 1);
var page_contents_len = new Array(page_num + 1).fill(0);

var current_page_idx = 0;
var initial_last_words = last_words;

// ===== 章节缓存系统 =====
const chapterCache = new Map();
const PRELOAD_RANGE = 10;
const SLIDE_KEEP_RANGE = 3;
const SLIDE_LOAD_AHEAD_SCREENS = 1.5;

function getChapterUrl(chapterId) {
    return url_chapter_content.replace('/0/', '/' + chapterId + '/');
}

function chapterIdx(id) { return chapter_ids.indexOf(id); }
function isLastChapter(id) { var i = chapterIdx(id); return i === -1 || i >= chapter_ids.length - 1; }

// ===== 排版应用 =====
function applyTypographyToArticle($el) {
    var $target = $el || $('article');
    $target.css('font-size', $('.font-value').text() + 'px');
    var fontFamily = $('.font-setting').val();
    $target.css('font-family', fontFamily ? fontFamily + ', sans-serif' : '');
    $target.css('color', $('#enable-font-color').is(':checked') ? $('#setting-font-color').val() : '');
    var fontWeight = $('#setting-font-weight').val();
    $target.css('font-weight', fontWeight || '');
    $target.css('letter-spacing', $('#setting-letter-spacing').val() + 'px');
    var lineHeight = parseFloat($('#setting-line-height').val());
    $target.css('line-height', lineHeight > 0 ? lineHeight : '');
}

// ===== 页面尺寸应用 =====
var PAGE_SIZE_MIN = 200;
var pageNavBuffer = 80;
function measurePageNavHeight() {
    var $nav = $('.page-nav');
    if ($nav.length) {
        var h = Math.ceil($nav.outerHeight(false) || 0);
        if (h > 0) pageNavBuffer = h;
    }
}
function applyPageSize() {
    var w = parseInt($('#setting-page-width').val()) || 0;
    var h = parseInt($('#setting-page-height').val()) || 0;
    var $c = $('.article-container');
    $c.css('max-width', w >= PAGE_SIZE_MIN ? w + 'px' : 'none');
    $c.css('margin-left', w >= PAGE_SIZE_MIN ? 'auto' : '');
    $c.css('margin-right', w >= PAGE_SIZE_MIN ? 'auto' : '');
    $c.css('height', h >= PAGE_SIZE_MIN ? h + 'px' : '');
    $c.css('max-height', h >= PAGE_SIZE_MIN ? 'calc(100% - ' + pageNavBuffer + 'px)' : '');
    $c.css('flex', h >= PAGE_SIZE_MIN ? 'none' : '1 1 0%');
    $('.page-nav').css('margin-top', h >= PAGE_SIZE_MIN ? 'auto' : '0');
}

function pageWidthLabel(v) { return v >= PAGE_SIZE_MIN ? v + 'px' : '默认'; }
function pageHeightLabel(v) { return v >= PAGE_SIZE_MIN ? v + 'px' : '默认'; }

// ===== 章节高亮（侧栏目录项） =====
var CHAPTER_ACTIVE_CLASSES = 'active bg-base-content text-base-100 font-medium';
function highlightChapter(id) {
    $('.list-group-item').removeClass(CHAPTER_ACTIVE_CLASSES).addClass('text-base-content');
    $('.list-group-item[data-chapter-id="' + id + '"]').addClass(CHAPTER_ACTIVE_CLASSES).removeClass('text-base-content');
}

// ===== 阅读模式 =====
function applyReadMode() {
    $('main').toggleClass('read-mode-slide', read_mode === 'slide');
}

function updateModeButtons() {
    $('.mode-setting').each(function() {
        var active = $(this).data('mode') === read_mode;
        $(this).toggleClass('btn-active active', active)
               .css('border', active ? '1px solid currentColor' : '');
    });
}

// ===== 键盘快捷键 =====
document.onkeydown = function(e) {
    if (read_mode === 'slide') return;
    var key = window.event ? e.keyCode : e.which;
    var map = { 37: '.prev-page', 38: '.prev-chapter', 39: '.next-page', 40: '.next-chapter' };
    if (key === 32) key = 39; // 空格 → 下一页
    var sel = map[key];
    if (!sel) return;
    if (key === 38 || key === 40) $(sel)[0].click();
    else $(sel).click();
};

// ===== 自动阅读 =====
// 速度档位表：0~3 步进 0.1，3~5 步进 0.2，5~10 步进 0.5
var AUTO_READ_SPEEDS = (function() {
    var arr = [], i;
    for (i = 1; i <= 30; i++) arr.push(i / 10);       // 0.1 → 3.0
    for (i = 32; i <= 50; i += 2) arr.push(i / 10);   // 3.2 → 5.0
    for (i = 55; i <= 100; i += 5) arr.push(i / 10);  // 5.5 → 10
    return arr;
})();
function sliderToSpeed(pos) { return AUTO_READ_SPEEDS[pos] || AUTO_READ_SPEEDS[0]; }
function speedToSlider(speed) {
    var best = 0, bestDiff = Infinity;
    for (var i = 0; i < AUTO_READ_SPEEDS.length; i++) {
        var d = Math.abs(AUTO_READ_SPEEDS[i] - speed);
        if (d < bestDiff) { bestDiff = d; best = i; }
    }
    return best;
}
var autoReadEnabled = false;
localStorage.setItem('auto_read_enabled', 'false');
var autoReadSpeed = (function() {
    var s = parseFloat(localStorage.getItem('auto_read_speed'));
    if (!s || !isFinite(s)) s = 2;
    return sliderToSpeed(speedToSlider(s));
})();
var autoReadActive = false;
var autoReadRAF = null;
var autoReadLastScrollTime = 0;
var autoReadUserPaused = false;
var autoReadResumeTimer = null;
var autoReadSaveTimer = null;
var prevReadMode = null;

function autoReadLoop() {
    if (!autoReadActive) return;
    var c = $('.article-container')[0];
    if (!c) return;
    autoReadLastScrollTime = Date.now();

    c.scrollTop += autoReadSpeed;
    if (c.scrollTop + c.clientHeight >= c.scrollHeight - 1) {
        var $arts = $('.article-container article[data-chapter-id]');
        if ($arts.length > 0) {
            var lastId = parseInt($arts.last().attr('data-chapter-id'));
            if (isLastChapter(lastId)) {
                autoReadEnabled = false;
                stopAutoRead();
                $('#auto-read-toggle').prop('checked', false);
                localStorage.setItem('auto_read_enabled', 'false');
                return;
            }
        }
    }
    autoReadRAF = requestAnimationFrame(autoReadLoop);
}

function startAutoRead() {
    if (autoReadActive) return;
    if (read_mode !== 'slide') {
        prevReadMode = read_mode;
        read_mode = 'slide';
        applyReadMode();
        $('article').css('transform', '');
        initSlideMode();
        ensureSlidePrepend() 
        restoreSlideOffset(page_contents_len[current_page_idx]);

        
    } else {
        prevReadMode = null;
    }
    autoReadLastScrollTime = 0;
    autoReadActive = true;
    autoReadUserPaused = false;

    setTimeout(function() { autoReadRAF = requestAnimationFrame(autoReadLoop); }, 1000);
    if (autoReadSaveTimer) clearInterval(autoReadSaveTimer);
    autoReadSaveTimer = setInterval(save_record, 5000);
}

function stopAutoRead() {
    if (autoReadRAF) { cancelAnimationFrame(autoReadRAF); autoReadRAF = null; }
    autoReadActive = false;
    autoReadUserPaused = false;
    if (autoReadResumeTimer) { clearTimeout(autoReadResumeTimer); autoReadResumeTimer = null; }
    if (autoReadSaveTimer) { clearInterval(autoReadSaveTimer); autoReadSaveTimer = null; }
    save_record();
    if (prevReadMode === 'page') {
        // 自动阅读强制切到 slide 模式：停止时复用缓存原地切回翻页布局
        // 切换 read_mode 之前捕获偏移，避免 save_record 按翻页分支读到错误值
        var slideOffset = getSlideOffset();
        prevReadMode = null;
        read_mode = 'page';
        $.ajax({
            url: url_book_reader,
            type: 'post',
            data: {
                book_id: book_id,
                chapter_id: chapter_id,
                words: slideOffset,
                csrfmiddlewaretoken: csrf_token
            },
            success: function(data) {
                if (typeof data === 'object' && data.success) {
                    updateProgressBar(data.progress, data.words_read, data.total_words);
                }
                applyAfterModeSwitch('page', slideOffset);
            },
            error: function() { applyAfterModeSwitch('page', slideOffset); }
        });
        return;
    }
    prevReadMode = null;
}

function pauseAutoReadByUser() {
    if (!autoReadActive) return;
    if (autoReadRAF) { cancelAnimationFrame(autoReadRAF); autoReadRAF = null; }
    autoReadUserPaused = true;
    if (autoReadResumeTimer) clearTimeout(autoReadResumeTimer);
    autoReadResumeTimer = setTimeout(function() {
        autoReadUserPaused = false;
        autoReadResumeTimer = null;
        if (autoReadActive) autoReadRAF = requestAnimationFrame(autoReadLoop);
    }, 1000);
}

// 自动阅读中：用户点击 / 滚轮滚动 → 暂停自动阅读，2s 后自动恢复
$('.article-container').on('click wheel', function() {
    if (autoReadActive) pauseAutoReadByUser();
});

// ===== 翻页模式：鼠标滚轮 / 左右键翻页 =====
// 复用 .next-page / .prev-page 按钮的委托处理器，自动处理跨章节边界与 save_record()
var pageWheelLockTimer = null;
$('.article-container').on('wheel', function(e) {
    if (read_mode !== 'page') return;
    var dy = e.originalEvent.deltaY;
    if (dy === 0) return;
    if (pageWheelLockTimer) return;  // 300ms 节流，防止一次滚动跳多页
    pageWheelLockTimer = setTimeout(function() { pageWheelLockTimer = null; }, 300);
    if (dy > 0) $('.next-page').click();   // 下滑 → 下一页
    else $('.prev-page').click();          // 上滑 → 上一页
});
// 左键：按下记录起点，抬起时若位移过小视为点击翻页，位移过大（拖选文本）则不翻页
var clickStart = null;
$('.article-container').on('mousedown', function(e) {
    if (read_mode !== 'page' || e.which !== 1) return;  // 仅左键
    clickStart = { x: e.clientX, y: e.clientY };
});
$('.article-container').on('mouseup', function(e) {
    if (read_mode !== 'page' || e.which !== 1) return;
    if (clickStart) {
        var dx = e.clientX - clickStart.x;
        var dy = e.clientY - clickStart.y;
        clickStart = null;
        if (dx * dx + dy * dy > 25) return;  // 位移 > 5px：拖选文本，不翻页
    }
    $('.next-page').click();   // 左键点击 → 下一页
});
// 右键：选中文本时不翻页（放行系统右键菜单便于复制），否则翻上一页
$('.article-container').on('contextmenu', function(e) {
    if (read_mode !== 'page') return;
    var sel = window.getSelection ? window.getSelection().toString() : '';
    if (sel) return;          // 有选中文本：不翻页，放行右键菜单
    e.preventDefault();         // 屏蔽系统右键菜单
    $('.prev-page').click();    // 右键点击 → 上一页
});

// ===== 滑动模式：章节挂载 / 视口补偿 =====
var slideLoadedChapters = new Set();

function markSlideArticle($art, chapterId) {
    $art.attr('data-chapter-id', chapterId);
    $art.find('#marker').remove();
}

// dir: 'append'（向下追加）或 'prepend'（向上插入并补偿滚动位置）
function mountSlideChapter(chapterId, dir) {
    if (slideLoadedChapters.has(chapterId) || !chapterCache.has(chapterId)) return false;
    var $art = $(chapterCache.get(chapterId).chapter_view);
    markSlideArticle($art, chapterId);
    var c = $('.article-container')[0];
    var prevHeight = dir === 'prepend' ? c.scrollHeight : 0;
    $('.article-container')[dir]($art);
    slideLoadedChapters.add(chapterId);
    applyTypographyToArticle($art);
    if (dir === 'prepend') c.scrollTop += c.scrollHeight - prevHeight;
    return true;
}

function appendSlideChapter(id)  { return mountSlideChapter(id, 'append'); }
function prependSlideChapter(id) { return mountSlideChapter(id, 'prepend'); }

// 按方向确保视口前后章节都已挂载；dir='append' 处理下方，'prepend' 处理上方
// 提前加载：距离底部/顶部约 SLIDE_LOAD_AHEAD_SCREENS 个视口高度时即挂载相邻章节
function ensureSlideChapters(dir) {
    var c = $('.article-container')[0];
    if (!c) return;
    var ahead = c.clientHeight * SLIDE_LOAD_AHEAD_SCREENS;
    var needMore = dir === 'append'
        ? function() { return c.scrollTop + c.clientHeight >= c.scrollHeight - ahead; }
        : function() { return c.scrollTop < ahead; };

    while (needMore()) {
        var $arts = $('.article-container article[data-chapter-id]');
        if ($arts.length === 0) break;
        var edgeId = parseInt((dir === 'append' ? $arts.last() : $arts.first()).attr('data-chapter-id'));
        var idx = chapterIdx(edgeId);
        if (idx === -1 || (dir === 'append' ? idx >= chapter_ids.length - 1 : idx <= 0)) break;
        var nextId = chapter_ids[dir === 'append' ? idx + 1 : idx - 1];
        if (slideLoadedChapters.has(nextId)) break;
        if (chapterCache.has(nextId)) {
            (dir === 'append' ? appendSlideChapter : prependSlideChapter)(nextId);
        } else {
            preloadChapter(nextId).then(function() { ensureSlideChapters(dir); });
            break;
        }
    }
}
function ensureSlideAppend()  { ensureSlideChapters('append'); }
function ensureSlidePrepend() { ensureSlideChapters('prepend'); }

// 裁剪 DOM 中远离当前章节的 article，防止滑动模式下 DOM 无限增长
// 保留当前章节前后 keepRange 章内的 article；视口内可见的 article 始终保留
function pruneSlideDom(currentId, keepRange) {
    var c = $('.article-container')[0];
    if (!c) return;
    var curIdx = chapterIdx(currentId);
    if (curIdx === -1) return;
    var keepStart = Math.max(0, curIdx - keepRange);
    var keepEnd = Math.min(chapter_ids.length - 1, curIdx + keepRange);
    var $container = $('.article-container');
    var containerTop = $container.offset().top;
    var viewTop = c.scrollTop;
    var viewBottom = c.scrollTop + c.clientHeight;
    var removedAboveHeight = 0;
    $('.article-container article[data-chapter-id]').each(function() {
        var id = parseInt($(this).attr('data-chapter-id'));
        var idx = chapterIdx(id);
        if (idx === -1) return;                  // 未知章节，保留
        if (idx >= keepStart && idx <= keepEnd) return;  // 保留窗口内
        var artTopInContent = ($(this).offset().top - containerTop) + c.scrollTop;
        var artBottomInContent = artTopInContent + this.offsetHeight;
        var fullyAbove = artBottomInContent <= viewTop + 1;
        var fullyBelow = artTopInContent >= viewBottom - 1;
        if (!fullyAbove && !fullyBelow) return;   // 视口内可见，保留
        if (fullyAbove) removedAboveHeight += this.offsetHeight;
        $(this).remove();
        slideLoadedChapters.delete(id);
    });
    if (removedAboveHeight > 0) c.scrollTop -= removedAboveHeight;
}

function initSlideMode() {
    $('article').css('transform', 'translateX(0px)');
    slideLoadedChapters = new Set();
    var $art = $('article').first();
    markSlideArticle($art, chapter_id);
    slideLoadedChapters.add(chapter_id);
}

function getCurrentSlideArticle() {
    var containerTop = $('.article-container').offset().top;
    var current = null;
    $('.article-container article[data-chapter-id]').each(function() {
        if ($(this).offset().top <= containerTop + 5) current = this;
    });
    return current;
}

function getSlideOffset() {
    var cur = getCurrentSlideArticle();
    if (!cur) return 0;
    var containerTop = $('.article-container').offset().top;
    var offset = 0;
    $(cur).find('p').each(function() {
        if ($(this).offset().top >= containerTop) return false;
        offset += $(this).text().length;
    });
    return offset;
}

// 滚动到指定文字偏移量对应的段落
function restoreSlideOffset(offset) {
    if (offset <= 0) {  return; }
    var accumulated = 0, target = null;
    $('.article-container article').first().find('p').each(function() {
        if (accumulated + $(this).text().length >= offset) { target = this; return false; }
        accumulated += $(this).text().length;
    });
    var $container = $('.article-container');
    if (target) {
        var top = $(target).offset().top - $container.offset().top + $container.scrollTop();
        $container.scrollTop(top);
    } else {
        $container.scrollTop(0);
    }
}

function restoreLastPosition() {
    if (read_mode === 'slide') {
        var c = $('.article-container');
        c.scrollTop(c[0].scrollHeight);
    } else {
        goToPage(page_num - 1);
    }
}

// ===== 翻页模式 =====
function reinitPages() {
    if (read_mode === 'slide') {
        $('.pages-container').empty();
        $('article').css('transform', 'translateX(0px)');
        last_words = 0;
        return;
    }
    page_width = $('article').width() + parseInt($('article').css('column-gap'));
    page_num = parseInt(($('#marker').offset().left - $('article').offset().left) / page_width + 1);
    page_contents_len = new Array(page_num + 1).fill(0);

    $('article p').each(function(i, e) {
        page_contents_len[parseInt($(e).offset().left / page_width) + 1] += $(e).text().length;
    });
    for (var i = 1; i < page_num + 1; i++) page_contents_len[i] += page_contents_len[i - 1];

    current_page_idx = 0;
    renderPageButtons(current_page_idx);
    $('article').css('transform', 'translateX(0px)');
    last_words = 0;
}

function renderPageButtons(activeIdx) {
    var $container = $('.pages-container').empty();
    if (page_num <= 0) return;

    // 用最大页码作为探测样本来测量单按钮槽位宽，避免 1 位数样本低估多位数按钮的宽度
    var $probe = $(
        '<button class="join-item btn btn-outline btn-sm page-num page-item" style="visibility:hidden;">' + page_num + '</button>' +
        '<button class="join-item btn btn-outline btn-sm page-num page-item" style="visibility:hidden;">' + page_num + '</button>'
    );
    $container.append($probe);
    var btnSlot = $probe.eq(1).offset().left - $probe.eq(0).offset().left;
    $probe.remove();
    if (!btnSlot || btnSlot < 1) btnSlot = 32;

    var maxVisible = Math.max(5, Math.floor($container.width() / btnSlot));
    var pages = buildPageList(page_num, activeIdx + 1, maxVisible);

    $.each(pages, function(i, p) {
        if (p.type === 'ellipsis') {
            $container.append($('<span class="join-item btn btn-sm" style="border:0;background:transparent;color:inherit;">…</span>'));
        } else {
            var $btn = $('<button class="join-item btn btn-outline btn-sm page-num page-item">' + p.num + '</button>');
            if (p.num - 1 === activeIdx) $btn.addClass('btn-active active').css('border', '1px solid currentColor');
            $container.append($btn);
        }
    });
}

// 计算分页按钮显示列表（首页 + 窗口 + 末页 + 省略号）
function buildPageList(total, cur, budget) {
    if (total <= budget) {
        var all = [];
        for (var i = 1; i <= total; i++) all.push({ num: i, type: 'page' });
        return all;
    }
    var win = Math.max(1, budget - 4);
    var half = Math.floor((win - 1) / 2);
    var start = cur - half, end = start + win - 1;
    if (start < 2) { start = 2; end = start + win - 1; }
    if (end > total - 1) { end = total - 1; start = end - win + 1; if (start < 2) start = 2; }

    var leftEll = start > 2, rightEll = end < total - 1;
    // 利用剩余槽位扩张窗口
    while (2 + (leftEll ? 1 : 0) + (rightEll ? 1 : 0) + (end - start + 1) < budget) {
        var leftGap = start - 2, rightGap = (total - 1) - end;
        if (rightGap >= leftGap && rightGap > 0) { end++; if (end >= total - 1) rightEll = false; }
        else if (leftGap > 0) { start--; if (start <= 2) leftEll = false; }
        else break;
    }
    if (!leftEll && start === 3) start = 2;
    if (!rightEll && end === total - 2) end = total - 1;
    leftEll = start > 2; rightEll = end < total - 1;

    var pages = [{ num: 1, type: 'page' }];
    if (leftEll) pages.push({ num: '...', type: 'ellipsis' });
    for (var j = start; j <= end; j++) pages.push({ num: j, type: 'page' });
    if (rightEll) pages.push({ num: '...', type: 'ellipsis' });
    pages.push({ num: total, type: 'page' });
    return pages;
}

function goToPage(idx) {
    if (read_mode === 'slide' || idx < 0 || idx >= page_num) return;
    current_page_idx = idx;
    renderPageButtons(idx);
    $('article').css('transform', 'translateX(-' + page_width * idx + 'px)');
}

function goToPageByOffset(offset) {
    if (read_mode === 'slide') { restoreSlideOffset(offset); return true; }
    for (var i = 0; i < page_num + 1; i++) {
        if (page_contents_len[i] > offset) { goToPage(i - 1); return true; }
    }
    return false;
}

// ===== 章节导航 =====
function navigateToChapter(targetId) {
    if (targetId === chapter_id) return;
    if (chapterCache.has(targetId)) {
        loadChapterFromCache(targetId);
    } else {
        // 章节未缓存：异步预加载后原地加载，失败则回退到整页 POST 导航
        preloadChapter(targetId).then(function() {
            if (!loadChapterFromCache(targetId)) {
                var form = $('<form method="POST" style="display:none;">')
                    .attr('action', url_book_reader)
                    .append($('<input>').attr({ type: 'hidden', name: 'csrfmiddlewaretoken', value: csrf_token }))
                    .append($('<input>').attr({ type: 'hidden', name: 'book_id', value: book_id }))
                    .append($('<input>').attr({ type: 'hidden', name: 'chapter_id', value: targetId }));
                $('body').append(form);
                form.submit();
            }
        });
    }
}

function scrollToSlideChapter(chapterId) {
    var c = $('.article-container')[0];
    if (!c) return;
    var $art = $('.article-container article[data-chapter-id="' + chapterId + '"]');
    if ($art.length === 0) return;
    c.scrollTop = $art.offset().top - $('.article-container').offset().top + c.scrollTop;
}

// 在滑动模式中按方向依次挂载中间章节至目标章节，再滚动到目标位置
function jumpToSlideChapter(targetId) {
    if (slideLoadedChapters.has(targetId)) { scrollToSlideChapter(targetId); return; }

    var curIdx = chapterIdx(chapter_id);
    var targetIdx = chapterIdx(targetId);
    if (curIdx === -1 || targetIdx === -1) { navigateToChapter(targetId); return; }

    var dir = targetIdx > curIdx ? 1 : -1;
    var pending = [];
    for (var i = curIdx + dir; (dir > 0 ? i <= targetIdx : i >= targetIdx); i += dir) {
        if (!slideLoadedChapters.has(chapter_ids[i])) pending.push(chapter_ids[i]);
    }

    (function drain() {
        while (pending.length > 0 && chapterCache.has(pending[0])) {
            (dir > 0 ? appendSlideChapter : prependSlideChapter)(pending.shift());
        }
        if (pending.length === 0) {
            scrollToSlideChapter(targetId);
        } else {
            preloadChapter(pending[0]).then(drain);
        }
    })();
}

// ===== 状态显示 =====
function fmtCn(n) {
    var s = String(Math.round(n)), parts = [];
    while (s.length > 4) { parts.unshift(s.slice(-4)); s = s.slice(0, -4); }
    if (s) parts.unshift(s);
    return parts.join(',');
}

function updateProgressBar(progress, wordsRead, totalWords) {
    var $prog = $('#read-progress-text'), $words = $('#read-words-text');
    if ($prog.length && typeof progress === 'number') $prog.text(progress.toFixed(2) + '%');
    if ($words.length && typeof wordsRead === 'number' && typeof totalWords === 'number') {
        $words.text(fmtCn(wordsRead) + ' / ' + fmtCn(totalWords));
    }
}

function updateChapterDisplay() {
    var $ch = $('#read-chapter-text');
    if ($ch.length && chapter_title) $ch.text(chapter_title);
}

// ===== 进度保存 =====
function currentWordsRead() {
    return read_mode === 'slide' ? getSlideOffset() : (page_contents_len[current_page_idx] || 0);
}

function save_record(callback) {
    $.ajax({
        url: url_book_reader,
        type: 'post',
        data: {
            book_id: book_id,
            chapter_id: chapter_id,
            words: currentWordsRead(),
            csrfmiddlewaretoken: csrf_token
        },
        success: function(data) {
            if (typeof data === 'object' && data.success) {
                updateProgressBar(data.progress, data.words_read, data.total_words);
            }
            if (callback) callback();
        },
        error: function() { if (callback) callback(); }
    });
}

// ===== 初始化 =====
applyReadMode();
updateModeButtons();
if (read_mode === 'slide') initSlideMode();
measurePageNavHeight();
applyPageSize();
reinitPages();

// ===== 上下章按钮 =====
$('.prev-chapter, .next-chapter').click(function(e) {
    e.preventDefault();
    if (typeof chapter_ids === 'undefined') return;
    var idx = chapterIdx(chapter_id);
    var isPrev = $(this).hasClass('prev-chapter');
    if (isPrev ? idx <= 0 : (idx === -1 || idx >= chapter_ids.length - 1)) return;
    var targetId = chapter_ids[idx + (isPrev ? -1 : 1)];
    if (read_mode === 'slide') jumpToSlideChapter(targetId);
    else navigateToChapter(targetId);
});

// ===== 翻页按钮区（含上/下页、页码）委托 =====
$('.page-nav').on('click', '.page-item', function() {
    var $this = $(this);
    if ($this.hasClass('prev-chapter') || $this.hasClass('next-chapter')) return;
    if ($this.hasClass('prev-page')) {
        if (current_page_idx > 0) { goToPage(current_page_idx - 1); save_record(); }
        else { localStorage.setItem('prev-chapter', 'true'); $('.prev-chapter')[0].click(); }
    } else if ($this.hasClass('next-page')) {
        if (current_page_idx < page_num - 1) { goToPage(current_page_idx + 1); save_record(); }
        else { $('.next-chapter')[0].click(); }
    } else if ($this.hasClass('page-num')) {
        var idx = parseInt($this.text()) - 1;
        if (idx === current_page_idx) return;
        goToPage(idx);
        save_record();
    }
});

// ===== 滑动模式滚动监听：自动保存进度、检测当前章节、追加相邻章节 =====
var slideSaveTimer = null;
$('.article-container').on('scroll', function() {
    if (read_mode !== 'slide') return;
    // if (autoReadActive && (Date.now() - autoReadLastScrollTime > 1000)) pauseAutoReadByUser();

    var cur = getCurrentSlideArticle();
    if (cur) {
        var newChapterId = parseInt($(cur).attr('data-chapter-id'));
        if (newChapterId !== chapter_id) {
            chapter_id = newChapterId;
            var $h3 = $(cur).find('h3').first();
            if ($h3.length) chapter_title = $h3.text().trim();
            updateChapterDisplay();
            highlightChapter(chapter_id);
            pruneSlideDom(chapter_id, SLIDE_KEEP_RANGE);
        }
    }
    ensureSlideAppend();
    ensureSlidePrepend();
    if (slideSaveTimer) clearTimeout(slideSaveTimer);
    slideSaveTimer = setTimeout(save_record, 500);
});

// ===== 初始位置恢复 =====
if (localStorage.getItem('prev-chapter')) {
    restoreLastPosition();
    localStorage.removeItem('prev-chapter');
} else {
    goToPageByOffset(initial_last_words);
}
if (read_mode === 'slide') ensureSlideAppend();

// ===== 搜索 =====
var searchKwd = '';
// ===== 搜索结果高亮（全文匹配） =====
// 清除旧高亮：解包 <mark> 并合并文本节点，避免重复累积
function clearSearchHighlight() {
    var $marks = $('article .search-mark');
    if (!$marks.length) return;
    $marks.each(function() { $(this).contents().unwrap(); });
    document.querySelectorAll('article').forEach(function(art) {
        if (typeof art.normalize === 'function') art.normalize();
    });
}
// 高亮文章内的搜索关键词：遍历文本节点，包裹为 <mark class="search-mark">
function highlightSearchTerm(kwd) {
    clearSearchHighlight();
    if (!kwd) return;
    var escaped = kwd.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    var regex = new RegExp(escaped, 'g');
    var firstMark = null;
    document.querySelectorAll('article').forEach(function(article) {
        var walker = document.createTreeWalker(article, NodeFilter.SHOW_TEXT, {
            acceptNode: function(node) {
                if (!node.nodeValue) return NodeFilter.FILTER_REJECT;
                regex.lastIndex = 0;
                if (!regex.test(node.nodeValue)) return NodeFilter.FILTER_REJECT;
                var p = node.parentNode;
                if (p && (p.nodeName === 'MARK' || (p.classList && p.classList.contains('search-mark')))) return NodeFilter.FILTER_REJECT;
                return NodeFilter.FILTER_ACCEPT;
            }
        });
        var targets = [];
        while (walker.nextNode()) targets.push(walker.currentNode);
        targets.forEach(function(node) {
            regex.lastIndex = 0;
            var text = node.nodeValue;
            var frag = document.createDocumentFragment();
            var last = 0, m;
            while ((m = regex.exec(text)) !== null) {
                if (m.index > last) frag.appendChild(document.createTextNode(text.slice(last, m.index)));
                var mark = document.createElement('mark');
                mark.className = 'search-mark';
                mark.textContent = m[0];
                frag.appendChild(mark);
                if (!firstMark) firstMark = mark;
                last = m.index + m[0].length;
                if (m[0].length === 0) regex.lastIndex++;  // 防止零宽死循环
            }
            if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
            node.parentNode.replaceChild(frag, node);
        });
    });
    if (firstMark && read_mode === 'slide') {
        try { firstMark.scrollIntoView({ block: 'center' }); } catch(e) {}
    }
}
// HTML 转义：防止书籍原文中的标签在 .html() 注入时被执行
function escapeHtml(s) {
    var amp = String.fromCharCode(38);  // '&'
    return s.replace(/&/g, amp + 'amp;').replace(/</g, amp + 'lt;').replace(/>/g, amp + 'gt;').replace(/"/g, amp + 'quot;').replace(/'/g, amp + '#39;');
}
// 高亮搜索结果列表中的关键词（仅作用于 .search-snippet 文本，用 <mark> 包裹）
function highlightSearchResultSnippets(kwd) {
    if (!kwd) return;
    var escaped = kwd.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    var regex = new RegExp(escaped, 'g');
    $('.search-res .search-snippet').each(function() {
        var text = $(this).text();
        if (!text) return;
        regex.lastIndex = 0;
        if (!regex.test(text)) return;
        regex.lastIndex = 0;
        var html = '', last = 0, m;
        while ((m = regex.exec(text)) !== null) {
            if (m.index > last) html += escapeHtml(text.slice(last, m.index));
            html += '<mark class="search-res-mark">' + escapeHtml(m[0]) + '</mark>';
            last = m.index + m[0].length;
            if (m[0].length === 0) regex.lastIndex++;
        }
        if (last < text.length) html += escapeHtml(text.slice(last));
        $(this).html(html);
    });
}

function doContentSearch($input) {
    var kwd = ($input.val() || '').trim();
    if (!kwd) return;
    searchKwd = kwd;
    $.ajax({
        url: url_book_reader,
        type: 'post',
        data: { book_id: book_id, chapter_id: chapter_id, kwd: kwd, csrfmiddlewaretoken: csrf_token },
        success: function(data) {
            $('.search-res').html(data);
            $('.modal .content-search').val(kwd);
            highlightSearchResultSnippets(kwd);
            var $active = $('.search-res .list-group-item.active');
            if ($active[0]) $active[0].scrollIntoView({ block: 'nearest' });
        },
        error: function() {
            $('.search-res').html('<div class="text-center text-base-content/50 py-8 text-sm">搜索失败，请重试</div>');
        }
    });
    if (searchModal && typeof searchModal.showModal === 'function') searchModal.showModal();
}
$('.content-search').on('keydown', function(e) {
    if (e.which !== 13) return;  // Enter 键触发搜索
    e.preventDefault();
    doContentSearch($(this));
});

// 搜索结果列表：按当前章节更新高亮（异步跳转后章节已变，但结果列表仍为旧高亮）
function updateSearchResultHighlight() {
    var $items = $('.search-res .list-group-item');
    if (!$items.length) return;
    $items.removeClass('active bg-primary/15 border-primary text-primary')
          .addClass('bg-base-100 border-base-200 hover:border-base-300 hover:bg-base-200/60');
    $items.find('.search-chapter-label').removeClass('text-primary/80').addClass('text-base-content/50');
    $items.each(function() {
        var cid = parseInt($(this).closest('form').find('input[name="chapter_id"]').val());
        if (cid === chapter_id) {
            $(this).removeClass('bg-base-100 border-base-200 hover:border-base-300 hover:bg-base-200/60')
                   .addClass('active bg-primary/15 border-primary text-primary');
            $(this).find('.search-chapter-label').removeClass('text-base-content/50').addClass('text-primary/80');
        }
    });
}

$('.search-btn').click(function(e) {
    e.preventDefault();  // 阻止 label 原生聚焦输入框
    if (!searchModal || typeof searchModal.showModal !== 'function') return;
    var $input = $(this).closest('label').find('input.content-search');
    var kwd = ($input.val() || '').trim();
    if (searchModal.open) {
        // 弹窗内搜索图标：直接搜索
        if (kwd) doContentSearch($input);
        return;
    }
    // 页头图标：重开弹窗
    if (kwd && kwd !== searchKwd) {
        doContentSearch($input);   // 关键词变了：重新搜索并开弹窗
    } else {
        searchModal.showModal();     // 关键词未变或为空：直接重开，还原上次结果
        updateSearchResultHighlight();  // 更新章节高亮为当前章节
        highlightSearchResultSnippets(searchKwd);  // 重新高亮结果列表关键词
    }
});

// ===== 设置面板 =====
function showSettingsToast() {
    var toast = $('#offcanvassetting');
    $('.font-value').text(parseInt($('article').css('font-size')));

    // 选中态检测：若用户已选过 bg-setting 则保持，否则按 user_setting_bg 自动选中
    var hadChoose = $('.bg-setting.bodder.border-4.border-secondary').length > 0;
    if (!hadChoose) {
        $('.bg-setting').each(function() {
            var bgVal = $(this).attr('data-bg') || $(this).css('background-color');
            if (bgVal == user_setting_bg) $(this).addClass('bodder border-4 border-secondary');
        });
    }

    updateModeButtons();
    $('#auto-read-toggle').prop('checked', autoReadEnabled);
    $('#auto-read-speed').val(speedToSlider(autoReadSpeed));
    $('#auto-read-speed-val').text(autoReadSpeed);
    toast.show();
}

$('.setting-btn').click(function() {
    var toast = $('#offcanvassetting');
    toast.is(':visible') ? toast.hide() : showSettingsToast();
});
$('.setting-close').click(function() { $('#offcanvassetting').hide(); });

$('.inc-font').click(function() {
    var font = parseInt($('article').css('font-size')) + 1;
    $('.font-value').text(font);
    $('article').css('font-size', font);
});
$('.dec-font').click(function() {
    var font = parseInt($('article').css('font-size')) - 1;
    $('.font-value').text(font);
    $('article').css('font-size', font);
});

var bgFontColorMap = {
    'read-white': '',
    'read-blue': '#1f3a5a',
    'read-green': '#1f3a1f',
    'read-yellow': '#3a2a1a',
    'read-black': 'rgb(90, 90, 90)',
    'read-theme': 'var(--color-base-content)'
};

$('.bg-setting').click(function() {
    var $this = $(this);
    $('main').css('background', $this.attr('data-bg') || $this.css('background'));
    $('.bg-setting').removeClass('bodder border-4 border-secondary');
    $this.addClass('bodder border-4 border-secondary');
    for (var cls in bgFontColorMap) {
        if ($this.hasClass(cls)) { $('main').css('color', bgFontColorMap[cls]); break; }
    }
});

function collectSettings() {
    var $activeBg = $('.bg-setting.bodder');
    var readBg = ($activeBg.length && $activeBg.attr('data-bg'))
        ? $activeBg.attr('data-bg')
        : $('main').css('background-color');
    return {
        font_size: $('.font-value').text(),
        read_bg: readBg,
        read_mode: read_mode,
        font_family: $('.font-setting').val() || '',
        font_color: $('#enable-font-color').is(':checked') ? ($('#setting-font-color').val() || '') : '',
        letter_spacing: $('#setting-letter-spacing').val() || '0',
        line_height: $('#setting-line-height').val() || '1.2',
        font_weight: $('#setting-font-weight').val() || '',
        setting_page_width: $('#setting-page-width').val() || '0',
        setting_page_height: $('#setting-page-height').val() || '0',
        csrfmiddlewaretoken: csrf_token
    };
}

function saveSettings(successFn) {
    if (is_anon_reader) {
        try {
            var s = collectSettings();
            delete s.csrfmiddlewaretoken;
            // normalize page size keys: collectSettings uses setting_ prefix for server API,
            // localStorage uses clean names to match the restore script
            s.page_width = s.setting_page_width;
            s.page_height = s.setting_page_height;
            delete s.setting_page_width;
            delete s.setting_page_height;
            if (!s.font_size) s.font_size = parseInt($('article').css('font-size')) || 16;
            localStorage.setItem('reader_setting', JSON.stringify(s));
        } catch(e) {}
        if (typeof successFn === 'function') successFn();
        return;
    }
    $.ajax({
        url: url_update_setting,
        type: 'post',
        data: collectSettings(),
        success: successFn || function(data) { console.log(data); }
    });
}

$('.update-setting').click(saveSettings);

// 字体 / 字重 dropdown 选择
function bindSettingDropdown(optionSel, inputSel, labelSel, onChange) {
    $(document).on('click', optionSel, function(e) {
        e.preventDefault();
        var $opt = $(this);
        var val = $opt.attr('data-value') || '';
        var $dd = $opt.closest('.setting-dropdown');
        $dd.find(inputSel).val(val);
        $dd.find(labelSel).text($opt.text().trim());
        $dd.find(optionSel).removeClass('active');
        $opt.addClass('active');
        // 关闭 dropdown：移除选项按钮与触发按钮焦点，使 :focus-within 失效
        $opt.trigger('blur');
        $dd.find('[tabindex]').trigger('blur');
        onChange(val);
    });
}
bindSettingDropdown('.font-setting-option', '.font-setting', '.font-setting-label', function(fontFamily) {
    $('article').css('font-family', fontFamily ? fontFamily + ', sans-serif' : '');
    saveSettings();
});
bindSettingDropdown('.font-weight-option', '#setting-font-weight', '.font-weight-label', function(fontWeight) {
    $('article').css('font-weight', fontWeight || '');
    saveSettings();
});

// 字体颜色开关/取色变化
$('#enable-font-color, #setting-font-color').on('change', function() {
    $('article').css('color', $('#enable-font-color').is(':checked') ? $('#setting-font-color').val() : '');
    saveSettings();
});

// 字间距 / 行高：实时预览 + change 时保存
function bindRangePreview(selector, valSel, applyFn, fmt) {
    $(selector).on('input', function() {
        var val = parseFloat($(this).val());
        $(valSel).text(fmt ? fmt(val) : val);
        applyFn(val);
    });
    $(selector).on('change', saveSettings);
}
bindRangePreview('#setting-letter-spacing', '#setting-letter-spacing-val',
    function(val) { $('article').css('letter-spacing', val + 'px'); });
bindRangePreview('#setting-line-height', '#setting-line-height-val',
    function(val) { $('article').css('line-height', val > 0 ? val : ''); },
    function(val) { return val > 0 ? val.toFixed(1) : '默认'; });

// 页宽 / 页高：input 实时预览（不重算分页，避免拖动卡顿），change 时重算分页并保存
$('#setting-page-width').on('input', function() {
    var v = parseInt($(this).val()) || 0;
    $('#setting-page-width-val').text(pageWidthLabel(v));
    applyPageSize();
});
$('#setting-page-height').on('input', function() {
    var v = parseInt($(this).val()) || 0;
    $('#setting-page-height-val').text(pageHeightLabel(v));
    applyPageSize();
});
$('#setting-page-width, #setting-page-height').on('change', function() {
    var off = currentWordsRead();
    applyPageSize();
    if (read_mode !== 'slide') { reinitPages(); goToPageByOffset(off); }
    saveSettings();
});

// 窗口缩放：重测 page-nav 高度并重算阅读区尺寸（翻页模式按进度恢复页位）
var pageSizeResizeTimer = null;
$(window).on('resize', function() {
    if (pageSizeResizeTimer) clearTimeout(pageSizeResizeTimer);
    pageSizeResizeTimer = setTimeout(function() {
        var prevBuf = pageNavBuffer;
        measurePageNavHeight();
        if (pageNavBuffer === prevBuf) return;
        var off = currentWordsRead();
        applyPageSize();
        if (read_mode !== 'slide') { reinitPages(); goToPageByOffset(off); }
    }, 200);
});

// 阅读模式切换：先保存模式设置，再保存当前进度，最后按目标模式恢复
$('.mode-setting').click(function() {
    var newMode = $(this).data('mode');
    if (newMode === read_mode) return;
    // 在切换 read_mode 之前捕获当前进度（避免 save_record 按新模式的统计逻辑读取到错值）
    var slideOffset = read_mode === 'slide' ? getSlideOffset() : page_contents_len[current_page_idx] ;
    
    read_mode = newMode;
    saveSettings(function() {
        if (is_anon_reader) { applyAfterModeSwitch(newMode, slideOffset); return; }
        // 用捕获的偏移直接上报进度，再按目标模式恢复
        $.ajax({
            url: url_book_reader,
            type: 'post',
            data: {
                book_id: book_id,
                chapter_id: chapter_id,
                words: slideOffset,
                csrfmiddlewaretoken: csrf_token
            },
            success: function(data) {
                if (typeof data === 'object' && data.success) {
                    updateProgressBar(data.progress, data.words_read, data.total_words);
                }
                applyAfterModeSwitch(newMode, slideOffset);
            },
            error: function() { applyAfterModeSwitch(newMode, slideOffset); }
        });
    });
});

// 模式切换后按目标模式恢复阅读位置
function applyAfterModeSwitch(newMode, slideOffset) {
    if (newMode === 'page') {
        // slide→page：复用已缓存的当前章节视图，原地切回翻页布局
        applyReadMode();
        updateModeButtons();
        var cached = chapterCache.get(chapter_id);
        if (cached) {
            $('.article-container').html(cached.chapter_view);
            applyTypographyToArticle();
            reinitPages();
            highlightChapter(chapter_id);
            goToPageByOffset(slideOffset);
            setTimeout(function() {
                preloadAround(chapter_id, PRELOAD_RANGE).then(function() { pruneCache(chapter_id, PRELOAD_RANGE); });
            }, 1000);
            return;
        }
        // 当前章节未缓存：退回整页刷新
        location.reload();
    } else {
        // page→slide：用当前页起始偏移恢复滑动位置
        applyReadMode();
        updateModeButtons();
        initSlideMode();
        restoreSlideOffset(slideOffset);
        ensureSlideAppend();
        ensureSlidePrepend();
    }
}

// ===== 书签保存 =====
$('.bookmark-btn').click(function() {
    var cont = '';
    if (read_mode === 'slide') {
        var containerTop = $('.article-container').offset().top;
        var containerBottom = containerTop + $('.article-container').height();
        $('article p').each(function(i, e) {
            var top = $(e).offset().top;
            if (top >= containerTop && top < containerBottom) cont += $(e).text();
        });
    } else {
        $('article p').each(function(i, e) {
            var left = parseInt($(e).offset().left);
            if (left > 0 && left < page_width) cont += $(e).text();
        });
    }
    if (cont.length > 200) cont = cont.substring(0, 200) + '…';

    $.ajax({
        url: url_bookmark_save,
        type: 'post',
        data: {
            book_id: book_id,
            chapter_id: chapter_id,
            chapter_title: chapter_title,
            words_read: currentWordsRead(),
            content: cont,
            csrfmiddlewaretoken: csrf_token
        },
        success: function(data) {
            if (data !== 'ok') { console.warn('bookmark save failed:', data); return; }
            // 侧栏已打开且书签 tab 激活：刷新书签列表让新书签立即可见
            if (drawerCheckbox && drawerCheckbox.checked && !$('#tab-bookmarks').hasClass('hidden')) {
                refreshBookmarkList();
            }
        }
    });
});

// ===== 书签列表刷新 =====
function refreshBookmarkList() {
    if (!url_bookmark_list) { $('.bookmark_list_container').empty(); return; }
    $.ajax({
        url: url_bookmark_list,
        type: 'get',
        cache: false,
        success: function(data) {
            var $cont = $('.bookmark_list_container').html(data);
            // 高亮当前章节的书签
            $cont.find('.list-group-item').each(function() {
                var bmChapterId = $(this).closest('form').find('input[name="chapter_id"]').val();
                if (parseInt(bmChapterId) === chapter_id) {
                    $(this).addClass('active bg-base-300 font-medium').removeClass('text-base-content/70');
                }
            });
            // 书签 tab 激活且有搜索词：重新过滤
            if (!$('#tab-bookmarks').hasClass('hidden')) $('#sidebar-search').trigger('input');
            // 列表更新后重置滚动位置到顶部
            var scrollEl = document.getElementById('chapter-scroll-container');
            if (scrollEl) scrollEl.scrollTop = 0;
        }
    });
}

// ===== Drawer 监听 =====
$(drawerCheckbox).on('change', function() {
    if (this.checked) { loadChapterList(); refreshBookmarkList(); }
});

$('.bookmark-show').click(function() {
    $('[data-tab]').removeClass('tab-active');
    $('.bookmark-show').addClass('tab-active');
    $('#tab-chapters').addClass('hidden');
    $('#tab-bookmarks').removeClass('hidden');
    refreshBookmarkList();
});

// ===== 侧栏搜索过滤（仅筛选当前激活的 tab） =====
$('#sidebar-search').on('input', function() {
    var keyword = $(this).val().trim().toLowerCase();
    var bookmarkActive = $('#tab-chapters').hasClass('hidden');
    var $items = bookmarkActive
        ? $('.bookmark_list_container .list-group-item')
        : $('.chapter_list_container .list-group-item');
    $items.each(function() {
        var text = $(this).text().toLowerCase();
        var $wrap = bookmarkActive ? $(this).closest('form') : $(this).closest('div');
        $wrap.toggle(!keyword || text.indexOf(keyword) !== -1);
    });
    // 目录激活且搜索框清空时：跳回当前激活章节位置
    if (!bookmarkActive && !keyword) scrollToActiveChapter();
});

// 切换 tab 时按当前搜索词重新过滤
$('.chapter-list-show, .bookmark-show').on('click', function() { $('#sidebar-search').trigger('input'); });

// ===== 章节列表 =====
var chapterListLoaded = false;
var chapterListCacheVersion = 'v8';
var chapterListCacheKey = 'chapterList_' + book_id + '_' + chapterListCacheVersion;

function loadChapterList() {
    if (chapterListLoaded) return;
    chapterListLoaded = true;

    // 尝试从 sessionStorage 取缓存，校验 chapter_ids 一致性
    var cached = sessionStorage.getItem(chapterListCacheKey);
    if (cached) {
        try {
            var cacheData = JSON.parse(cached);
            var cachedIds = cacheData.chapter_ids || [];
            var same = cachedIds.length === chapter_ids.length &&
                       cachedIds.every(function(id, i) { return id === chapter_ids[i]; });
            if (same) {
                $('.chapter_list_container').html(cacheData.html);
                highlightChapterInContainer('.chapter_list_container', chapter_id);
                scrollToActiveChapter();
                return;
            }
            sessionStorage.removeItem(chapterListCacheKey);
        } catch (e) {
            sessionStorage.removeItem(chapterListCacheKey);
        }
    }

    // 无缓存：请求服务器
    $.ajax({
        url: url_chapter_list_ajax + '?chapter_id=' + chapter_id,
        type: 'get',
        cache: false,
        success: function(data) {
            if (!data.success) return;
            $('.chapter_list_container').html(data.html);
            if (data.chapter_ids && data.chapter_ids.length > 0) chapter_ids = data.chapter_ids;
            try {
                sessionStorage.setItem(chapterListCacheKey, JSON.stringify({
                    html: data.html,
                    chapter_ids: data.chapter_ids || []
                }));
            } catch (e) {
                console.warn('目录缓存写入失败:', e);
            }
            scrollToActiveChapter();
        }
    });
}

// 仅在指定容器内高亮当前章节
function highlightChapterInContainer(containerSelector, id) {
    $(containerSelector + ' .list-group-item')
        .removeClass(CHAPTER_ACTIVE_CLASSES).addClass('text-base-content');
    $(containerSelector + ' .list-group-item[data-chapter-id="' + id + '"]')
        .addClass(CHAPTER_ACTIVE_CLASSES).removeClass('text-base-content');
}

function scrollToActiveChapter() {
    var drawerSide = document.getElementById('drawer-side');
    function doScroll() {
        var act = document.querySelector('#chapter-scroll-container .list-group-item.active');
        if (act) act.scrollIntoView({ block: 'center' });
    }
    // drawer 过渡动画结束后再滚动
    if (drawerSide) {
        var handler = function() {
            drawerSide.removeEventListener('transitionend', handler);
            setTimeout(doScroll, 50);
        };
        drawerSide.addEventListener('transitionend', handler);
    }
    // 兜底：500ms 后强制滚动（防止 transitionend 未触发）
    setTimeout(doScroll, 500);
}

$('.chapter-list-show').click(function() {
    $('[data-tab]').removeClass('tab-active');
    $('.chapter-list-show').addClass('tab-active');
    $('#tab-bookmarks').addClass('hidden');
    $('#tab-chapters').removeClass('hidden');
    loadChapterList();
    scrollToActiveChapter();
});

$('.chapter_list_btn').click(scrollToActiveChapter);

// ===== 章节缓存：预加载 / 清理 / 内联加载 =====
async function preloadChapter(chapterId) {
    if (chapterCache.has(chapterId)) return;
    try {
        const resp = await fetch(getChapterUrl(chapterId));
        if (!resp.ok) { console.warn('预加载章节失败:', chapterId, 'HTTP', resp.status); return; }
        const contentType = resp.headers.get('content-type') || '';
        if (!contentType.includes('application/json')) { console.warn('预加载章节失败:', chapterId, '非JSON响应'); return; }
        const data = await resp.json();
        if (data.success) {
            chapterCache.set(chapterId, {
                chapter_view: data.chapter_view,
                title: data.title,
                book_id: data.book_id
            });
        }
    } catch (e) {
        console.warn('预加载章节失败:', chapterId, e);
    }
}

async function preloadAround(currentId, range) {
    const idx = chapter_ids.indexOf(currentId);
    if (idx === -1) return;
    const start = Math.max(0, idx - range);
    const end = Math.min(chapter_ids.length - 1, idx + range);
    for (let i = start; i <= end; i++) {
        if (chapter_ids[i] !== currentId && !chapterCache.has(chapter_ids[i])) {
            await preloadChapter(chapter_ids[i]);
            await new Promise(function(resolve) { setTimeout(resolve, 150); });
        }
    }
}

function pruneCache(currentId, range) {
    const idx = chapter_ids.indexOf(currentId);
    if (idx === -1) return;
    const keepStart = Math.max(0, idx - range);
    const keepEnd = Math.min(chapter_ids.length - 1, idx + range);
    const keepSet = new Set();
    for (let i = keepStart; i <= keepEnd; i++) keepSet.add(chapter_ids[i]);
    for (const key of chapterCache.keys()) {
        if (!keepSet.has(key)) chapterCache.delete(key);
    }
}

function loadChapterFromCache(chapterId, offset) {
    const cached = chapterCache.get(chapterId);
    if (!cached) return false;

    chapter_id = chapterId;
    chapter_title = cached.title || chapter_title;
    updateChapterDisplay();
    highlightChapter(chapterId);

    var restorePrevChapter = localStorage.getItem('prev-chapter');
    var useOffset = !restorePrevChapter && typeof offset === 'number' && offset >= 0;

    if (read_mode === 'slide') {
        $('.article-container').empty();
        slideLoadedChapters = new Set();
        var $art = $(cached.chapter_view);
        markSlideArticle($art, chapterId);
        $('.article-container').append($art);
        slideLoadedChapters.add(chapterId);
        applyTypographyToArticle($art);

        if (restorePrevChapter) {
            $('.article-container').scrollTop($('.article-container')[0].scrollHeight);
            localStorage.removeItem('prev-chapter');
        } else if (useOffset && offset > 0) {
            restoreSlideOffset(offset);
        } else {
            $('.article-container').scrollTop(0);
        }
    } else {
        $('.article-container').html(cached.chapter_view);
        applyTypographyToArticle();
        reinitPages();
        if (restorePrevChapter) {
            restoreLastPosition();
            localStorage.removeItem('prev-chapter');
        } else if (useOffset) {
            goToPageByOffset(offset);
        }
    }

    save_record();
    if (read_mode === 'slide') ensureSlideAppend();

    setTimeout(function() {
        preloadAround(chapterId, PRELOAD_RANGE).then(function() { pruneCache(chapterId, PRELOAD_RANGE); });
    }, 1000);
    scrollToActiveChapter();
    return true;
}

// 章节列表 / 书签列表表单：原地跳转
$('.chapter_list_container').on('submit', 'form', function(e) {
    e.preventDefault();
    var targetId = parseInt($(this).find('button.list-group-item').attr('data-chapter-id'));
    if (targetId === chapter_id) return;
    if (chapterCache.has(targetId)) loadChapterFromCache(targetId);
    else preloadChapter(targetId).then(function() { loadChapterFromCache(targetId); });
});

$('.bookmark_list_container').on('submit', 'form', function(e) {
    e.preventDefault();
    var targetId = parseInt($(this).find('input[name="chapter_id"]').val());
    var offset = parseInt($(this).find('input[name="words_read"]').val()) || 0;
    if (targetId === chapter_id) { goToPageByOffset(offset); return; }
    if (chapterCache.has(targetId)) loadChapterFromCache(targetId, offset);
    else preloadChapter(targetId).then(function() { loadChapterFromCache(targetId, offset); });
});

// 搜索结果：异步加载目标章节并跳转到匹配偏移，随后高亮关键词
$('.search-res').on('submit', 'form', function(e) {
    e.preventDefault();
    var targetId = parseInt($(this).find('input[name="chapter_id"]').val());
    var offset = parseInt($(this).find('input[name="words_read"]').val()) || 0;
    if (searchModal && typeof searchModal.close === 'function') searchModal.close();
    var doHighlight = function() { if (searchKwd) highlightSearchTerm(searchKwd); };
    if (targetId === chapter_id) { goToPageByOffset(offset); doHighlight(); return; }
    if (chapterCache.has(targetId)) {
        if (loadChapterFromCache(targetId, offset)) doHighlight();
    } else {
        preloadChapter(targetId).then(function() {
            if (loadChapterFromCache(targetId, offset)) doHighlight();
        });
    }
});

function closeDrawer() {
    if (drawerCheckbox && drawerCheckbox.checked) {
        drawerCheckbox.checked = false;
        $(drawerCheckbox).trigger('change');
    }
}

// ===== 启动预加载 / 离开页面进度上报 =====
setTimeout(function() {
    preloadAround(chapter_id, PRELOAD_RANGE).then(function() { pruneCache(chapter_id, PRELOAD_RANGE); });
}, 2000);

window.addEventListener('beforeunload', function() {
    var data = new URLSearchParams();
    data.append('book_id', book_id);
    data.append('chapter_id', chapter_id);
    data.append('words', currentWordsRead());
    data.append('csrfmiddlewaretoken', csrf_token);
    if (navigator.sendBeacon) {
        navigator.sendBeacon(url_book_reader, data);
    } else {
        try {
            $.ajax({ url: url_book_reader, type: 'post', data: data.toString(), async: false, contentType: 'application/x-www-form-urlencoded' });
        } catch (e) {}
    }
});

// ===== 自动阅读 UI 事件绑定 =====
$('#auto-read-toggle').change(function() {
    autoReadEnabled = this.checked;
    localStorage.setItem('auto_read_enabled', autoReadEnabled ? 'true' : 'false');
    autoReadEnabled ? startAutoRead() : stopAutoRead();
});

$('#auto-read-speed').on('input', function() {
    autoReadSpeed = sliderToSpeed(parseInt(this.value));
    localStorage.setItem('auto_read_speed', autoReadSpeed);
    $('#auto-read-speed-val').text(autoReadSpeed);
});

