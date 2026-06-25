// Get references to DaisyUI/dialog elements
var drawerCheckbox = document.getElementById('drawer-left');
var searchModal = document.querySelector('.myModal');
var settingModal = document.querySelector('.setting_modal');
var addFont = 0;

var page_width = $('article').width() + parseInt($('article').css('column-gap'));
var page_num = parseInt(($('#marker').offset().left - $('article').offset().left)/ page_width +1);
var page_contents_len = new Array(page_num + 1 ).fill(0);

// ===== 章节缓存系统 =====
const chapterCache = new Map();
const PRELOAD_RANGE = 10;

function getChapterUrl(chapterId) {
    return url_chapter_content.replace('/0/', '/' + chapterId + '/');
}

$('.chapter_list_btn').click(function(e){
    var act = $('#drawer-side .list-group-item.active');
    if(act[0]) {
        if (!act.attr('offset'))
            act.attr('offset', act.offset().top - act.height()*4);
        $('#drawer-side').scrollTop(act.offset().top - act.height()*4);
    }
});

document.onkeydown=function(e){
    var keyNum=window.event ? e.keyCode :e.which;
    if(keyNum==37){
        $('.prev-page').click();
    }
    if(keyNum==38){
        $('.prev-chapter')[0].click();
    }
    if(keyNum==39 | keyNum == 32){
        $('.next-page').click();
    }
    if(keyNum==40){
        $('.next-chapter')[0].click();
    }
}

function reinitPages() {
    page_width = $('article').width() + parseInt($('article').css('column-gap'));
    page_num = parseInt(($('#marker').offset().left - $('article').offset().left) / page_width + 1);
    page_contents_len = new Array(page_num + 1).fill(0);

    $('article p').each((i, e) => {
        page_contents_len[parseInt($(e).offset().left / page_width) + 1] += $(e).text().length;
    })
    for (var i = 1; i < page_num + 1; i++)
        page_contents_len[i] += page_contents_len[i - 1];

    $('.pages-container').empty();
    if (page_num > 0) {
        for (var i = 0; i < page_num; i++) {
            $('.pages-container').prepend($('<button class="join-item btn btn-sm page-num page-item">' + String(page_num - i) + '</button>'));
        }
        $('.pages-container').children().first().addClass('btn-active active');
    }

    $('article').css('transform', 'translateX(0px)');
    last_words = 0;
}

var initial_last_words = last_words;
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

$('.prev-chapter').click(function(e){
    e.preventDefault();
    if (typeof chapter_ids === 'undefined') return;
    var idx = chapter_ids.indexOf(chapter_id);
    if (idx <= 0) return;
    navigateToChapter(chapter_ids[idx - 1]);
})

$('.next-chapter').click(function(e){
    e.preventDefault();
    if (typeof chapter_ids === 'undefined') return;
    var idx = chapter_ids.indexOf(chapter_id);
    if (idx === -1 || idx >= chapter_ids.length - 1) return;
    navigateToChapter(chapter_ids[idx + 1]);
})

$('.page-item').click(function(e){
    // Don't handle prev-chapter/next-chapter here (they have their own handlers)
    if ($(this).hasClass('prev-chapter') || $(this).hasClass('next-chapter')) return;
    var cur = $('.page-item.active, .page-num.btn-active');
    var cur_idx = parseInt(cur.text()) -1;
    var all = $('.page-num').length;
    if ($(this).hasClass('prev-page')) {
        if(cur_idx>0) {
            cur.prev().addClass('active btn-active');
            cur.removeClass('active btn-active');
            $('article').css('transform',`translateX(-${page_width * (cur_idx - 1 )}px)`);
            save_record();
        } else {
            $('.prev-chapter')[0].click();
            localStorage.setItem('prev-chapter','true');
        }
    } else if ($(this).hasClass('next-page')) {
        if(cur_idx<all-1) {
            cur.next().addClass('active btn-active');
            cur.removeClass('active btn-active');
            $('article').css('transform',`translateX(-${page_width * (cur_idx +1 )}px)`);
            save_record();
        } else {
            $('.next-chapter')[0].click();
        }
    } else if ($(this).hasClass('page-num')) {
        cur.removeClass('active btn-active');
        $(this).addClass('active btn-active');
        cur_idx = parseInt($(this).text()) -1;
        $('article').css('transform',`translateX(-${page_width * cur_idx}px)`);
        save_record();
    }
})

function save_record() {
    $.ajax({
     url: url_book_reader,
     type: 'post',
     data: {
         'book_id': book_id,
         'chapter_id': chapter_id,
         'words': page_contents_len[parseInt($('.page-num.btn-active').first().text()) - 1],
         csrfmiddlewaretoken: csrf_token
     },
     success: function (data){
     console.log(data);
     }
 });
}

// 恢复上一章翻页标记：跳转到最后一页
if(localStorage.getItem('prev-chapter')) {
    $('.pages-container').children().last().click();
    localStorage.removeItem('prev-chapter');
}

// 恢复阅读进度：跳转到 last_words 对应的页码
for(var i=0;i<page_num+1;i++) {
    if(page_contents_len[i]>initial_last_words) {
        $('.page-num').each((idx,e)=>{
            if(parseInt($(e).text()) == i)
                $(e).click();
        })
        break;
    }
}

