import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import daily_summary
from render_card import card_data, render


class CardTests(unittest.TestCase):
    def snapshot(self):
        return {'now': '2026-10-26T08:00:00+08:00', 'timezone': 'Asia/Shanghai', 'events': [
            {'title': '后项', 'start': '2026-10-26T14:00:00+08:00', 'end': None},
            {'title': '</script><script>bad()</script>', 'start': '2026-10-25T23:00:00+08:00', 'end': '2026-10-26T09:00:00+08:00'}]}

    def test_dates_sort_and_unspecified_end(self):
        data = card_data(self.snapshot(), 'dark')
        self.assertEqual(data['weekday'], '星期一')
        self.assertEqual(data['count'], 2)
        self.assertEqual(data['events'][0]['time'], '10/25 23:00 — 09:00')
        self.assertIn('结束未定', data['events'][1]['time'])

    def test_date_precision_labels_and_order(self):
        snapshot = self.snapshot()
        snapshot['events'] = [snapshot['events'][0],
                              {'id': 'b', 'title': '未定', 'start': '2026-10-26', 'end': None},
                              {'id': 'a', 'title': '跨日', 'start': '2026-10-26', 'end': '2026-10-28'}]
        data = card_data(snapshot, 'light')
        self.assertEqual([e['title'] for e in data['events']], ['跨日', '未定', '后项'])
        self.assertEqual(data['events'][0]['time'], '时间未定 — 10/28')
        self.assertEqual(data['events'][1]['time'], '时间未定 · 结束未定')

    def test_html_escapes_untrusted_title(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('render_card.subprocess.run', side_effect=OSError('no browser')):
                with self.assertRaises(OSError):
                    render(self.snapshot(), directory, chrome='/chrome')
            html = (Path(directory) / 'agenda-light.html').read_text()
            self.assertNotIn('</script><script>bad()', html)
            self.assertIn('\\u003c/script>', html)

    def run_daily(self, directory, deliver, renderer):
        argv = ['daily_summary.py', '--data', str(Path(directory) / 'events.json'), '--to', 'weixin:test']
        output = io.StringIO()
        with patch.object(sys, 'argv', argv), patch.object(daily_summary, 'deliver', deliver), patch.object(daily_summary, 'render', renderer), contextlib.redirect_stdout(output):
            code = daily_summary.main()
        return code, json.loads(output.getvalue())

    def test_text_survives_render_failure(self):
        calls = []
        def send(text, *args):
            calls.append(text)
            return {'success': True}
        def fail(*args):
            raise ValueError('render failed')
        with tempfile.TemporaryDirectory() as directory:
            code, result = self.run_daily(directory, send, fail)
        self.assertEqual(code, 1)
        self.assertTrue(result['sent'])
        self.assertEqual(len(calls), 1)
        self.assertIn('暂无安排', calls[0])

    def test_text_before_images_and_image_failure(self):
        calls = []
        def send(text, *args):
            calls.append(text)
            if text.startswith('MEDIA:'):
                raise ValueError('media failed')
            return {'success': True}
        with tempfile.TemporaryDirectory() as directory:
            code, result = self.run_daily(directory, send, lambda *args: ['/tmp/card.png'])
        self.assertEqual(code, 1)
        self.assertTrue(result['sent'])
        self.assertFalse(result['image_sent'])
        self.assertFalse(calls[0].startswith('MEDIA:'))
        self.assertEqual(calls[1], 'MEDIA:/tmp/card.png')
