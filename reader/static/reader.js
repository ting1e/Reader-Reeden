var offcanvasLeft = new bootstrap.Offcanvas($('.offcanvas-start'))
var offcanvasSetting = new bootstrap.Offcanvas($('.offcanvas-bottom-setting'))

var page_width = $('article').width() + parseInt($('article').css('column-gap'))
var page_num = parseInt(($('#marker').offset().left - $('article').offset().left)/ page_width +1)
var page_contents_len = new Array(page_num + 1 ).fill(0);
var modal = new bootstrap.Modal($(".myModal")) // Returns a Bootstrap modal instance

// ===== 章节缓存系统 =====
const chapterCache = new Map();
const PRELOAD_RANGE = 10;

function getChapterUrl(chapterId) {
    return url_chapter_content.replace('/0/', '/' + chapterId + '/');
}

$('.chapter_list_btn').click(function(e){
    var act = $('.offcanvas-start .offcanvas-body .list-group-item.active ')
    console.log(1)
    if(act[0])
    {
        if (!act.attr('offset'))
            act.attr('offset',act.offset().top - act.height()*4)
        $('.offcanvas-start .offcanvas-body').scrollTop(act.offset().top - act.height()*4)

    }
    offcanvasLeft.toggle()
})
document.onkeydown=function(e){    //对整个页面监听
    var keyNum=window.event ? e.keyCode :e.which;       //获取被按下的键值
    if(keyNum==37){  //left
        $('.prev-page').click()
    }
    if(keyNum==38){  //up
        $('.page-link.prev-chapter')[0].click()
    }
    if(keyNum==39 | keyNum == 32){  //right space
        $('.next-page').click()
    }
    if(keyNum==40){  //down
        $('.page-link.next-chapter')[0].click()
    }
}

// 重新初始化分页（章节切换后可复用）
function reinitPages() {
    page_width = $('article').width() + parseInt($('article').css('column-gap'))
    page_num = parseInt(($('#marker').offset().left - $('article').offset().left) / page_width + 2)
    page_contents_len = new Array(page_num + 1).fill(0);

    $('article p').each((i, e) => {
        page_contents_len[parseInt($(e).offset().left / page_width) + 1] += $(e).text().length
    })
    for (var i = 1; i < page_num + 1; i++)
        page_contents_len[i] += page_contents_len[i - 1]

    $('.pages-container').empty();
    if (page_num > 0) {
        for (var i = 0; i < page_num; i++) {
            $('.pages-container').prepend($('<li class="page-item page-num"><a class="page-link" href="#">' + String(page_num - i) + '</a></li>'))
        }
        $('.pages-container').children().first().addClass('active')
    }

    $('article').css('transform', 'translateX(0px)');
    last_words = 0;
}

// 首次页面初始化
reinitPages();



// 上一章：优先使用缓存，缓存未命中则提交表单
$('.prev-chapter').click(function(e){
    e.preventDefault()
    var prevButton = $('.list-group-item.active').closest('form').parent().prev().find('button.list-group-item')
    if (!prevButton.length) return;
    var targetId = parseInt(prevButton.attr('data-chapter-id'));
    if (chapterCache.has(targetId)) {
        loadChapterFromCache(targetId);
    } else {
        prevButton.closest('form').submit();
    }
})

// 下一章：优先使用缓存，缓存未命中则提交表单
$('.next-chapter').click(function(e){
    e.preventDefault()
    var nextButton = $('.list-group-item.active').closest('form').parent().next().find('button.list-group-item')
    if (!nextButton.length) return;
    var targetId = parseInt(nextButton.attr('data-chapter-id'));
    if (chapterCache.has(targetId)) {
        loadChapterFromCache(targetId);
    } else {
        nextButton.closest('form').submit();
    }
})

