from datetime import date, timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone

from ..models import ReadStat, UserBookRecord, Book


def _fmt_duration(seconds):
    """将秒数格式化为 'Xh Ym Zs'。"""
    if seconds is None:
        seconds = 0
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f'{h}h {m}m {s}s'
    if m > 0:
        return f'{m}m {s}s'
    return f'{s}s'


def _level(secs, max_secs):
    """按最大值映射热力图等级 0-4。"""
    if not secs:
        return 0
    if max_secs < 1:
        max_secs = 1
    if secs < max_secs * 0.25:
        return 1
    if secs < max_secs * 0.5:
        return 2
    if secs < max_secs * 0.75:
        return 3
    return 4


def _resolve_name(t, book_map):
    """统一解析 books 聚合行的展示书名并补 fmt/speed。"""
    bid = t.get('book_id')
    if bid and bid > 0 and bid in book_map:
        t['book_name'] = book_map[bid]
    elif not t['book_name']:
        t['book_name'] = '已删除'
    t['fmt'] = _fmt_duration(t['secs'])
    secs = t.get('secs') or 0
    words = t.get('words') or 0
    t['speed'] = int(round(words * 60 / secs)) if secs > 0 else 0
    return t


def _expand_days(days_list):
    """补全为从首个阅读日到末个阅读日的连续区间，缺失日期填 0。

    区间以"实际有阅读"(secs>0)的首末日为准，忽略仅打开未读的零时长记录，
    避免把今天等无阅读的日期拉进柱状图。
    """
    if not days_list:
        return []
    parsed = []
    for d in days_list:
        y, m, dd = (int(x) for x in d['date'].split('-'))
        parsed.append((date(y, m, dd), d))
    parsed.sort(key=lambda x: x[0])
    reading = [p for p in parsed if (p[1].get('secs') or 0) > 0]
    if not reading:
        return []
    start, end = reading[0][0], reading[-1][0]
    by_date = {dt: info for dt, info in parsed}
    full = []
    cur = start
    while cur <= end:
        info = by_date.get(cur)
        if info:
            entry = dict(info)
        else:
            entry = {'date': cur.strftime('%Y-%m-%d'), 'secs': 0, 'words': 0, 'fmt': '0s'}
        # 月份标签：首日或每月 1 号标注，统一显示年份
        if cur == start or cur.day == 1:
            entry['month_label'] = f'{cur.year}年{cur.month}月'
        else:
            entry['month_label'] = ''
        full.append(entry)
        cur += timedelta(days=1)
    return full


