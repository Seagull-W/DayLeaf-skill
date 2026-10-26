#!/usr/bin/env python3
"""Small, local agenda. JSON stdin/stdout; Python 3.9+, POSIX."""
import argparse
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
import fcntl
import json
import os
import re
from pathlib import Path
import sys
import tempfile
import uuid
from zoneinfo import ZoneInfo


class AgendaError(ValueError):
    pass


def timestamp(value):
    if not isinstance(value, str):
        raise AgendaError('时间必须是带 UTC 偏移的 ISO 8601 字符串')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise AgendaError('时间格式无效') from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise AgendaError('时间必须带 UTC 偏移，例如 2026-10-26T09:00:00+08:00')
    return result.astimezone(timezone.utc)


def validate_time(value):
    """Return a date or an offset-aware datetime without needing a zone."""
    if isinstance(value, str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise AgendaError('日期格式无效') from exc
    return timestamp(value)


def resolve(value, zone):
    parsed = validate_time(value)
    if isinstance(parsed, datetime):
        return parsed.astimezone(zone), False
    return datetime.combine(parsed, time.min, zone), True


def validate_event(event):
    if set(event) not in ({'id', 'title', 'start', 'end', 'notes'},
                          {'id', 'title', 'start', 'end', 'notes', 'done'}):
        raise AgendaError('日程字段无效')
    if not isinstance(event['id'], str) or not event['id']:
        raise AgendaError('ID 无效')
    if not isinstance(event['title'], str) or not event['title'].strip():
        raise AgendaError('标题不能为空')
    if event['notes'] is not None and not isinstance(event['notes'], str):
        raise AgendaError('备注必须是字符串或 null')
    if 'done' in event and not isinstance(event['done'], bool):
        raise AgendaError('done 必须是布尔值')
    start = validate_time(event['start'])
    if event['end'] is not None:
        end = validate_time(event['end'])
        if isinstance(start, datetime) != isinstance(end, datetime):
            raise AgendaError('开始与结束时间必须使用相同精度')
        if end <= start:
            raise AgendaError('结束时间必须晚于开始时间')


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Lock a stable sidecar inode: replacing the JSON must not replace the lock.
    fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def read_events(path):
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('events'), list):
        raise AgendaError('数据文件结构无效，停止操作；请恢复备份')
    ids = set()
    for event in data['events']:
        if not isinstance(event, dict):
            raise AgendaError('数据文件日程无效')
        validate_event(event)
        event.setdefault('done', False)
        if event['id'] in ids:
            raise AgendaError('数据文件包含重复 ID')
        ids.add(event['id'])
    return data['events']


def write_events(path, events):
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'version': 1, 'events': events}, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def overlaps(left, right, zone=timezone.utc):
    a, a_date = resolve(left['start'], zone)
    b, b_date = resolve(right['start'], zone)
    if a_date or b_date:
        return False
    a, b = a.astimezone(timezone.utc), b.astimezone(timezone.utc)
    ae = resolve(left['end'], zone)[0].astimezone(timezone.utc) if left['end'] else a
    be = resolve(right['end'], zone)[0].astimezone(timezone.utc) if right['end'] else b
    if ae == a:
        return a == b if be == b else b <= a < be
    if be == b:
        return a <= b < ae
    return a < be and b < ae


def bounds(period, zone, now):
    today = now.astimezone(zone).date()
    if period == 'week':
        today -= timedelta(days=today.weekday())
    elif period != 'today':
        raise AgendaError('period 只支持 today 或 week')
    start = datetime.combine(today, time.min, zone)
    end = datetime.combine(today + timedelta(days=7 if period == 'week' else 1), time.min, zone)
    return start, end


def select(events, request, zone, now):
    start = end = None
    if 'period' in request:
        if 'from' in request or 'to' in request:
            raise AgendaError('period 与 from/to 不能同时使用')
        start, end = bounds(request['period'], zone, now)
    else:
        start = resolve(request['from'], zone)[0] if 'from' in request else None
        end = resolve(request['to'], zone)[0] if 'to' in request else None
    if start and end and start >= end:
        raise AgendaError('查询结束时间必须晚于开始时间')
    query = request.get('query', '')
    if not isinstance(query, str):
        raise AgendaError('query 必须是字符串')
    status = request.get('status', 'all')
    if status not in ('all', 'pending', 'done'):
        raise AgendaError('status 只支持 all、pending 或 done')
    result = []
    for event in events:
        if status != 'all' and event.get('done', False) != (status == 'done'):
            continue
        s = resolve(event['start'], zone)[0]
        e = resolve(event['end'], zone)[0] if event['end'] else s
        if start and (e < start or (e == start and e != s)):
            continue
        if end and s >= end:
            continue
        if query.casefold() not in (event['title'] + '\n' + (event['notes'] or '')).casefold():
            continue
        result.append(event)
    return sorted(result, key=lambda e: (resolve(e['start'], zone)[0], e['id']))


