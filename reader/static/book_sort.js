// 书籍网格客户端排序（本地/远程书库共用）。包含前页面需先定义：
//      window.BOOK_SORT_DEFAULT_DIR  — 各排序键的默认方向，如 {name:'asc', size:'desc'}
//      window.BOOK_SORT_STORAGE_KEY  — localStorage 键名
(function() {
    var defaultDir = window.BOOK_SORT_DEFAULT_DIR || {};
    var STORAGE_KEY = window.BOOK_SORT_STORAGE_KEY || 'book_list_sort';
    var currentSort = null;
    var currentDir = null;

    function saveSortState() {
        if (currentSort) {
            localStorage.setItem(STORAGE_KEY, JSON.stringify({sort: currentSort, dir: currentDir}));
        }
    }

    function loadSortState() {
        try {
            var s = JSON.parse(localStorage.getItem(STORAGE_KEY));
            if (s && s.sort && defaultDir[s.sort] !== undefined) {
                return s;
            }
        } catch (e) {}
        return null;
    }

    function itemValue(el, sortKey) {
        // 书名统一读 data-book-name（与 header 搜索框共用同一属性，避免重复）
        if (sortKey === 'name') return $(el).data('book-name');
        return $(el).data('sort-' + sortKey);
    }

    function applySort(sortKey, dir) {
        if (arguments.length >= 2) {
            currentSort = sortKey;
            currentDir = dir;
        } else if (currentSort === sortKey) {
            currentDir = currentDir === 'asc' ? 'desc' : 'asc';
        } else {
            currentSort = sortKey;
            currentDir = defaultDir[sortKey] || 'asc';
        }
        // name 按语言环境排序；date 为 ISO 字符串，字典序即时间序；其余按数值
        var isText = (currentSort === 'name' || currentSort === 'date');
        var $grid = $('.book-grid');
        var $items = $grid.children('.book-item').get();
        $items.sort(function(a, b) {
            var av = itemValue(a, currentSort);
            var bv = itemValue(b, currentSort);
            if (isText) {
                av = String(av || '');
                bv = String(bv || '');
                return currentDir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av);
            }
            av = parseFloat(av) || 0;
            bv = parseFloat(bv) || 0;
            return currentDir === 'asc' ? av - bv : bv - av;
        });
        $.each($items, function(i, el) { $grid.append(el); });

        $('.sort-btn').each(function() {
            var active = $(this).data('sort') === currentSort;
            $(this).toggleClass('btn-active', active);
            var label = $(this).text().replace(/[▲▼]\s*$/, '').trim();
            $(this).text(label);
            if (active) {
                $(this).text(label + (currentDir === 'asc' ? ' ▲' : ' ▼'));
            }
        });
        saveSortState();
    }

    $('.sort-btn').click(function() {
        applySort($(this).data('sort'));
    });

    var saved = loadSortState();
    if (saved) {
        applySort(saved.sort, saved.dir);
    }
})();