@login_required(login_url='reader:index')
def reading_stats(request):
    """阅读统计页面：今日/累计概览 + 30 天热力图 + Top5 书籍"""
    uid = request.user.id
    today = timezone.now().strftime('%Y-%m-%d')

    # 今日
    today_qs = list(ReadStat.objects.filter(user_id=uid, date=today))
    today_seconds = sum(s.read_seconds for s in today_qs)
    today_words = sum(s.word_count for s in today_qs)

    # 累计
    all_stats = list(ReadStat.objects.filter(user_id=uid))
    total_seconds = sum(s.read_seconds for s in all_stats)
    total_words = sum(s.word_count for s in all_stats)
    total_books = UserBookRecord.objects.filter(user_id=uid).count()

    # 阅读速度（字/分钟）
    today_speed = int(round(today_words * 60 / today_seconds)) if today_seconds > 0 else 0
    total_speed = int(round(total_words * 60 / total_seconds)) if total_seconds > 0 else 0

    # 最近 30 天每日汇总
    start = (timezone.now() - timedelta(days=29)).strftime('%Y-%m-%d')
    daily = list(ReadStat.objects.filter(user_id=uid, date__gte=start)
                 .values('date').order_by('date')
                 .annotate(secs=Sum('read_seconds'), words=Sum('word_count')))
    daily_map = {d['date']: d for d in daily}
    # 补全 30 天（缺失日期填 0）
    calendar_30 = []
    for i in range(30):
        d_date = (timezone.now() - timedelta(days=29 - i)).date()
        d = d_date.strftime('%Y-%m-%d')
        info = daily_map.get(d)
        calendar_30.append({
            'date': d,
            'secs': info['secs'] if info else 0,
            'words': info['words'] if info else 0,
            'weekday': d_date.weekday(),
        })

    # 为热力图计算每格的透明度级别 0-4
    max_secs = max((c['secs'] for c in calendar_30), default=1) or 1
    for c in calendar_30:
        c['level'] = _level(c['secs'], max_secs)
        c['fmt'] = _fmt_duration(c['secs'])

    month30_total_seconds = sum(c['secs'] for c in calendar_30)
    month30_total_words = sum(c['words'] for c in calendar_30)

    # 构建近一年日历热力图（GitHub 风格：每列一周，7 格/列）
    # 范围：今天往前推 365 天 ~ 今天
    today_date = timezone.now().date()
    start_date = today_date - timedelta(days=364)  # 含今天共 365 天
    start_str = start_date.strftime('%Y-%m-%d')

    # 查询近一年内的所有 ReadStat
    year_stats = list(ReadStat.objects.filter(user_id=uid, date__gte=start_str))
    # 按日期汇总（同一天可能有多本书的记录）
    stat_map = {}
    for s in year_stats:
        if s.date not in stat_map:
            stat_map[s.date] = {'secs': 0, 'words': 0}
        stat_map[s.date]['secs'] += s.read_seconds
        stat_map[s.date]['words'] += s.word_count
    year_total_seconds = sum(v['secs'] for v in stat_map.values())
    year_total_words = sum(v['words'] for v in stat_map.values())

    all_days = []
    d = start_date
    while d <= today_date:
        dstr = d.strftime('%Y-%m-%d')
        info = stat_map.get(dstr)
        secs = info['secs'] if info else 0
        words = info['words'] if info else 0
        all_days.append({
            'date': dstr,
            'secs': secs,
            'words': words,
            'fmt': _fmt_duration(secs),
            'weekday': d.weekday(),
            'month': d.month,
        })
        d += timedelta(days=1)

    # 计算颜色级别
    year_max_secs = max((day['secs'] for day in all_days), default=1) or 1
    for day in all_days:
        day['level'] = _level(day['secs'], year_max_secs)

    # 组织成列（每列 = 一周，7 行）
    # 第一列从 1 月 1 日所在周的周一开始
    first_weekday = all_days[0]['weekday']  # 0=Mon
    # 前面补空格使第一列对齐到周一
    lead_blanks = first_weekday
    # 按列组织：每 7 个元素为一列
    columns = []
    current_col = [None] * lead_blanks  # 前置空位
    for day in all_days:
        current_col.append(day)
        if len(current_col) == 7:
            columns.append(current_col)
            current_col = []
    if current_col:
        # 尾部补空格
        while len(current_col) < 7:
            current_col.append(None)
        columns.append(current_col)

    # 转置为行（每周一行 → 每行 7 天），模板单层循环渲染，附带周几标签
    weekday_labels = ['一', '', '三', '', '五', '', '日']
    year_rows = [(weekday_labels[i], [col[i] for col in columns]) for i in range(7)]

    # 月份标签：找到每月第一天所在的列索引
    months_cn = ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月']
    month_label_list = []
    seen_keys = set()
    for ci, col in enumerate(columns):
        for day in col:
            if day:
                ym = day['date'][:7]  # 'YYYY-MM'
                if ym not in seen_keys:
                    seen_keys.add(ym)
                    month_label_list.append((ci, months_cn[day['month'] - 1]))
                    break

    # 今日 Top 5 书籍
    today_top = list(ReadStat.objects.filter(user_id=uid, date=today)
                     .values('book_id', 'book_name')
                     .annotate(secs=Sum('read_seconds'), words=Sum('word_count'))
                     .order_by('-secs')[:5])
    today_book_ids = [t['book_id'] for t in today_top if t['book_id'] > 0]
    today_book_map = {b.id: b.name for b in Book.objects.filter(id__in=today_book_ids)}
    for t in today_top:
        _resolve_name(t, today_book_map)

    # 所有书籍的阅读时长排行
    top = list(ReadStat.objects.filter(user_id=uid)
               .values('book_id', 'book_name')
               .annotate(secs=Sum('read_seconds'), words=Sum('word_count'))
               .order_by('-secs'))
    book_ids = [t['book_id'] for t in top if t['book_id'] > 0]
    book_map = {b.id: b.name for b in Book.objects.filter(id__in=book_ids)}
    for t in top:
        _resolve_name(t, book_map)

    # 为每本书构建每日明细（用于排行区下方的柱状图）
    top_detail_map = {}
    for s in ReadStat.objects.filter(user_id=uid).order_by('date'):
        key = (s.book_id, '') if s.book_id > 0 else (0, s.book_name)
        top_detail_map.setdefault(key, []).append({
            'date': s.date, 'secs': s.read_seconds,
            'words': s.word_count, 'fmt': _fmt_duration(s.read_seconds),
        })
    for t in top:
        key = (t['book_id'], '') if t['book_id'] > 0 else (0, t['book_name'])
        days_list = _expand_days(top_detail_map.get(key, []))
        t['days_list'] = days_list
        t['max_secs'] = max((d['secs'] for d in days_list), default=0) or 1

    return render(request, 'reading_stats.html', {
        'today_seconds': today_seconds,
        'today_words': today_words,
        'today_fmt': _fmt_duration(today_seconds),
        'today_speed': today_speed,
        'total_seconds': total_seconds,
        'total_words': total_words,
        'total_fmt': _fmt_duration(total_seconds),
        'total_speed': total_speed,
        'total_books': total_books,
        'calendar_30': calendar_30,
        'month30_total_fmt': _fmt_duration(month30_total_seconds),
        'month30_total_words': month30_total_words,
        'today_top_books': today_top,
        'top_books': top,
        'year_total_fmt': _fmt_duration(year_total_seconds),
        'year_total_words': year_total_words,
        'year_rows': year_rows,
        'month_labels': month_label_list,
    })