save_record();

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
        if(!had_choose && $(this).css('background')==user_setting_bg)
            $(this).addClass('bodder border-4 border-secondary');
    });

    toast.show();
    // Auto-hide after 8 seconds
    if (window.settingToastTimer) clearTimeout(window.settingToastTimer);
    window.settingToastTimer = setTimeout(function() { toast.hide(); }, 8000);
}

$('.setting-btn').click(function(){
    var toast = $('#offcanvassetting');
    if (toast.is(':visible')) {
        toast.hide();
    } else {
        showSettingsToast();
    }
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

$('.bg-setting').click(function(){
    $('main').css('background',$(this).css('background'));
    $('.bg-setting').removeClass('bodder border-4 border-secondary');
    $(this).addClass('bodder border-4 border-secondary');
})

$('.update-setting').click(function(){
    var bg = $('main').css('background');
    if($(this).hasClass('bg-setting'))
        bg = $(this).css('background');

    if ($(this).hasClass('read-black'))
        $('main').css('color','rgb(90,90,90)');

    $.ajax({
     url: url_update_setting,
     type: 'post',
     data: {
         'font_size': $('.font-value').text(),
         'read_bg':bg,
         csrfmiddlewaretoken: csrf_token
     },
     success: function (data){ console.log(data); }
    })
})

$('.bookmark-btn').click(function(){
    var cont ='';
    $('article p').each((i,e)=>{
        if(parseInt($(e).offset().left) >0  && parseInt($(e).offset().left)<page_width)
            cont+=$(e).text();
    })
    $.ajax({
     url: url_bookmark_save,
     type: 'post',
     data: {
         'book_id':book_id,
         'chapter_id':chapter_id,
         'chapter_title':chapter_title,
         'words_read':page_contents_len[parseInt($('.page-num.btn-active').first().text()) - 1],
         'content':cont,
         csrfmiddlewaretoken: csrf_token
     },
     success: function (data){ console.log(data); }
    })
})

// Drawer toggle listener (replaces offcanvas show.bs.offcanvas)
$(drawerCheckbox).on('change', function(e) {
    if (this.checked) {
        document.documentElement.style.overflow = 'hidden';
        loadChapterList();
        $.ajax({
            url: url_bookmark_list,
            type: 'get',
            success: function (data){
                $('.bookmark_list_container').html(data);
            }
        });
    } else {
        document.documentElement.style.overflow = '';
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

function loadChapterList() {
    if (chapterListLoaded) return;
    chapterListLoaded = true;
    $.ajax({
        url: url_chapter_list_ajax + '?chapter_id=' + chapter_id,
        type: 'get',
        success: function (data) {
            if (data.success) {
                $('.chapter_list_container').html(data.html);
                if (data.chapter_ids && data.chapter_ids.length > 0) {
                    chapter_ids = data.chapter_ids;
                }
                setTimeout(function () {
                    var act = $('#drawer-side .list-group-item.active');
                    if (act[0]) {
                        var scrollOffset = act.offset().top - act.height() * 4;
                        act.attr('offset', scrollOffset);
                        $('#drawer-side').scrollTop(scrollOffset);
                    }
                }, 100);
            }
        }
    });
}

$('.chapter-list-show').click(function(){
    // Switch to chapter list tab
    $('[data-tab]').removeClass('tab-active');
    $('.chapter-list-show').addClass('tab-active');
    $('#tab-bookmarks').addClass('hidden');
    $('#tab-chapters').removeClass('hidden');

    loadChapterList();
    setTimeout(function(){
        var act = $('#drawer-side .list-group-item.active');
        if(act[0])
            $('#drawer-side').scrollTop(act.attr('offset'));
    }, 250);
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

function loadChapterFromCache(chapterId) {
    const cached = chapterCache.get(chapterId);
    if (!cached) return false;

    $('.article-container').html(cached.chapter_view);
    chapter_id = chapterId;

    $('.list-group-item').removeClass('active bg-primary text-primary-content font-medium');
    $('.list-group-item[data-chapter-id="' + chapterId + '"]').addClass('active bg-primary text-primary-content font-medium');

    reinitPages();

    if (localStorage.getItem('prev-chapter')) {
        $('.pages-container').children().last().click();
        localStorage.removeItem('prev-chapter');
    }

    save_record();

    setTimeout(function() {
        preloadAround(chapterId, PRELOAD_RANGE).then(() => pruneCache(chapterId, PRELOAD_RANGE));
    }, 1000);

    var act = $('#drawer-side .list-group-item.active');
    if (act[0]) {
        var scrollTop = act.offset().top - act.height() * 4;
        act.attr('offset', scrollTop);
        $('#drawer-side').scrollTop(scrollTop);
    }

    return true;
}

$('.chapter_list_container').on('submit', 'form', function(e) {
    var targetId = parseInt($(this).find('button.list-group-item').attr('data-chapter-id'));
    if (targetId === chapter_id) {
        e.preventDefault();
        closeDrawer();
        return;
    }
    if (chapterCache.has(targetId)) {
        e.preventDefault();
        loadChapterFromCache(targetId);
        closeDrawer();
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
    var words = page_contents_len[parseInt($('.page-num.btn-active').first().text()) - 1] || 0;
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
