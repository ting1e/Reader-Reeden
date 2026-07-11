from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
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
        if c['secs'] == 0:
            c['level'] = 0
        elif c['secs'] < max_secs * 0.25:
            c['level'] = 1
        elif c['secs'] < max_secs * 0.5:
            c['level'] = 2
        elif c['secs'] < max_secs * 0.75:
            c['level'] = 3
        else:
            c['level'] = 4
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
        if day['secs'] == 0:
            day['level'] = 0
        elif day['secs'] < year_max_secs * 0.25:
            day['level'] = 1
        elif day['secs'] < year_max_secs * 0.5:
            day['level'] = 2
        elif day['secs'] < year_max_secs * 0.75:
            day['level'] = 3
        else:
            day['level'] = 4

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
        if t['book_id'] > 0 and t['book_id'] in today_book_map:
            t['book_name'] = today_book_map[t['book_id']]
        elif not t['book_name']:
            t['book_name'] = '已删除'
        t['fmt'] = _fmt_duration(t['secs'])

    # 所有书籍的阅读时长排行
    top = list(ReadStat.objects.filter(user_id=uid)
               .values('book_id', 'book_name')
               .annotate(secs=Sum('read_seconds'), words=Sum('word_count'))
               .order_by('-secs'))
    book_ids = [t['book_id'] for t in top if t['book_id'] > 0]
    book_map = {b.id: b.name for b in Book.objects.filter(id__in=book_ids)}
    for t in top:
        if t['book_id'] > 0 and t['book_id'] in book_map:
            t['book_name'] = book_map[t['book_id']]
        elif not t['book_name']:
            t['book_name'] = '已删除'
        t['fmt'] = _fmt_duration(t['secs'])

    return render(request, 'reading_stats.html', {
        'today_seconds': today_seconds,
        'today_words': today_words,
        'today_fmt': _fmt_duration(today_seconds),
        'total_seconds': total_seconds,
        'total_words': total_words,
        'total_fmt': _fmt_duration(total_seconds),
        'total_books': total_books,
        'calendar_30': calendar_30,
        'month30_total_fmt': _fmt_duration(month30_total_seconds),
        'month30_total_words': month30_total_words,
        'today_top_books': today_top,
        'top_books': top,
        'year_total_fmt': _fmt_duration(year_total_seconds),
        'year_total_words': year_total_words,
        'year_columns': columns,
        'month_labels': month_label_list,
    })