def clean(text):
    return ' '.join(text.split())


def summary(events, period, zone, now):
    start, end = bounds(period, zone, now)
    label = '今日日程' if period == 'today' else '本周日程'
    dates = start.strftime('%Y-%m-%d')
    if period == 'week':
        dates += ' ～ ' + (end - timedelta(days=1)).strftime('%m-%d')
    lines = [f'📅 {label}｜{dates}', f'时区：{zone.key} · 共 {len(events)} 项', '━━━━━━━━━━━━']
    previous = None
    for event in events:
        s, date_only = resolve(event['start'], zone)
        e = resolve(event['end'], zone)[0] if event['end'] else None
        if s.date() != previous:
            lines.append(f'🗓 {s:%m-%d} 周{"一二三四五六日"[s.weekday()]}')
            previous = s.date()
        span = '时间未定' if date_only else s.strftime('%H:%M')
        if e and not date_only:
            span += '–' + e.strftime('%H:%M' if e.date() == s.date() else '%m-%d %H:%M')
        marker = '✅ ' if event.get('done', False) else ''
        lines.append(f'• {span}  {marker}{clean(event["title"])}')
        if event['notes']:
            lines.append('  📝 ' + clean(event['notes']))
    if not events:
        lines.append('暂无安排，留点时间给自己 ☀️')
    return '\n'.join(lines)


def execute(path, request):
    if not isinstance(request, dict):
        raise AgendaError('输入必须是 JSON 对象')
    action = request.get('action')
    fields = {
        'add': {'title', 'start', 'end', 'notes', 'done', 'allow_conflict'},
        'update': {'id', 'changes', 'allow_conflict'},
        'delete': {'id'},
        'list': {'period', 'from', 'to', 'query', 'status'},
        'summary': {'period', 'status'},
    }
    if action not in fields:
        raise AgendaError('action 必须为 add/list/update/delete/summary')
    if set(request) - fields[action] - {'action', 'timezone'}:
        raise AgendaError('包含未知字段')
    if 'allow_conflict' in request and not isinstance(request['allow_conflict'], bool):
        raise AgendaError('allow_conflict 必须是布尔值')
    zone = ZoneInfo(request.get('timezone', 'Asia/Shanghai'))
    now = datetime.now(timezone.utc)
    with locked(path):
        events = read_events(path)
        if action in ('list', 'summary'):
            filters = dict(request)
            if action == 'summary':
                filters.setdefault('period', 'today')
            selected = select(events, filters, zone, now)
            result = {'ok': True, 'events': selected, 'timezone': zone.key, 'now': now.astimezone(zone).isoformat()}
            if action == 'summary':
                result['text'] = summary(selected, filters['period'], zone, now)
            return result
        old = None
        if action in ('update', 'delete'):
            matches = [e for e in events if e['id'] == request.get('id')]
            if len(matches) != 1:
                raise AgendaError('必须提供唯一且存在的完整 ID；先查询并向用户澄清目标')
            old = matches[0]
        if action == 'delete':
            events.remove(old)
            write_events(path, events)
            return {'ok': True, 'deleted': old}
        if action == 'add':
            event = {'id': uuid.uuid4().hex, 'title': request.get('title'), 'start': request.get('start', now.astimezone(zone).date().isoformat()),
                     'end': request.get('end'), 'notes': request.get('notes'), 'done': request.get('done', False)}
        else:
            changes = request.get('changes')
            if not isinstance(changes, dict) or not changes or set(changes) - {'title', 'start', 'end', 'notes', 'done'}:
                raise AgendaError('changes 必须包含可修改字段 title/start/end/notes/done')
            event = {**old, **changes}
        validate_event(event)
        conflicts = [e for e in events if e['id'] != event['id'] and overlaps(event, e, zone)]
        if conflicts and not request.get('allow_conflict', False):
            return {'ok': False, 'error': 'conflict', 'message': '时间冲突，尚未保存；请用户决定是否重叠安排', 'conflicts': conflicts}
        if old:
            events.remove(old)
        events.append(event)
        write_events(path, events)
        return {'ok': True, 'event': event, 'conflicts': conflicts}


def default_data():
    return Path(os.environ.get('HERMES_HOME', Path.home() / '.hermes')) / 'agenda' / 'events.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=default_data())
    args = parser.parse_args()
    try:
        result = execute(args.data.expanduser(), json.load(sys.stdin))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        result = {'ok': False, 'error': type(exc).__name__, 'message': str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