$('.page-item').click(function(e){
    var cur = $('.page-item.active')
    var cur_idx = parseInt(cur.text()) -1
    var all = $('.page-num').length
    if ($(this).hasClass('prev-page'))
    {
        if(cur_idx>0)
        {

            cur.prev().addClass('active')
            cur.removeClass('active')
            $('article').css('transform',`translateX(-${page_width * (cur_idx - 1 )}px)`)
            save_record()

        }
        else{
            console.log(cur_idx)
            $('.page-link.prev-chapter')[0].click()
            localStorage.setItem('prev-chapter','true')
        }
    }
    else if ($(this).hasClass('next-page'))
    {
        if(cur_idx<all-1)
        {

            cur.next().addClass('active')
            cur.removeClass('active')
            $('article').css('transform',`translateX(-${page_width * (cur_idx +1 )}px)`)
            save_record()
        }
        else
        {
            $('.page-link.next-chapter')[0].click()
        }
    }
    else if ($(this).hasClass('page-num'))
    {
        cur.removeClass('active')
        $(this).addClass('active')
        cur_idx = parseInt($(this).text()) -1
        $('article').css('transform',`translateX(-${page_width * cur_idx}px)`)
        save_record()
    }
})

function save_record()
{
    $.ajax({
     url: url_book_reader,
     type: 'post',
     data: {
         'words': page_contents_len[parseInt($('.page-item.active').text()) - 1],
         csrfmiddlewaretoken: csrf_token
     },
     // 上面data为提交数据，下面data形参指代的就是异步提交的返回结果data
     success: function (data){
     console.log(data);
     }
 })

}


if(localStorage.getItem('prev-chapter'))
{
    $('.pages-container').children().last().click()
    localStorage.removeItem('prev-chapter')
    save_record()
}
else
{
    save_record()
}

for(var i=0;i<page_num+1;i++)
{
    if(page_contents_len[i]>last_words)
    {
        $('.page-item').each((idx,e)=>{
            if(parseInt($(e).text()) == i)
                $(e).click()
        })
        break
    }
}

$('.content-serarch').on("search", function() {
    // console.log($('.content-serarch').val())
    // console.log($(this).val())
    $.ajax({
     url: url_book_reader,
     type: 'post',
     data: {
         'kwd': $(this).val(),
         csrfmiddlewaretoken: csrf_token
     },
     // 上面data为提交数据，下面data形参指代的就是异步提交的返回结果data
     success: function (data){
        $('.search-res').html(data)
        // console.log($('.search-res .active'))
        if($('.search-res .active')[0])
            $('.search-res').scrollTop($('.search-res .active').offset().top)
    //  console.log(data);
     }

    })
 modal.show()
});

$('.search-btn').click(function(){
    modal.show()
})


$('#offcanvassetting').on('show.bs.offcanvas', function () {
    $('.font-value').text(parseInt($('article').css('font-size')))
    // $().addClass('bodder border-4 border-secondary')

        var had_choose = false
        $('.bg-setting').each(function(){
            if($(this).hasClass('bodder border-4 border-secondary'))
                had_choose = true
        })

        $('.bg-setting').each(function(){
            if(!had_choose && $(this).css('background')==user_setting_bg)
                $(this).addClass('bodder border-4 border-secondary')
        })
})



$('.inc-font').click(function(){
    var font = parseInt($('article').css('font-size'))
    font += 1
    $('.font-value').text(font)
    $('article').css('font-size',font)
})
$('.dec-font').click(function(){
    var font = parseInt($('article').css('font-size'))
    font -= 1
    $('.font-value').text(font)
    $('article').css('font-size',font)
})


$('.bg-setting').click(function(){
    $('main').css('background',$(this).css('background'))
    $('.bg-setting').removeClass('bodder border-4 border-secondary')
    $(this).addClass('bodder border-4 border-secondary')
})

$('.update-setting').click(function(){
    var bg = $('main').css('background')
    if($(this).hasClass('bg-setting'))
        bg = $(this).css('background')

    if ($(this).hasClass('read-black'))
        $('main').css('color','rgb(90,90,90)')

    $.ajax({
     url: url_update_setting,
     type: 'post',
     data: {
         'font_size': $('.font-value').text(),
         'read_bg':bg,
         csrfmiddlewaretoken: csrf_token
     },
     // 上面data为提交数据，下面data形参指代的就是异步提交的返回结果data
     success: function (data){

     console.log(data);
     }

    })
})

$('.setting-btn').click(function(){
    offcanvasSetting.toggle()
})

