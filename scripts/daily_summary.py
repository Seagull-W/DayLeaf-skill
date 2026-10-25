#!/usr/bin/env python3
"""Render a daily agenda and deliver using the existing Hermes CLI."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from agenda import default_data, execute
from render_card import render


def deliver(text, target, hermes):
    process = subprocess.run([hermes, 'send', '--to', target, '--file', '-', '--json'],
                             input=text, text=True, capture_output=True, timeout=90)
    if process.returncode:
        raise ValueError('Hermes 发送失败：' + (process.stderr or process.stdout).strip())
    response = json.loads(process.stdout)
    # Hermes considers skipped delivery exit 0; it is not a successful push.
    if response.get('error') or response.get('skipped') or not response.get('success'):
        raise ValueError('Hermes 未确认发送成功：' + json.dumps(response, ensure_ascii=False))
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=default_data())
    parser.add_argument('--timezone', default='Asia/Shanghai')
    parser.add_argument('--to', help='Hermes target: platform:chat_id')
    parser.add_argument('--hermes', default='hermes', help='Hermes executable, preferably absolute path')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--theme', choices=['light', 'dark'], default='light')
    parser.add_argument('--chrome', help='Chrome/Chromium 可执行文件路径')
    parser.add_argument('--output-dir', type=Path, help='保留卡片的目录')
    parser.add_argument('--text-only', action='store_true', help='仅文本，跳过图片渲染')
    args = parser.parse_args()
    try:
        if not args.dry_run and not args.to:
            raise ValueError('推送必须提供 --to；预览请使用 --dry-run')
        result = execute(args.data.expanduser(), {'action': 'summary', 'period': 'today', 'timezone': args.timezone})
        # Independent text delivery comes first: rendering or media failure cannot suppress it.
        result['sent'] = False
        result['image_sent'] = False
        if not args.dry_run:
            result['delivery'] = deliver(result['text'], args.to, args.hermes)
            result['sent'] = True
        if not args.text_only:
            output = args.output_dir or args.data.expanduser().parent / 'cards' / result['now'][:10]
            try:
                result['images'] = render(result, output, args.theme, args.chrome)
                result['image_deliveries'] = []
                if not args.dry_run:
                    for image in result['images']:
                        result['image_deliveries'].append(deliver('MEDIA:' + image, args.to, args.hermes))
                    result['image_sent'] = True
            except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
                result.update(ok=False, error='image_failed', message=str(exc))
                # Preserve sent=true to distinguish partial delivery from total failure.

    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        result = {'ok': False, 'error': type(exc).__name__, 'message': str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
