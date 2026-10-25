"""Local HTML/CSS → PNG using installed Chrome. No Python packages or server."""
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from zoneinfo import ZoneInfo
from agenda import timestamp


def card_data(snapshot, theme):
    if theme not in ('light', 'dark'):
        raise ValueError('主题必须为 light 或 dark')
    zone = ZoneInfo(snapshot['timezone'])
    today = datetime.fromisoformat(snapshot['now']).astimezone(zone)
    events = []
    for event in sorted(snapshot['events'], key=lambda e: timestamp(e['start'])):
        start = timestamp(event['start']).astimezone(zone)
        end = timestamp(event['end']).astimezone(zone) if event['end'] else None
        label = start.strftime('%H:%M' if start.date() == today.date() else '%m/%d %H:%M')
        if end:
            label += ' — ' + end.strftime('%H:%M' if end.date() == today.date() else '%m/%d %H:%M')
        else:
            label += ' · 结束未定'
        events.append({'title': event['title'], 'time': label})
    return {'theme': theme, 'day': f'{today.day:02}', 'month': f'{today.year} / {today.month:02}',
            'weekday': '星期' + '一二三四五六日'[today.weekday()], 'count': len(events),
            'timezone': zone.key, 'events': events}


def render(snapshot, output, theme='light', chrome=None):
    browser = chrome or shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
    if not browser:
        raise ValueError('未找到 Chrome/Chromium；文本摘要仍可独立发送')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    data = json.dumps(card_data(snapshot, theme), ensure_ascii=False).replace('<', '\\u003c')
    template = Path(__file__).resolve().parents[1] / 'templates' / 'card.html'
    html = output / f'agenda-{theme}.html'
    html.write_text(template.read_text(encoding='utf-8').replace('__DATA__', data), encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='agenda-chrome-') as profile:
        base = [browser, '--headless', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
                '--no-default-browser-check', '--disable-background-networking',
                '--allow-file-access-from-files', '--force-device-scale-factor=1',
                '--window-size=720,1200', '--virtual-time-budget=1000', '--user-data-dir=' + profile]
        dom = subprocess.run(base + ['--dump-dom', html.as_uri()], capture_output=True, text=True, timeout=45)
        match = re.search(r'name="card-pages" content="(\d+)"', dom.stdout)
        if dom.returncode or not match:
            raise ValueError('Chrome 排版失败：' + dom.stderr[-800:])
        images = []
        for page in range(1, int(match.group(1)) + 1):
            image = output / f'agenda-{theme}-{page:02}.png'
            run = subprocess.run(base + ['--screenshot=' + str(image), html.as_uri() + f'?page={page}'],
                                 capture_output=True, text=True, timeout=45)
            if run.returncode or not image.exists() or image.stat().st_size < 100:
                raise ValueError('Chrome 截图失败：' + run.stderr[-800:])
            images.append(str(image))
    return images
