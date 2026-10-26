#!/usr/bin/env python3
"""Print pending deadline reminders for an external scheduler; never send them."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from zoneinfo import ZoneInfo

import agenda


def read_state(path):
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(data, dict) or data.get('version') != 1
            or not isinstance(data.get('sent'), dict)):
        raise ValueError('提醒状态文件结构无效')
    for key, value in data['sent'].items():
        if not isinstance(key, str):
            raise ValueError('提醒状态键无效')
        agenda.timestamp(value)
    return data['sent']


def write_state(path, sent):
    """Replace the state using a flushed temporary file in the same directory."""
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'version': 1, 'sent': sent}, stream, ensure_ascii=False, indent=2)
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


def duration(delta):
    minutes = int(delta.total_seconds() // 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours} 小时 {minutes} 分' if hours else f'{minutes} 分'


def reminders(events, sent, zone, now, window_minutes=120, grace_minutes=720):
    """Return reminder text and pruned/updated state without doing any I/O."""
    now = now.astimezone(timezone.utc)
    pending_ids = {event['id'] for event in events if not event.get('done', False)}
    # Split from the right: an event ID itself may contain an @ character.
    updated = {key: value for key, value in sent.items()
               if key.rsplit('@', 1)[0] in pending_ids}
    lines = []
    for event in events:
        if event.get('done', False) or event.get('due') is None:
            continue
        key = event['id'] + '@' + event['due']
        due, date_only = agenda.resolve(event['due'], zone)
        instant = due.astimezone(timezone.utc)
        upcoming = now < instant <= now + timedelta(minutes=window_minutes)
        overdue = instant <= now < instant + timedelta(minutes=grace_minutes)
        if key in updated or not (upcoming or overdue):
            continue
        label = due.strftime('%m-%d' if date_only else '%m-%d %H:%M')
        line = f'• {event["title"]} · 截止 {label}'
        if overdue:
            line += ' · 已过期' + ('' if date_only else ' ' + duration(now - instant))
        elif not date_only:
            line += ' · 还有 ' + duration(instant - now)
        lines.append(line)
        updated[key] = now.isoformat()
    return ('⏰ 截止提醒\n' + '\n'.join(lines) if lines else ''), updated


def watch(data, state, zone, now, window_minutes=120, grace_minutes=720, dry_run=False):
    events = agenda.read_events(data)
    if dry_run:
        return reminders(events, read_state(state), zone, now,
                         window_minutes, grace_minutes)[0]
    with agenda.locked(state):
        sent = read_state(state)
        text, updated = reminders(events, sent, zone, now, window_minutes, grace_minutes)
        if updated != sent:
            write_state(state, updated)
        return text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=agenda.default_data())
    parser.add_argument('--timezone', default='Asia/Shanghai')
    parser.add_argument('--window-minutes', type=int, default=120)
    parser.add_argument('--grace-minutes', type=int, default=720)
    parser.add_argument('--state', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--now', help='ISO 8601 timestamp with UTC offset')
    args = parser.parse_args(argv)
    try:
        if args.window_minutes < 0 or args.grace_minutes < 0:
            raise ValueError('提醒窗口与宽限分钟数不能为负数')
        data = args.data.expanduser()
        state = args.state.expanduser() if args.state else data.parent / 'due-reminders.json'
        if state.resolve() == data.resolve():
            raise ValueError('提醒状态文件不能与日程数据文件相同')
        text = watch(data, state, ZoneInfo(args.timezone),
                     agenda.timestamp(args.now) if args.now else datetime.now(timezone.utc),
                     args.window_minutes, args.grace_minutes, args.dry_run)
        if text:
            print(text)
        return 0
    except (ValueError, OSError, KeyError, TypeError, OverflowError) as exc:
        print(f'due_watch: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
