import concurrent.futures
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import agenda
import daily_summary


class AgendaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'events.json'

    def add(self, **kwargs):
        return agenda.execute(self.path, {'action': 'add', 'title': '会议',
                                         'start': '2026-10-26T09:00:00+08:00', **kwargs})

    def test_crud_and_exact_target(self):
        event = self.add()['event']
        self.assertEqual(agenda.execute(self.path, {'action': 'list', 'query': '会议'})['events'], [event])
        for request in ({'action': 'delete'}, {'action': 'delete', 'id': event['id'][:8]},
                        {'action': 'update', 'id': 'wrong', 'changes': {'title': 'x'}}):
            with self.assertRaises(agenda.AgendaError):
                agenda.execute(self.path, request)
        updated = agenda.execute(self.path, {'action': 'update', 'id': event['id'], 'changes': {'title': '新标题'}})
        self.assertEqual(updated['event']['title'], '新标题')
        agenda.execute(self.path, {'action': 'delete', 'id': event['id']})
        self.assertEqual(agenda.read_events(self.path), [])

    def test_notes_add_update_clear_and_summary(self):
        notes = '1. 设置 batch_size=32\n2. 验证 loss = (y_pred - y_true)^2'
        event = self.add(notes=notes)['event']
        self.assertEqual(event['notes'], notes)
        plain = self.add(title='无备注任务', start='2026-10-26T10:00:00+08:00')['event']
        self.assertIsNone(plain['notes'])
        self.assertEqual(agenda.read_events(self.path), [event, plain])

        zone = ZoneInfo('Asia/Shanghai')
        now = datetime(2026, 10, 26, 4, tzinfo=timezone.utc)
        text = agenda.summary(agenda.read_events(self.path), 'today', zone, now)
        self.assertEqual(text.count('📝'), 1)
        for line in notes.splitlines():
            self.assertIn(line, text)
        self.assertIn('• 10:00  无备注任务', text)
        self.assertNotIn('📝', text.split('• 10:00  无备注任务', 1)[1])

        replacement = '验收：accuracy >= 0.95\n参考：https://example.com/spec?id=42'
        updated = agenda.execute(self.path, {'action': 'update', 'id': event['id'],
                                            'changes': {'notes': replacement}})['event']
        self.assertEqual(updated['notes'], replacement)
        self.assertEqual(agenda.read_events(self.path)[-1]['notes'], replacement)
        cleared = agenda.execute(self.path, {'action': 'update', 'id': event['id'],
                                            'changes': {'notes': None}})['event']
        self.assertIsNone(cleared['notes'])
        self.assertIsNone(agenda.read_events(self.path)[-1]['notes'])
        self.assertNotIn('📝', agenda.summary(agenda.read_events(self.path), 'today', zone, now))

    def test_validation_conflicts_and_adjacent(self):
        for fields in ({'start': '2026-10-26T09:00:00'}, {'end': '2026-10-26T08:00:00+08:00'}, {'title': ''}):
            with self.assertRaises(agenda.AgendaError):
                self.add(**fields)
        event = self.add(end='2026-10-26T10:00:00+08:00')['event']
        before = self.path.read_bytes()
        self.assertEqual(self.add(start='2026-10-26T01:30:00Z')['error'], 'conflict')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(self.add(start='2026-10-26T10:00:00+08:00')['ok'])
        self.assertTrue(self.add(allow_conflict=True)['ok'])
        result = agenda.execute(self.path, {'action': 'update', 'id': event['id'], 'changes': {'start': '2026-10-26T08:00:00+08:00'}})
        self.assertEqual(result['error'], 'conflict')

    def test_cross_midnight_week_and_dst(self):
        self.add(start='2026-10-25T23:00:00+08:00', end='2026-10-26T01:00:00+08:00')
        events = agenda.read_events(self.path)
        zone = ZoneInfo('Asia/Shanghai')
        now = datetime(2026, 10, 26, 4, tzinfo=timezone.utc)
        selected = agenda.select(events, {'period': 'today'}, zone, now)
        self.assertEqual(len(selected), 1)
        self.assertIn('10-26 01:00', agenda.summary(selected, 'today', zone, now))
        start, end = agenda.bounds('week', zone, now)
        self.assertEqual((start.day, end.day), (26, 2))
        start, end = agenda.bounds('today', ZoneInfo('America/New_York'), datetime(2026, 3, 8, 12, tzinfo=timezone.utc))
        self.assertEqual((end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds(), 23 * 3600)

    def test_corruption_and_failed_replace_preserve_original(self):
        self.add()
        before = self.path.read_bytes()
        with patch.object(agenda.os, 'replace', side_effect=OSError('simulated')):
            with self.assertRaises(OSError):
                self.add(start='2026-10-27T09:00:00+08:00')
        self.assertEqual(self.path.read_bytes(), before)
        self.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.add()
        self.assertEqual(self.path.read_text(), '{broken')

    def test_concurrent_cli_writes(self):
        def run(index):
            return subprocess.run([sys.executable, str(SCRIPTS / 'agenda.py'), '--data', str(self.path)],
                                  input=json.dumps({'action': 'add', 'title': str(index), 'start': '2026-10-26T09:00:00Z', 'allow_conflict': True}),
                                  text=True, capture_output=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(run, range(12)))
        self.assertTrue(all(r.returncode == 0 and json.loads(r.stdout)['ok'] for r in results))
        events = agenda.read_events(self.path)
        self.assertEqual(len({e['id'] for e in events}), 12)

    def test_delivery_and_preview(self):
        with patch.object(daily_summary.subprocess, 'run') as run:
            run.return_value = subprocess.CompletedProcess([], 0, '{"success":true}', '')
            self.assertTrue(daily_summary.deliver('今日摘要', 'qqbot:example', '/bin/hermes')['success'])
            self.assertEqual(run.call_args.args[0], ['/bin/hermes', 'send', '--to', 'qqbot:example', '--file', '-', '--json'])
            self.assertEqual(run.call_args.kwargs['input'], '今日摘要')
            for response in (subprocess.CompletedProcess([], 1, '', 'failed'),
                             subprocess.CompletedProcess([], 0, '{"skipped":true}', ''),
                             subprocess.CompletedProcess([], 0, 'bad json', '')):
                run.return_value = response
                with self.assertRaises(ValueError):
                    daily_summary.deliver('text', 'target', 'hermes')
        result = subprocess.run([sys.executable, str(SCRIPTS / 'daily_summary.py'), '--data', str(self.path), '--dry-run', '--text-only', '--hermes', '/nonexistent'], capture_output=True, text=True)
        payload = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(payload['sent'])
        self.assertIn('暂无安排', payload['text'])


if __name__ == '__main__':
    unittest.main()