$('.bookmark-btn').click(function(){
    var cont =''
    $('article p').each((i,e)=>{
        if(parseInt($(e).offset().left) >0  && parseInt($(e).offset().left)<page_width)
            cont+=$(e).text()
    })
    $.ajax({
     url: url_bookmark_save,
     type: 'post',
     data: {
         'book_id':book_id,
         'chapter_id':chapter_id,
         'words_read':page_contents_len[parseInt($('.page-item.active').text()) - 1],
         'content':cont,
         csrfmiddlewaretoken: csrf_token
     },
     // 上面data为提交数据，下面data形参指代的就是异步提交的返回结果data
     success: function (data){
     console.log(data);
     }

    })
})

$('.offcanvas-start').on('show.bs.offcanvas',function(){
    // var act = $('.offcanvas-start .offcanvas-body .list-group-item.active ')
    // if(act[0])
    //     $('.offcanvas-start .offcanvas-body').scrollTop(act.offset().top - act.height()*4)
    // $.ajax({
    //     url: url_chapter_list,
    //     type: 'get',
    //     // 上面data为提交数据，下面data形参指代的就是异步提交的返回结果data
    //     success: function (data){
    //         $('.chapter_list_container').html(data)
    //     // console.log(data);
    //     }

    //    })
    $.ajax({
        url: url_bookmark_list,
        type: 'get',
        // 上面data为提交数据，下面data形参指代的就是异步提交的返回结果data
        success: function (data){
            $('.bookmark_list_container').html(data)
        // console.log(data);
        }

       })

})

$('.bookmark-show').click(function(){
    $('.offcanvas-start .offcanvas-body').scrollTop(0)
})





$('.chapter-list-show').click(function(){
    setTimeout(function(){
        var act = $('.offcanvas-start .offcanvas-body .list-group-item.active ')
        if(act[0])
            $('.offcanvas-start .offcanvas-body').scrollTop(act.attr('offset'))
    }, 250);

})

// ===== 章节缓存：预加载 & 内联切换 =====

// 预加载单个章节到缓存
async function preloadChapter(chapterId) {
    if (chapterCache.has(chapterId)) return;
    try {
        const resp = await fetch(getChapterUrl(chapterId));
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

// 预加载当前章节前后 range 章
async function preloadAround(currentId, range) {
    const idx = chapter_ids.indexOf(currentId);
    if (idx === -1) return;
    const start = Math.max(0, idx - range);
    const end = Math.min(chapter_ids.length - 1, idx + range);
    const toLoad = [];
    for (let i = start; i <= end; i++) {
        if (chapter_ids[i] !== currentId && !chapterCache.has(chapter_ids[i])) {
            toLoad.push(preloadChapter(chapter_ids[i]));
        }
    }
    await Promise.all(toLoad);
}

// 从缓存加载章节（内联替换，无需刷新页面）
function loadChapterFromCache(chapterId) {
    const cached = chapterCache.get(chapterId);
    if (!cached) return false;

    // 替换文章内容
    $('.article-container').html(cached.chapter_view);

    // 更新全局状态
    chapter_id = chapterId;

    // 更新侧边栏高亮
    $('.list-group-item').removeClass('active');
    $('.list-group-item[data-chapter-id="' + chapterId + '"]').addClass('active');

    // 重新初始化分页
    reinitPages();
    save_record();

    // 预加载新的边界章节
    preloadAround(chapterId, PRELOAD_RANGE);

    // 侧边栏滚动到当前章节
    var act = $('.offcanvas-start .offcanvas-body .list-group-item.active');
    if (act[0]) {
        var scrollTop = act.offset().top - act.height() * 4;
        act.attr('offset', scrollTop);
        $('.offcanvas-start .offcanvas-body').scrollTop(scrollTop);
    }

    return true;
}

// 拦截侧边栏章节列表点击：缓存命中则内联切换
$('.list-group').on('submit', 'form', function(e) {
    var targetId = parseInt($(this).find('button.list-group-item').attr('data-chapter-id'));
    if (targetId === chapter_id) {
        e.preventDefault();
        return;
    }
    if (chapterCache.has(targetId)) {
        e.preventDefault();
        loadChapterFromCache(targetId);
    }
    // 缓存未命中：走默认表单提交（整页刷新）
});

// 页面加载时预缓存前后10章
preloadAround(chapter_id, PRELOAD_RANGE);
