// Get references to DaisyUI/dialog elements
var drawerCheckbox = document.getElementById('drawer-left');
var searchModal = document.querySelector('.myModal');
var settingModal = document.querySelector('.setting_modal');
var addFont = 0;

var read_mode = user_setting_mode || 'page';

var page_width = $('article').width() + parseInt($('article').css('column-gap'));
var page_num = parseInt(($('#marker').offset().left - $('article').offset().left)/ page_width +1);
var page_contents_len = new Array(page_num + 1 ).fill(0);

function applyTypographyToArticle($el) {
    var $target = $el || $('article');
    $target.css('font-size', $('.font-value').text() + 'px');
    var fontFamily = $('.font-setting').val();
    $target.css('font-family', fontFamily ? fontFamily + ', sans-serif' : '');
    if ($('#enable-font-color').is(':checked')) {
        $target.css('color', $('#setting-font-color').val());
    } else {
        $target.css('color', '');
    }
    var fontWeight = $('#setting-font-weight').val();
    $target.css('font-weight', fontWeight || '');
    $target.css('letter-spacing', $('#setting-letter-spacing').val() + 'px');
    var lineHeight = parseFloat($('#setting-line-height').val());
    $target.css('line-height', lineHeight > 0 ? lineHeight : '');
}

// ===== 章节缓存系统 =====
const chapterCache = new Map();
const PRELOAD_RANGE = 10;

function getChapterUrl(chapterId) {
    return url_chapter_content.replace('/0/', '/' + chapterId + '/');
}

$('.chapter_list_btn').click(function(e){
    scrollToActiveChapter();
});

document.onkeydown=function(e){
    var keyNum=window.event ? e.keyCode :e.which;
    if(read_mode === 'slide') return;
    if(keyNum==37){
        $('.prev-page').click();
    }
    if(keyNum==38){
        $('.prev-chapter')[0].click();
    }
    if(keyNum==39 || keyNum == 32){
        $('.next-page').click();
    }
    if(keyNum==40){
        $('.next-chapter')[0].click();
    }
}

var current_page_idx = 0;

// ===== 自动阅读 =====
var autoReadEnabled = localStorage.getItem('auto_read_enabled') === 'true';
var autoReadSpeed = parseFloat(localStorage.getItem('auto_read_speed')) || 2;
var autoReadActive = false;
var autoReadRAF = null;
var autoReadLastScrollTime = 0;
var autoReadUserPaused = false;
var autoReadResumeTimer = null;
var prevReadMode = null;