@login_required(login_url='reader:index')
def reading_stats_admin(request):
    """阅读统计管理：按书聚合当前用户的全部阅读统计，支持按书/按天删除。"""
    uid = request.user.id

    # 1) 存在的书：按 book_id 聚合（book_name 可能改名不一致，统一用 Book.name 显示）
    exist_rows = list(
        ReadStat.objects.filter(user_id=uid, book_id__gt=0)
        .values('book_id')
        .annotate(secs=Sum('read_seconds'), words=Sum('word_count'),
                  days=Count('date', distinct=True), last_date=Max('date'))
        .order_by('-secs')
    )
    book_map = {b.id: b.name for b in Book.objects.filter(
        id__in=[r['book_id'] for r in exist_rows])}
    # 2) 已删除的书（book_id=0）：按 book_name 聚合
    deleted_rows = list(
        ReadStat.objects.filter(user_id=uid, book_id=0)
        .exclude(book_name='')
        .values('book_name')
        .annotate(secs=Sum('read_seconds'), words=Sum('word_count'),
                  days=Count('date', distinct=True), last_date=Max('date'))
        .order_by('-secs')
    )

    # 日明细：一次查全部行，按 (book_id, book_name) 分组，避免 N+1
    all_rows = list(ReadStat.objects.filter(user_id=uid).order_by('-date'))
    detail_map = {}
    for s in all_rows:
        key = (s.book_id, s.book_name if s.book_id == 0 else '')
        detail_map.setdefault(key, []).append({
            'date': s.date,
            'secs': s.read_seconds,
            'words': s.word_count,
            'fmt': _fmt_duration(s.read_seconds),
        })

    stats_list = []
    # 存在的书
    for r in exist_rows:
        bid = r['book_id']
        name = book_map.get(bid) or '已删除'
        key = (bid, '')
        days_list = _expand_days(detail_map.get(key, []))
        secs = r['secs'] or 0
        words = r['words'] or 0
        stats_list.append({
            'book_id': bid,
            'book_name': name,
            'secs': secs,
            'words': words,
            'days': r['days'] or 0,
            'last_date': r['last_date'] or '',
            'fmt': _fmt_duration(secs),
            'speed': int(round(words * 60 / secs)) if secs > 0 else 0,
            'days_list': days_list,
            'max_secs': max((d['secs'] for d in days_list), default=0) or 1,
        })
    # 已删除的书
    for r in deleted_rows:
        bname = r['book_name'] or '已删除'
        key = (0, bname)
        days_list = _expand_days(detail_map.get(key, []))
        secs = r['secs'] or 0
        words = r['words'] or 0
        stats_list.append({
            'book_id': 0,
            'book_name': bname,
            'secs': secs,
            'words': words,
            'days': r['days'] or 0,
            'last_date': r['last_date'] or '',
            'fmt': _fmt_duration(secs),
            'speed': int(round(words * 60 / secs)) if secs > 0 else 0,
            'days_list': days_list,
            'max_secs': max((d['secs'] for d in days_list), default=0) or 1,
        })

    # 统一按时长倒序
    stats_list.sort(key=lambda x: x['secs'], reverse=True)

    return render(request, 'reading_stats_admin.html', {
        'stats_list': stats_list,
    })


