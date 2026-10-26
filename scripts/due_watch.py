#!/usr/bin/env python3
"""Print deadline reminders for an external scheduler; never send them.

Checkpoints are nonnegative integer minutes, sorted descending with duplicates
normalised away. They apply only to timed deadlines; date-only deadlines get
one announcement within the existing two-hour window or overdue grace.
"""
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


def parse_checkpoints(value):
    try:
        marks = [int(part) for part in value.split(',')]
    except ValueError:
        raise argparse.ArgumentTypeError('检查点必须是逗号分隔的非负整数')
    if not marks or any(mark < 0 for mark in marks):
        raise argparse.ArgumentTypeError('检查点必须是非空的非负整数列表')
    return sorted(set(marks), reverse=True)


def remaining_label(delta, checkpoints=(120, 60, 30)):
    minutes = delta.total_seconds() / 60
    rounded = max(1, int(minutes + 0.5))
    if minutes > 30:
        rounded = int((minutes + 2.5) // 5) * 5
    # Do not round down across a checkpoint that has not yet been crossed.
    if any(rounded <= mark < minutes for mark in checkpoints):
        rounded = int((minutes + 4.999999) // 5) * 5
    if rounded >= 60:
        hours, minutes = divmod(rounded, 60)
        return f'还有约 {hours} 小时' + (f' {minutes} 分' if minutes else '')
    return f'还有约 {rounded} 分'


def reminders(events, sent, zone, now, checkpoints=(120, 60, 30), grace_minutes=720,
              min_gap_minutes=20):
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
        remaining = instant - now
        overdue = instant <= now < instant + timedelta(minutes=grace_minutes)
        if instant <= now:
            keys = [key if date_only else key + '#overdue'] if overdue else []
        elif date_only:
            keys = [key] if remaining <= timedelta(minutes=120) else []
        else:
            keys = [key + '#' + str(mark) for mark in checkpoints
                    if remaining <= timedelta(minutes=mark)]
            keys = [mark for mark in keys if mark not in updated]
            previous = [agenda.timestamp(value) for mark, value in updated.items()
                        if mark.startswith(key + '#')]
            if keys and previous:
                if now - max(previous) < timedelta(minutes=min_gap_minutes):
                    continue
                # Preserve more urgent crossed tiers for subsequent runs.
                keys = [max(keys, key=lambda mark: int(mark.rsplit('#', 1)[1]))]
        if not keys or all(mark in updated for mark in keys):
            continue
        label = due.strftime('%m-%d' if date_only else '%m-%d %H:%M')
        line = f'• {event["title"]} · 截止 {label}'
        if overdue:
            line += ' · 已过期' + ('' if date_only else ' ' + duration(now - instant))
        elif not date_only:
            line += ' · ' + remaining_label(remaining, checkpoints)
        lines.append(line)
        for mark in keys:
            updated[mark] = now.isoformat()
    return ('⏰ 截止提醒\n' + '\n'.join(lines) if lines else ''), updated


def watch(data, state, zone, now, checkpoints=(120, 60, 30), grace_minutes=720,
          dry_run=False, min_gap_minutes=20):
    events = agenda.read_events(data)
    if dry_run:
        return reminders(events, read_state(state), zone, now,
                         checkpoints, grace_minutes, min_gap_minutes)[0]
    with agenda.locked(state):
        sent = read_state(state)
        text, updated = reminders(events, sent, zone, now, checkpoints, grace_minutes,
                                  min_gap_minutes)
        if updated != sent:
            write_state(state, updated)
        return text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=agenda.default_data())
    parser.add_argument('--timezone', default='Asia/Shanghai')
    parser.add_argument('--checkpoints', type=parse_checkpoints, default='120,60,30')
    parser.add_argument('--grace-minutes', type=int, default=720)
    parser.add_argument('--min-gap-minutes', type=int, default=20)
    parser.add_argument('--state', type=Path)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--now', help='ISO 8601 timestamp with UTC offset')
    args = parser.parse_args(argv)
    try:
        if args.grace_minutes < 0:
            raise ValueError('宽限分钟数不能为负数')
        if args.min_gap_minutes < 0:
            raise ValueError('最小提醒间隔分钟数不能为负数')
        data = args.data.expanduser()
        state = args.state.expanduser() if args.state else data.parent / 'due-reminders.json'
        if state.resolve() == data.resolve():
            raise ValueError('提醒状态文件不能与日程数据文件相同')
        text = watch(data, state, ZoneInfo(args.timezone),
                     agenda.timestamp(args.now) if args.now else datetime.now(timezone.utc),
                     args.checkpoints, args.grace_minutes, args.dry_run,
                     args.min_gap_minutes)
        if text:
            print(text)
        return 0
    except (ValueError, OSError, KeyError, TypeError, OverflowError) as exc:
        print(f'due_watch: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