function autoReadLoop() {
    if (!autoReadActive) return;
    var c = $('.article-container')[0];
    if (!c) return;
    autoReadLastScrollTime = Date.now();
    c.scrollTop += autoReadSpeed;
    ensureSlideAppend();
    if (c.scrollTop + c.clientHeight >= c.scrollHeight - 1) {
        var $arts = $('.article-container article[data-chapter-id]');
        if ($arts.length > 0) {
            var lastId = parseInt($arts.last().attr('data-chapter-id'));
            var idx = chapter_ids.indexOf(lastId);
            if (idx === -1 || idx >= chapter_ids.length - 1) {
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
        $('.article-container').scrollTop(0);
        ensureSlideAppend();
    } else {
        prevReadMode = null;
    }
    autoReadLastScrollTime = 0;
    autoReadActive = true;
    autoReadUserPaused = false;
    autoReadRAF = requestAnimationFrame(autoReadLoop);
}

function stopAutoRead() {
    if (autoReadRAF) {
        cancelAnimationFrame(autoReadRAF);
        autoReadRAF = null;
    }
    autoReadActive = false;
    autoReadUserPaused = false;
    if (autoReadResumeTimer) {
        clearTimeout(autoReadResumeTimer);
        autoReadResumeTimer = null;
    }
    if (prevReadMode === 'page') {
        prevReadMode = null;
        save_record(function() {
            location.reload();
        });
        return;
    }
    prevReadMode = null;
}

function pauseAutoReadByUser() {
    if (!autoReadActive || autoReadUserPaused) return;
    cancelAnimationFrame(autoReadRAF);
    autoReadRAF = null;
    autoReadUserPaused = true;
    if (autoReadResumeTimer) clearTimeout(autoReadResumeTimer);
    autoReadResumeTimer = setTimeout(function() {
        autoReadUserPaused = false;
        autoReadResumeTimer = null;
        if (autoReadActive) {
            autoReadRAF = requestAnimationFrame(autoReadLoop);
        }
    }, 1000);
}

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

function getCurrentSlideArticle() {
    var containerTop = $('.article-container').offset().top;
    var current = null;
    $('.article-container article[data-chapter-id]').each(function() {
        if ($(this).offset().top <= containerTop + 5) {
            current = this;
        }
    });
    return current;
}

var slideLoadedChapters = new Set();

function markSlideArticle($art, chapterId) {
    $art.attr('data-chapter-id', chapterId);
    $art.find('#marker').remove();
}

function appendSlideChapter(chapterId) {
    if (slideLoadedChapters.has(chapterId)) return false;
    if (!chapterCache.has(chapterId)) return false;
    var $art = $(chapterCache.get(chapterId).chapter_view);
    markSlideArticle($art, chapterId);
    $('.article-container').append($art);
    slideLoadedChapters.add(chapterId);
    applyTypographyToArticle($art);
    return true;
}

function prependSlideChapter(chapterId) {
    if (slideLoadedChapters.has(chapterId)) return false;
    if (!chapterCache.has(chapterId)) return false;
    var $art = $(chapterCache.get(chapterId).chapter_view);
    markSlideArticle($art, chapterId);
    var c = $('.article-container')[0];
    var prevScrollHeight = c.scrollHeight;
    $('.article-container').prepend($art);
    slideLoadedChapters.add(chapterId);
    applyTypographyToArticle($art);
    // 保持视口位置：补偿新内容插入导致的高度增量
    c.scrollTop += c.scrollHeight - prevScrollHeight;
    return true;
}

function ensureSlideAppend() {
    var c = $('.article-container')[0];
    if (!c) return;
    while (c.scrollTop + c.clientHeight >= c.scrollHeight - 300) {
        var $arts = $('.article-container article[data-chapter-id]');
        if ($arts.length === 0) break;
        var lastId = parseInt($arts.last().attr('data-chapter-id'));
        var idx = chapter_ids.indexOf(lastId);
        if (idx === -1 || idx >= chapter_ids.length - 1) break;
        var nextId = chapter_ids[idx + 1];
        if (slideLoadedChapters.has(nextId)) break;
        if (chapterCache.has(nextId)) {
            appendSlideChapter(nextId);
        } else {
            preloadChapter(nextId).then(function() { ensureSlideAppend(); });
            break;
        }
    }
}

function ensureSlidePrepend() {
    var c = $('.article-container')[0];
    if (!c) return;
    while (c.scrollTop < 300) {
        var $arts = $('.article-container article[data-chapter-id]');
        if ($arts.length === 0) break;
        var firstId = parseInt($arts.first().attr('data-chapter-id'));
        var idx = chapter_ids.indexOf(firstId);
        if (idx <= 0) break;
        var prevId = chapter_ids[idx - 1];
        if (slideLoadedChapters.has(prevId)) break;
        if (chapterCache.has(prevId)) {
            prependSlideChapter(prevId);
        } else {
            preloadChapter(prevId).then(function() { ensureSlidePrepend(); });
            break;
        }
    }
}

function initSlideMode() {
    $('article').css('transform', 'translateX(0px)');
    slideLoadedChapters = new Set();
    var $art = $('article').first();
    markSlideArticle($art, chapter_id);
    slideLoadedChapters.add(chapter_id);
}

function restoreSlideOffset(offset) {
    var accumulated = 0;
    var target = null;
    $('.article-container article').first().find('p').each(function() {
        if (accumulated + $(this).text().length >= offset) {
            target = this;
            return false;
        }
        accumulated += $(this).text().length;
    });
    if (target) {
        var container = $('.article-container');
        var top = $(target).offset().top - container.offset().top + container.scrollTop();
        container.scrollTop(top);
    } else {
        $('.article-container').scrollTop(0);
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

    $('article p').each((i, e) => {
        page_contents_len[parseInt($(e).offset().left / page_width) + 1] += $(e).text().length;
    })
    for (var i = 1; i < page_num + 1; i++)
        page_contents_len[i] += page_contents_len[i - 1];

    current_page_idx = 0;
    renderPageButtons(current_page_idx);

    $('article').css('transform', 'translateX(0px)');
    last_words = 0;
}

function renderPageButtons(activeIdx) {
    $('.pages-container').empty();
    if (page_num <= 0) return;

    // 动态计算可显示按钮数：上一页与下一页之间的可用宽度 / 单个按钮占位宽度
    var $probe = $('<button class="join-item btn btn-outline btn-sm page-num page-item" style="visibility:hidden;">1</button>' +
                   '<button class="join-item btn btn-outline btn-sm page-num page-item" style="visibility:hidden;">2</button>');
    $('.pages-container').append($probe);
    var btnSlot = $probe.eq(1).offset().left - $probe.eq(0).offset().left;
    $probe.remove();
    if (!btnSlot || btnSlot < 1) btnSlot = 32;

    var prevRight = $('.prev-page').offset().left + $('.prev-page').outerWidth(true);
    var nextLeft = $('.next-page').offset().left;
    var availableWidth = nextLeft - prevRight;
    var maxVisible = Math.max(5, Math.floor(availableWidth / btnSlot));

    var pages = [];

    if (page_num <= maxVisible) {
        for (var i = 1; i <= page_num; i++) pages.push({num: i, type: 'page'});
    } else {
        var range = 2;
        var leftBound = Math.max(2, activeIdx + 1 - range);
        var rightBound = Math.min(page_num - 1, activeIdx + 1 + range);
        if (leftBound <= 2) { leftBound = 2; rightBound = Math.min(page_num - 1, 2 + range * 2); }
        if (rightBound >= page_num - 1) { rightBound = page_num - 1; leftBound = Math.max(2, page_num - 1 - range * 2); }

        pages.push({num: 1, type: 'page'});
        if (leftBound > 2) pages.push({num: '...', type: 'ellipsis'});
        for (var i = leftBound; i <= rightBound; i++) pages.push({num: i, type: 'page'});
        if (rightBound < page_num - 1) pages.push({num: '...', type: 'ellipsis'});
        pages.push({num: page_num, type: 'page'});
    }

    for (var i = 0; i < pages.length; i++) {
        var p = pages[i];
        if (p.type === 'ellipsis') {
            $('.pages-container').append($('<span class="join-item btn btn-sm" style="border:0;background:transparent;color:inherit;">…</span>'));
        } else {
            var btn = $('<button class="join-item btn btn-outline btn-sm page-num page-item">' + p.num + '</button>');
            if (p.num - 1 === activeIdx) {
                btn.addClass('btn-active active').css('border','1px solid currentColor');
            }
            $('.pages-container').append(btn);
        }
    }
}

function goToPage(idx) {
    if (read_mode === 'slide') return;
    if (idx < 0 || idx >= page_num) return;
    current_page_idx = idx;
    renderPageButtons(idx);
    $('article').css('transform', `translateX(-${page_width * idx}px)`);
}

function goToPageByOffset(offset) {
    if (read_mode === 'slide') {
        restoreSlideOffset(offset);
        return true;
    }
    for (var i = 0; i < page_num + 1; i++) {
        if (page_contents_len[i] > offset) {
            goToPage(i - 1);
            return true;
        }
    }
    return false;
}

applyReadMode();
updateModeButtons();
var initial_last_words = last_words;
if (read_mode === 'slide') {
    initSlideMode();
}
reinitPages();

function navigateToChapter(targetId) {
    if (targetId === chapter_id) return;
    if (chapterCache.has(targetId)) {
        loadChapterFromCache(targetId);
    } else {
        var form = $('<form method="POST" style="display:none;">')
            .attr('action', url_book_reader)
            .append($('<input>').attr({type: 'hidden', name: 'csrfmiddlewaretoken', value: csrf_token}))
            .append($('<input>').attr({type: 'hidden', name: 'book_id', value: book_id}))
            .append($('<input>').attr({type: 'hidden', name: 'chapter_id', value: targetId}));
        $('body').append(form);
        form.submit();
    }
}

function scrollToSlideChapter(chapterId) {
    var c = $('.article-container')[0];
    if (!c) return;
    var $art = $('.article-container article[data-chapter-id="' + chapterId + '"]');
    if ($art.length === 0) return;
    var top = $art.offset().top - $('.article-container').offset().top + c.scrollTop;
    c.scrollTop = top;
}

function jumpToSlideChapter(targetId) {
    if (slideLoadedChapters.has(targetId)) {
        scrollToSlideChapter(targetId);
        return;
    }
    var curIdx = chapter_ids.indexOf(chapter_id);
    var targetIdx = chapter_ids.indexOf(targetId);
    if (curIdx === -1 || targetIdx === -1) {
        navigateToChapter(targetId);
        return;
    }

    if (targetIdx > curIdx) {
        // 向下：依次 append 中间章节直到目标
        var pending = [];
        for (var i = curIdx + 1; i <= targetIdx; i++) {
            if (!slideLoadedChapters.has(chapter_ids[i])) pending.push(chapter_ids[i]);
        }
        var loadAndScroll = function() {
            while (pending.length > 0 && chapterCache.has(pending[0])) {
                appendSlideChapter(pending.shift());
            }
            if (pending.length === 0) {
                scrollToSlideChapter(targetId);
            } else {
                var next = pending[0];
                preloadChapter(next).then(loadAndScroll);
            }
        };
        loadAndScroll();
    } else {
        // 向上：依次 prepend 中间章节直到目标
        var pendingUp = [];
        for (var j = curIdx - 1; j >= targetIdx; j--) {
            if (!slideLoadedChapters.has(chapter_ids[j])) pendingUp.push(chapter_ids[j]);
        }
        var loadAndScrollUp = function() {
            while (pendingUp.length > 0 && chapterCache.has(pendingUp[0])) {
                prependSlideChapter(pendingUp.shift());
            }
            if (pendingUp.length === 0) {
                scrollToSlideChapter(targetId);
            } else {
                var next = pendingUp[0];
                preloadChapter(next).then(loadAndScrollUp);
            }
        };
        loadAndScrollUp();
    }
}

$('.prev-chapter').click(function(e){
    e.preventDefault();
    if (typeof chapter_ids === 'undefined') return;
    var idx = chapter_ids.indexOf(chapter_id);
    if (idx <= 0) return;
    var targetId = chapter_ids[idx - 1];
    if (read_mode === 'slide') {
        jumpToSlideChapter(targetId);
    } else {
        navigateToChapter(targetId);
    }
})

$('.next-chapter').click(function(e){
    e.preventDefault();
    if (typeof chapter_ids === 'undefined') return;
    var idx = chapter_ids.indexOf(chapter_id);
    if (idx === -1 || idx >= chapter_ids.length - 1) return;
    var targetId = chapter_ids[idx + 1];
    if (read_mode === 'slide') {
        jumpToSlideChapter(targetId);
    } else {
        navigateToChapter(targetId);
    }
})

$('.page-nav').on('click', '.page-item', function(e){
    if ($(this).hasClass('prev-chapter') || $(this).hasClass('next-chapter')) return;
    if ($(this).hasClass('prev-page')) {
        if (current_page_idx > 0) {
            goToPage(current_page_idx - 1);
            save_record();
        } else {
            localStorage.setItem('prev-chapter','true');
            $('.prev-chapter')[0].click();
        }
    } else if ($(this).hasClass('next-page')) {
        if (current_page_idx < page_num - 1) {
            goToPage(current_page_idx + 1);
            save_record();
        } else {
            $('.next-chapter')[0].click();
        }
    } else if ($(this).hasClass('page-num')) {
        var idx = parseInt($(this).text()) - 1;
        if (idx === current_page_idx) return;
        goToPage(idx);
        save_record();
    }
})

function save_record(callback) {
    var words = (read_mode === 'slide') ? getSlideOffset() : (page_contents_len[current_page_idx] || 0);
    $.ajax({
     url: url_book_reader,
     type: 'post',
     data: {
         'book_id': book_id,
         'chapter_id': chapter_id,
         'words': words,
         csrfmiddlewaretoken: csrf_token
     },
     success: function (data){
     console.log(data);
     if (callback) callback();
     },
     error: function() {
     if (callback) callback();
     }
 });
}

// 滑动模式：滚动时自动保存进度（防抖）、检测当前章节并追加下一章
var slideSaveTimer = null;
$('.article-container').on('scroll', function() {
    if (read_mode !== 'slide') return;
    if (autoReadActive && Date.now() - autoReadLastScrollTime > 150) {
        pauseAutoReadByUser();
    }
    var cur = getCurrentSlideArticle();
    if (cur) {
        var newChapterId = parseInt($(cur).attr('data-chapter-id'));
        if (newChapterId !== chapter_id) {
            chapter_id = newChapterId;
            $('.list-group-item').removeClass('active bg-base-300 font-medium').addClass('text-base-content/70');
            $('.list-group-item[data-chapter-id="' + chapter_id + '"]').addClass('active bg-base-300 font-medium').removeClass('text-base-content/70');
        }
    }
    ensureSlideAppend();
    ensureSlidePrepend();
    if (slideSaveTimer) clearTimeout(slideSaveTimer);
    slideSaveTimer = setTimeout(function() { save_record(); }, 500);
});

// 恢复上一章翻页标记：跳转到最后一页
if(localStorage.getItem('prev-chapter')) {
    restoreLastPosition();
    localStorage.removeItem('prev-chapter');
} else {
    // 恢复阅读进度：跳转到 last_words 对应的页码
    goToPageByOffset(initial_last_words);
}

// 滑动模式：初始追加下一章
if (read_mode === 'slide') {
    ensureSlideAppend();
}


$('.content-serarch').on("search", function() {
    var kwd = $(this).val();
    if (!kwd) return;
    $.ajax({
     url: url_book_reader,
     type: 'post',
     data: {
         'book_id': book_id,
         'chapter_id': chapter_id,
         'kwd': kwd,
         csrfmiddlewaretoken: csrf_token
     },
     success: function (data){
        $('.search-res').html(data);
        $('.modal .content-serarch').val(kwd);
        if($('.search-res .list-group-item.active')[0])
            $('.search-res .list-group-item.active')[0].scrollIntoView({ block: 'nearest' });
     }
    });

    // Show search modal via dialog API
    if (searchModal && typeof searchModal.showModal === 'function') {
        searchModal.showModal();
    }
});

$('.search-btn').click(function(){
    if (searchModal && typeof searchModal.showModal === 'function') {
        searchModal.showModal();
    }
});

// Settings toast show/hide
function showSettingsToast() {
    var toast = $('#offcanvassetting');
    $('.font-value').text(parseInt($('article').css('font-size')));

    var had_choose = false;
    $('.bg-setting').each(function(){
        if($(this).hasClass('bodder border-4 border-secondary'))
            had_choose = true;
    });

    $('.bg-setting').each(function(){
        if(!had_choose && $(this).css('background-color')==user_setting_bg)
            $(this).addClass('bodder border-4 border-secondary');
    });

    updateModeButtons();

    $('#auto-read-toggle').prop('checked', autoReadEnabled);
    $('#auto-read-speed').val(autoReadSpeed);
    $('#auto-read-speed-val').text(autoReadSpeed);

    toast.show();
}

$('.setting-btn').click(function(){
    var toast = $('#offcanvassetting');
    if (toast.is(':visible')) {
        toast.hide();
    } else {
        showSettingsToast();
    }
});

$('.setting-close').click(function(){
    $('#offcanvassetting').hide();
});

$('.inc-font').click(function(){
    var font = parseInt($('article').css('font-size'));
    font += 1;
    $('.font-value').text(font);
    $('article').css('font-size',font);
})

$('.dec-font').click(function(){
    var font = parseInt($('article').css('font-size'));
    font -= 1;
    $('.font-value').text(font);
    $('article').css('font-size',font);
})

var bgFontColorMap = {
    'read-white': '',
    'read-blue': '#1f3a5a',
    'read-green': '#1f3a1f',
    'read-yellow': '#3a2a1a',
    'read-black': 'rgb(90, 90, 90)'
};

$('.bg-setting').click(function(){
    $('main').css('background',$(this).css('background'));
    $('.bg-setting').removeClass('bodder border-4 border-secondary');
    $(this).addClass('bodder border-4 border-secondary');
    for (var cls in bgFontColorMap) {
        if ($(this).hasClass(cls)) {
            $('main').css('color', bgFontColorMap[cls]);
            break;
        }
    }
})

function collectSettings() {
    return {
        'font_size': $('.font-value').text(),
        'read_bg': $('main').css('background-color'),
        'read_mode': read_mode,
        'font_family': $('.font-setting').val() || '',
        'font_color': $('#enable-font-color').is(':checked') ? ($('#setting-font-color').val() || '') : '',
        'letter_spacing': $('#setting-letter-spacing').val() || '0',
        'line_height': $('#setting-line-height').val() || '1.2',
        'font_weight': $('#setting-font-weight').val() || '',
        csrfmiddlewaretoken: csrf_token
    };
}

function saveSettings(successFn) {
    $.ajax({
     url: url_update_setting,
     type: 'post',
     data: collectSettings(),
     success: successFn || function (data){ console.log(data); }
    });
}

$('.update-setting').click(function(){
    saveSettings();
})

$('.font-setting').on('change', function(){
    var fontFamily = $(this).val();
    $('article').css('font-family', fontFamily ? fontFamily + ', sans-serif' : '');
    saveSettings();
});

$('#enable-font-color, #setting-font-color').on('change', function(){
    if ($('#enable-font-color').is(':checked')) {
        $('article').css('color', $('#setting-font-color').val());
    } else {
        $('article').css('color', '');
    }
    saveSettings();
});

$('#setting-font-weight').on('change', function(){
    var val = $(this).val();
    $('article').css('font-weight', val || '');
    saveSettings();
});

$('#setting-letter-spacing').on('input', function(){
    var val = $(this).val();
    $('#setting-letter-spacing-val').text(val);
    $('article').css('letter-spacing', val + 'px');
});
$('#setting-letter-spacing').on('change', function(){
    saveSettings();
});

$('#setting-line-height').on('input', function(){
    var val = parseFloat($(this).val());
    $('#setting-line-height-val').text(val > 0 ? val.toFixed(1) : '默认');
    $('article').css('line-height', val > 0 ? val : '');
});
$('#setting-line-height').on('change', function(){
    saveSettings();
});

$('.mode-setting').click(function(){
    var mode = $(this).data('mode');
    if (mode === read_mode) return;
    read_mode = mode;
    $.ajax({
     url: url_update_setting,
     type: 'post',
     data: collectSettings(),
     success: function (data){
         if (read_mode === 'page') {
             location.reload();
         } else {
             applyReadMode();
             updateModeButtons();
             initSlideMode();
             restoreSlideOffset(page_contents_len[current_page_idx]);
             ensureSlideAppend();
         }
     }
    });
});

$('.bookmark-btn').click(function(){
    var cont ='';
    if (read_mode === 'slide') {
        var containerTop = $('.article-container').offset().top;
        var containerBottom = containerTop + $('.article-container').height();
        $('article p').each((i,e)=>{
            var top = $(e).offset().top;
            if(top >= containerTop && top < containerBottom)
                cont+=$(e).text();
        });
    } else {
        $('article p').each((i,e)=>{
            if(parseInt($(e).offset().left) >0  && parseInt($(e).offset().left)<page_width)
                cont+=$(e).text();
        });
    }
    var words = (read_mode === 'slide') ? getSlideOffset() : page_contents_len[current_page_idx];
    $.ajax({
     url: url_bookmark_save,
     type: 'post',
     data: {
         'book_id':book_id,
         'chapter_id':chapter_id,
         'chapter_title':chapter_title,
         'words_read':words,
         'content':cont,
         csrfmiddlewaretoken: csrf_token
     },
     success: function (data){ console.log(data); }
    })
})

// Drawer toggle listener (replaces offcanvas show.bs.offcanvas)
$(drawerCheckbox).on('change', function(e) {
    if (this.checked) {
        loadChapterList();
        if (url_bookmark_list) {
            $.ajax({
                url: url_bookmark_list,
                type: 'get',
                success: function (data){
                    $('.bookmark_list_container').html(data);
                    // 前端高亮当前章节的书签
                    $('.bookmark_list_container .list-group-item').each(function() {
                        var bmChapterId = $(this).closest('form').find('input[name="chapter_id"]').val();
                        if (parseInt(bmChapterId) === chapter_id) {
                            $(this).addClass('active bg-base-300 font-medium').removeClass('text-base-content/70');
                        }
                    });
                }
            });
        } else {
            $('.bookmark_list_container').empty();
        }
    }
});

$('.bookmark-show').click(function(){
    // Switch to bookmark tab
    $('[data-tab]').removeClass('tab-active');
    $('.bookmark-show').addClass('tab-active');
    $('#tab-chapters').addClass('hidden');
    $('#tab-bookmarks').removeClass('hidden');
    $('#drawer-side').scrollTop(0);
});

var chapterListLoaded = false;
var chapterListCacheVersion = 'v5';
var chapterListCacheKey = 'chapterList_' + book_id + '_' + chapterListCacheVersion;

function loadChapterList() {
    if (chapterListLoaded) return;
    chapterListLoaded = true;

    // 尝试从 sessionStorage 读取缓存
    var cached = sessionStorage.getItem(chapterListCacheKey);
    if (cached) {
        try {
            var cacheData = JSON.parse(cached);
            $('.chapter_list_container').html(cacheData.html);
            if (cacheData.chapter_ids && cacheData.chapter_ids.length > 0) {
                chapter_ids = cacheData.chapter_ids;
            }
            // 更新当前章节高亮
            $('.chapter_list_container .list-group-item').removeClass('active bg-base-300 font-medium').addClass('text-base-content/70');
            $('.chapter_list_container .list-group-item[data-chapter-id="' + chapter_id + '"]').addClass('active bg-base-300 font-medium').removeClass('text-base-content/70');
            scrollToActiveChapter();
            return;
        } catch (e) {
            sessionStorage.removeItem(chapterListCacheKey);
        }
    }

    // 无缓存，发起请求
    $.ajax({
        url: url_chapter_list_ajax + '?chapter_id=' + chapter_id,
        type: 'get',
        success: function (data) {
            if (data.success) {
                $('.chapter_list_container').html(data.html);
                if (data.chapter_ids && data.chapter_ids.length > 0) {
                    chapter_ids = data.chapter_ids;
                }
                // 写入 sessionStorage 缓存
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
        }
    });
}

function scrollToActiveChapter() {
    var drawerSide = document.getElementById('drawer-side');

    function doScroll() {
        var act = document.querySelector('#chapter-scroll-container .list-group-item.active');
        if (act) {
            act.scrollIntoView({ block: 'center' });
        }
    }

    // 如果 drawer 正在做过渡动画，等动画结束后再滚动
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

$('.chapter-list-show').click(function(){
    // Switch to chapter list tab
    $('[data-tab]').removeClass('tab-active');
    $('.chapter-list-show').addClass('tab-active');
    $('#tab-bookmarks').addClass('hidden');
    $('#tab-chapters').removeClass('hidden');

    loadChapterList();
    scrollToActiveChapter();
})

// ===== 章节缓存：预加载 & 内联切换 =====

async function preloadChapter(chapterId) {
    if (chapterCache.has(chapterId)) return;
    try {
        const resp = await fetch(getChapterUrl(chapterId));
        if (!resp.ok) {
            console.warn('预加载章节失败:', chapterId, 'HTTP', resp.status);
            return;
        }
        const contentType = resp.headers.get('content-type') || '';
        if (!contentType.includes('application/json')) {
            console.warn('预加载章节失败:', chapterId, '非JSON响应');
            return;
        }
        const data = await resp.json();
        if (data.success) {
            chapterCache.set(chapterId, {
                chapter_view: data.chapter_view,
                title: data.title,
                book_id: data.book_id,
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
            await new Promise(function(resolve) { return setTimeout(resolve, 150); });
        }
    }
}

function pruneCache(currentId, range) {
    const idx = chapter_ids.indexOf(currentId);
    if (idx === -1) return;
    const keepStart = Math.max(0, idx - range);
    const keepEnd = Math.min(chapter_ids.length - 1, idx + range);
    const keepSet = new Set();
    for (let i = keepStart; i <= keepEnd; i++) {
        keepSet.add(chapter_ids[i]);
    }
    for (const key of chapterCache.keys()) {
        if (!keepSet.has(key)) {
            chapterCache.delete(key);
        }
    }
}

function loadChapterFromCache(chapterId, offset) {
    const cached = chapterCache.get(chapterId);
    if (!cached) return false;

    chapter_id = chapterId;
    $('.list-group-item').removeClass('active bg-base-300 font-medium').addClass('text-base-content/70');
    $('.list-group-item[data-chapter-id="' + chapterId + '"]').addClass('active bg-base-300 font-medium').removeClass('text-base-content/70');

    if (read_mode === 'slide') {
        $('.article-container').empty();
        slideLoadedChapters = new Set();
        var $art = $(cached.chapter_view);
        markSlideArticle($art, chapterId);
        $('.article-container').append($art);
        slideLoadedChapters.add(chapterId);
        applyTypographyToArticle($art);

        if (localStorage.getItem('prev-chapter')) {
            var c = $('.article-container');
            c.scrollTop(c[0].scrollHeight);
            localStorage.removeItem('prev-chapter');
        } else if (typeof offset === 'number' && offset >= 0) {
            restoreSlideOffset(offset);
        }
        save_record();
        ensureSlideAppend();
        setTimeout(function() {
            preloadAround(chapterId, PRELOAD_RANGE).then(() => pruneCache(chapterId, PRELOAD_RANGE));
        }, 1000);
        scrollToActiveChapter();
        return true;
    }

    $('.article-container').html(cached.chapter_view);
    applyTypographyToArticle();
    reinitPages();

    if (localStorage.getItem('prev-chapter')) {
        restoreLastPosition();
        localStorage.removeItem('prev-chapter');
    } else if (typeof offset === 'number' && offset >= 0) {
        goToPageByOffset(offset);
    }

    save_record();

    setTimeout(function() {
        preloadAround(chapterId, PRELOAD_RANGE).then(() => pruneCache(chapterId, PRELOAD_RANGE));
    }, 1000);

    scrollToActiveChapter();

    return true;
}

$('.chapter_list_container').on('submit', 'form', function(e) {
    e.preventDefault();
    var targetId = parseInt($(this).find('button.list-group-item').attr('data-chapter-id'));
    if (targetId === chapter_id) return;
    if (chapterCache.has(targetId)) {
        loadChapterFromCache(targetId);
    } else {
        preloadChapter(targetId).then(function() {
            loadChapterFromCache(targetId);
        });
    }
});

$('.bookmark_list_container').on('submit', 'form', function(e) {
    e.preventDefault();
    var targetId = parseInt($(this).find('input[name="chapter_id"]').val());
    var offset = parseInt($(this).find('input[name="words_read"]').val()) || 0;
    if (targetId === chapter_id) {
        goToPageByOffset(offset);
        return;
    }
    if (chapterCache.has(targetId)) {
        loadChapterFromCache(targetId, offset);
    } else {
        preloadChapter(targetId).then(function() {
            loadChapterFromCache(targetId, offset);
        });
    }
});

function closeDrawer() {
    if (drawerCheckbox && drawerCheckbox.checked) {
        drawerCheckbox.checked = false;
        $(drawerCheckbox).trigger('change');
    }
}

setTimeout(function() {
    preloadAround(chapter_id, PRELOAD_RANGE).then(() => pruneCache(chapter_id, PRELOAD_RANGE));
}, 2000);

window.addEventListener('beforeunload', function() {
    var words = (read_mode === 'slide') ? getSlideOffset() : (page_contents_len[current_page_idx] || 0);
    var data = new URLSearchParams();
    data.append('book_id', book_id);
    data.append('chapter_id', chapter_id);
    data.append('words', words);
    data.append('csrfmiddlewaretoken', csrf_token);
    if (navigator.sendBeacon) {
        navigator.sendBeacon(url_book_reader, data);
    } else {
        try { $.ajax({url: url_book_reader, type: 'post', data: data.toString(), async: false, contentType: 'application/x-www-form-urlencoded'}); } catch(e) {}
    }
});

// ===== 自动阅读 UI 事件绑定 =====
$('#auto-read-toggle').change(function() {
    autoReadEnabled = this.checked;
    localStorage.setItem('auto_read_enabled', autoReadEnabled ? 'true' : 'false');
    if (autoReadEnabled) {
        startAutoRead();
    } else {
        stopAutoRead();
    }
});

$('#auto-read-speed').on('input', function() {
    autoReadSpeed = parseFloat(this.value);
    localStorage.setItem('auto_read_speed', autoReadSpeed);
    $('#auto-read-speed-val').text(autoReadSpeed);
});

// ===== 自动阅读初始化 =====
if (autoReadEnabled) {
    setTimeout(function() {
        startAutoRead();
    }, 500);
}
