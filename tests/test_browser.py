"""Opt-in real-browser layout test: AGENDA_RENDER_TEST=1 python3 -m unittest discover ..."""
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from render_card import render


@unittest.skipUnless(os.environ.get('AGENDA_RENDER_TEST') == '1', 'real Chrome test is opt-in')
class BrowserTests(unittest.TestCase):
    def test_pagination_and_png(self):
        snapshot = {'now': '2026-10-26T08:00:00+08:00', 'timezone': 'Asia/Shanghai', 'events': [
            {'title': '长标题中文 English <script> & 😀 ' * 45 if i == 3 else f'第 {i} 项日程 · 清晰阅读',
             'start': f'2026-10-26T{i+8:02}:00:00+08:00', 'end': None} for i in range(9)]}
        with tempfile.TemporaryDirectory() as directory:
            images = render(snapshot, directory)
            self.assertGreater(len(images), 2)
            for image in images:
                data = Path(image).read_bytes()
                self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
                self.assertEqual(struct.unpack('>II', data[16:24]), (720, 1200))
            # Capture the live layout before non-selected pages are hidden.
            html = Path(directory) / 'agenda-light.html'
            source = html.read_text()
            probe = '''const check=document.createElement('meta');check.name='layout-test';check.content=JSON.stringify({titles:Array.from(document.querySelectorAll('.event-title')).map(n=>n.textContent).join(''),overflow:pages.some(p=>p.scrollHeight>1200||Array.from(p.querySelectorAll('.event')).some(n=>n.getBoundingClientRect().bottom>p.querySelector('.events').getBoundingClientRect().bottom))});document.head.append(check);'''
            html.write_text(source.replace('const chosen=', probe + 'const chosen='))
            run = subprocess.run([shutil.which('google-chrome'), '--headless', '--disable-gpu', '--no-first-run', '--user-data-dir=' + directory + '/profile', '--dump-dom', html.as_uri()], capture_output=True, text=True, timeout=45)
            from html import unescape
            match = re.search(r'name="layout-test" content="([^"]+)"', run.stdout)
            self.assertIsNotNone(match)
            check = json.loads(unescape(match.group(1)))
            self.assertFalse(check['overflow'])
            self.assertEqual(check['titles'], ''.join(e['title'] for e in snapshot['events']))
