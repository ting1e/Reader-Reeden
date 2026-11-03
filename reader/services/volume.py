"""分卷识别、目录分组与同步索引映射。

设计原则：
- Chapter.index 是内部连续章节索引，分卷不会改变它。
- 卷标题不创建 Chapter，只记录在章节的 volume_index / volume_title 上。
- 仅在同步边界，把卷标题当作额外的一个 index；本地进度、数据库、阅读器内部
  始终使用原始章节索引。
"""
import re

VOLUME_SYNC_KEY = 'volumeIndexVersion'
VOLUME_SYNC_VERSION = 1

VOLUME_TITLE_MAX_LEN = 256


def compile_volume_rule(rule):
    """编译分卷正则；空规则返回 None。"""
    if not rule:
        return None
    return re.compile(rule, re.MULTILINE)


def find_volume_markers(data, rule):
    """返回卷标题标记列表，按出现顺序。

    每项包含 pos（标题匹配起点）、title（标题文本）、line_start（所在行起点）。
    """
    pattern = compile_volume_rule(rule)
    if pattern is None:
        return []
    markers = []
    for match in pattern.finditer(data):
        title = match.group().strip()
        if not title:
            continue
        markers.append({
            'pos': match.start(),
            'title': title[:VOLUME_TITLE_MAX_LEN],
            'line_start': data.rfind('\n', 0, match.start()) + 1,
        })
    return markers


def _volume_of_chapter(chapter):
    """返回 (volume_index, volume_title)；无有效卷信息时返回 (None, '')。"""
    index = getattr(chapter, 'volume_index', None)
    title = (getattr(chapter, 'volume_title', '') or '').strip()
    if index is None or not title:
        return None, ''
    return index, title


def has_volumes(chapters):
    """章节列表中是否存在有效分卷信息。"""
    return any(_volume_of_chapter(ch)[0] is not None for ch in chapters)


def group_chapters(chapters):
    """把按 index 排序的章节整理成目录分组。

    连续的同一卷章节合并为一个分组；无卷信息的连续章节合并为一个 title 为空的
    平铺分组。返回 [{'volume_index', 'title', 'chapters'}]。
    """
    groups = []
    for chapter in chapters:
        volume_index, volume_title = _volume_of_chapter(chapter)
        if volume_index is None:
            if not groups or groups[-1]['volume_index'] is not None:
                groups.append({'volume_index': None, 'title': '', 'chapters': []})
            groups[-1]['chapters'].append(chapter)
            continue
        if (not groups or groups[-1]['volume_index'] != volume_index
                or groups[-1]['title'] != volume_title):
            groups.append({
                'volume_index': volume_index,
                'title': volume_title,
                'chapters': [],
            })
        groups[-1]['chapters'].append(chapter)
    return groups


def build_sync_map(chapters):
    """构建同步目录映射表。

    每卷第一章之前插入一个卷条目。条目字段：
    - type: 'volume' | 'chapter'
    - sync_index: 同步索引
    - chapter_index: 该条目对应的内部章节索引（卷条目指向该卷第一章）
    - volume_index / volume_title: 所属卷信息
    """
    entries = []
    last_volume = None
    for chapter in chapters:
        volume_index, volume_title = _volume_of_chapter(chapter)
        key = (volume_index, volume_title) if volume_index is not None else None
        if key is not None and key != last_volume:
            entries.append({
                'type': 'volume',
                'sync_index': len(entries),
                'chapter_index': chapter.index,
                'volume_index': volume_index,
                'volume_title': volume_title,
            })
        entries.append({
            'type': 'chapter',
            'sync_index': len(entries),
            'chapter_index': chapter.index,
            'volume_index': volume_index,
            'volume_title': volume_title,
        })
        last_volume = key
    return entries


def sync_index_for_chapter(chapters, chapter_index):
    """内部章节索引 → 同步索引；没有分卷时原样返回。"""
    if not has_volumes(chapters):
        return chapter_index
    for entry in build_sync_map(chapters):
        if entry['type'] == 'chapter' and entry['chapter_index'] == chapter_index:
            return entry['sync_index']
    return chapter_index


def resolve_sync_index(chapters, sync_index):
    """同步索引 → (内部章节索引, 是否为卷标题)；无效索引返回 (None, False)。"""
    if not has_volumes(chapters):
        return sync_index, False
    entries = build_sync_map(chapters)
    if not isinstance(sync_index, int) or not 0 <= sync_index < len(entries):
        return None, False
    entry = entries[sync_index]
    return entry['chapter_index'], entry['type'] == 'volume'


def chapter_index_for_sync(chapters, sync_index):
    """同步索引 → 内部章节索引；卷标题恢复到该卷第一章。"""
    return resolve_sync_index(chapters, sync_index)[0]


def progress_to_sync(data, chapters):
    """把本地内部进度转换为同步协议进度（卷标题占额外 index）。"""
    if not isinstance(data, dict) or not has_volumes(chapters):
        return data
    try:
        section_index = int(data.get('sectionIndex'))
    except (TypeError, ValueError):
        return data
    result = dict(data)
    result['sectionIndex'] = sync_index_for_chapter(chapters, section_index)
    result[VOLUME_SYNC_KEY] = VOLUME_SYNC_VERSION
    return result


def progress_from_sync(data, chapters):
    """把同步协议进度恢复为内部章节索引。旧数据无标记时按原索引处理。"""
    if not isinstance(data, dict):
        return data
    if not data.get(VOLUME_SYNC_KEY):
        return data
    result = dict(data)
    result.pop(VOLUME_SYNC_KEY, None)
    if not has_volumes(chapters):
        return result
    try:
        section_index = int(result.get('sectionIndex'))
    except (TypeError, ValueError):
        return result
    chapter_index, is_volume = resolve_sync_index(chapters, section_index)
    if chapter_index is None:
        return result
    result['sectionIndex'] = chapter_index
    if is_volume:
        # 卷标题没有正文位置，恢复到该卷第一章开头。
        result['paragraphIndex'] = 0
        result['elementIndex'] = 0
    return result