@login_required(login_url='reader:index')
def reading_stats_del(request):
    """删除某本书的阅读统计（整本或某天）。AJAX，返回 JSON。"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'method not allowed'}, status=405)

    uid = request.user.id
    try:
        book_id = int(request.POST.get('book_id', ''))
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': '缺少 book_id'})

    date = request.POST.get('date') or None

    qs = ReadStat.objects.filter(user_id=uid)
    if book_id > 0:
        # 存在的书：仅按 book_id 删，不限制 book_name（改名后旧行也能删掉）
        qs = qs.filter(book_id=book_id)
    else:
        # 已删除的书（book_id=0）：按 book_name 删
        book_name = request.POST.get('book_name', '').strip()
        if not book_name:
            return JsonResponse({'success': False, 'error': 'book_id=0 时需提供 book_name'})
        qs = qs.filter(book_id=0, book_name=book_name)

    if date:
        qs = qs.filter(date=date)

    # 删除前汇总被删数据
    agg = qs.aggregate(s=Sum('read_seconds'), w=Sum('word_count'))
    deleted_secs = agg['s'] or 0
    deleted_words = agg['w'] or 0

    deleted_count, _ = qs.delete()

    # 删除后该书的剩余统计（供前端刷新累计栏）
    rem_qs = ReadStat.objects.filter(user_id=uid)
    if book_id > 0:
        rem_qs = rem_qs.filter(book_id=book_id)
    else:
        rem_qs = rem_qs.filter(book_id=0, book_name=book_name)
    rem = rem_qs.aggregate(s=Sum('read_seconds'), w=Sum('word_count'),
                           d=Count('date', distinct=True), last=Max('date'))
    rem_secs = rem['s'] or 0
    rem_words = rem['w'] or 0
    rem_days = rem['d'] or 0
    return JsonResponse({
        'success': True,
        'deleted_count': deleted_count,
        'deleted_secs': deleted_secs,
        'deleted_words': deleted_words,
        'remaining_secs': rem_secs,
        'remaining_words': rem_words,
        'remaining_days': rem_days,
        'remaining_last_date': rem['last'] or '',
        'remaining_fmt': _fmt_duration(rem_secs),
        'remaining_speed': int(round(rem_words * 60 / rem_secs)) if rem_secs > 0 else 0,
    })
