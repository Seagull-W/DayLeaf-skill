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

    def test_date_only_storage_order_summary_and_conflicts(self):
        timed = self.add()['event']
        result = self.add(title='待安排', start='2026-10-26', notes='保留备注')
        self.assertTrue(result['ok'])
        self.assertEqual(result['conflicts'], [])
        event = result['event']
        self.assertEqual(event['start'], '2026-10-26')
        self.assertEqual(set(event), {'id', 'title', 'start', 'end', 'notes', 'done', 'due'})
        self.assertEqual(agenda.read_events(self.path), [timed, event])
        self.assertEqual(json.loads(self.path.read_text())['version'], 1)
        selected = agenda.execute(self.path, {'action': 'list', 'from': '2026-10-26',
                                             'to': '2026-10-27'})['events']
        self.assertEqual(selected, [event, timed])
        zone = ZoneInfo('Asia/Shanghai')
        now = datetime(2026, 10, 26, 4, tzinfo=timezone.utc)
        text = agenda.summary([event], 'today', zone, now)
        self.assertIn('🗓 10-26', text)
        self.assertIn('• 时间未定  待安排', text)
        self.assertIn('📝 保留备注', text)
        self.assertNotRegex(text, r'\d{2}:\d{2}')
        self.assertFalse(agenda.overlaps(event, timed, zone))
        self.assertFalse(agenda.overlaps(timed, event, zone))

    def test_deadline_roundtrip_and_clear(self):
        for due in ('2026-10-27T09:00:00+08:00', None, '2026-10-27'):
            with self.subTest(due=due):
                fields = {} if due is None else {'due': due}
                event = self.add(start='2026-10-26', **fields)['event']
                self.assertEqual(event['due'], due)
                self.assertEqual(agenda.read_events(self.path)[-1], event)
                listed = agenda.execute(self.path, {'action': 'list'})['events']
                self.assertEqual(next(e for e in listed if e['id'] == event['id'])['due'], due)
        updated = agenda.execute(self.path, {'action': 'update', 'id': event['id'],
                                            'changes': {'due': '2026-10-28T09:00:00+08:00'}})['event']
        self.assertEqual(updated['due'], '2026-10-28T09:00:00+08:00')
        cleared = agenda.execute(self.path, {'action': 'update', 'id': event['id'],
                                            'changes': {'due': None}})['event']
        self.assertIsNone(cleared['due'])
        self.assertEqual(agenda.read_events(self.path)[-1], cleared)

    def test_invalid_deadlines_and_ordering_preserve_file(self):
        event = self.add(start='2026-10-26', due='2026-10-27')['event']
        before = self.path.read_bytes()
        for due in (1, '', '2026-10-27T09:00:00', '2026-13-01',
                    '2026-10-25', '2026-10-25T23:59:59+08:00'):
            with self.subTest(due=due):
                with self.assertRaises(agenda.AgendaError):
                    self.add(start='2026-10-26', due=due)
                with self.assertRaises(agenda.AgendaError):
                    agenda.execute(self.path, {'action': 'update', 'id': event['id'],
                                               'changes': {'due': due}})
                self.assertEqual(self.path.read_bytes(), before)
        with self.assertRaisesRegex(agenda.AgendaError, '截止时间不得早于'):
            agenda.execute(self.path, {'action': 'update', 'id': event['id'],
                                       'changes': {'start': '2026-10-28'}})
        with self.assertRaises(agenda.AgendaError):
            self.add(start='2026-10-26T09:00:00+08:00', due='2026-10-26')
        for start, due in (('2026-10-26', '2026-10-26'),
                           ('2026-10-26', '2026-10-26T00:00:00+08:00'),
                           ('2026-10-26T00:00:00+08:00', '2026-10-26'),
                           ('2026-10-26T09:00:00+08:00', '2026-10-26T01:00:00Z')):
            with self.subTest(start=start, due=due):
                self.assertTrue(self.add(start=start, due=due, allow_conflict=True)['ok'])

    def test_deadline_request_zone_and_no_conflict(self):
        event = agenda.execute(self.path, {'action': 'add', 'title': '作业',
                                           'start': '2026-10-26',
                                           'due': '2026-10-26T00:00:00+08:00',
                                           'timezone': 'Asia/Shanghai'})['event']
        # The same instant precedes a date anchor in UTC, so request zone matters.
        with self.assertRaisesRegex(agenda.AgendaError, '截止时间不得早于'):
            agenda.execute(self.path, {'action': 'add', 'title': 'UTC 作业',
                                       'start': event['start'], 'due': event['due'],
                                       'timezone': 'UTC'})
        meeting = self.add(start=event['due'])
        self.assertTrue(meeting['ok'])
        self.assertEqual(meeting['conflicts'], [])

    def test_stored_deadline_ordering_without_request_zone(self):
        # A local midnight is still the previous UTC day; both mixed forms load.
        for start, due in (('2026-10-26', '2026-10-26T00:00:00+08:00'),
                           ('2026-10-26T00:00:00-08:00', '2026-10-26')):
            event = {'id': 'one', 'title': '任务', 'start': start, 'end': None,
                     'notes': None, 'due': due}
            self.path.write_text(json.dumps({'version': 1, 'events': [event]}))
            self.assertEqual(agenda.read_events(self.path)[0]['due'], due)
        for start, due in (('2026-10-28', '2026-10-26'),
                           ('2026-10-28', '2026-10-26T09:00:00+08:00'),
                           ('2026-10-28T09:00:00+08:00', '2026-10-26'),
                           ('2026-10-26T09:00:00+08:00', '2026-10-26T08:00:00+08:00')):
            with self.subTest(start=start, due=due):
                event.update(start=start, due=due)
                self.path.write_text(json.dumps({'version': 1, 'events': [event]}))
                with self.assertRaisesRegex(agenda.AgendaError, '截止时间不得早于'):
                    agenda.read_events(self.path)

    def test_due_before_filters_and_pending_reminders(self):
        due = '2026-10-27T09:00:00+08:00'
        pending = self.add(start='2026-10-26', due=due)['event']
        done = self.add(start='2026-10-26', due=due, done=True)['event']
        dated = self.add(start='2026-10-26', due='2026-10-27')['event']
        self.add(start='2026-10-26')
        self.add(start='2026-10-26', due='2026-10-27T09:00:01+08:00')
        zone = ZoneInfo('Asia/Shanghai')
        now = datetime(2026, 10, 26, 4, tzinfo=timezone.utc)
        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz)
        for action in ('list', 'summary'):
            for status, expected in (('pending', [pending, dated]), ('all', [pending, done, dated])):
                request = {'action': action, 'due_before': due}
                if status == 'pending':
                    request['status'] = status
                with self.subTest(action=action, status=status), patch.object(agenda, 'datetime', FixedDatetime):
                    selected = agenda.execute(self.path, request)['events']
                    self.assertEqual({e['id'] for e in selected}, {e['id'] for e in expected})
        self.assertEqual(agenda.select(agenda.read_events(self.path), {'due_before': '2026-10-27'}, zone, now), [dated])
        for value in (None, 1, '', '2026-10-27T09:00:00', '2026-13-01'):
            with self.subTest(value=value), self.assertRaises(agenda.AgendaError):
                agenda.execute(self.path, {'action': 'list', 'due_before': value})

    def test_summary_deadline_labels(self):
        events = [self.add(title='当天', start='2026-10-26', due='2026-10-26T01:00:00Z')['event'],
                  self.add(title='经济学作业', start='2026-10-26', due='2026-10-27T09:00:00+08:00', done=True)['event'],
                  self.add(title='日期', start='2026-10-26', due='2026-10-27')['event']]
        text = agenda.summary(events, 'today', ZoneInfo('Asia/Shanghai'),
                              datetime(2026, 10, 26, 4, tzinfo=timezone.utc))
        self.assertIn('• 时间未定  当天  ⏰ 截止 09:00', text)
        self.assertIn('• 时间未定  ✅ 经济学作业  ⏰ 截止 10-27 09:00', text)
        self.assertIn('• 时间未定  日期  ⏰ 截止 10-27', text.splitlines())

    def test_done_validation_roundtrip_and_toggle(self):
        event = self.add(notes='操作细节')['event']
        self.assertIs(event['done'], False)
        self.add(start='2026-10-27', done=True)
        self.assertIs(agenda.read_events(self.path)[1]['done'], True)
        for value in (1, 0, 'true', None):
            with self.subTest(value=value), self.assertRaises(agenda.AgendaError):
                self.add(start='2026-10-28', done=value)
            with self.assertRaises(agenda.AgendaError):
                agenda.execute(self.path, {'action': 'update', 'id': event['id'],
                                           'changes': {'done': value}})
        for done in (True, False):
            updated = agenda.execute(self.path, {'action': 'update', 'id': event['id'],
                                                'changes': {'done': done}})['event']
            self.assertEqual(updated, {**event, 'done': done})
            self.assertEqual(next(e for e in agenda.read_events(self.path) if e['id'] == event['id']), updated)

    def test_legacy_done_normalization_without_read_write(self):
        event = self.add()['event']
        legacy = {key: value for key, value in event.items() if key not in ('done', 'due')}
        self.path.write_text(json.dumps({'version': 1, 'events': [legacy]}))
        before = self.path.read_bytes()
        agenda.validate_event(legacy)
        self.assertEqual(agenda.read_events(self.path), [event])
        self.assertEqual(agenda.execute(self.path, {'action': 'list'})['events'], [event])
        self.assertEqual(self.path.read_bytes(), before)
        self.add(start='2026-10-27')
        stored = json.loads(self.path.read_text())
        self.assertEqual(stored['version'], 1)
        self.assertTrue(all(e['done'] is False for e in stored['events']))
        self.assertTrue(all(e['due'] is None for e in stored['events']))

    def test_status_filters_and_completed_summary(self):
        pending = self.add(title='待处理', start='2026-10-26')['event']
        dated = self.add(title='健身', start='2026-10-26', done=True)['event']
        timed = self.add(title='精读论文', done=True, notes='保留备注')['event']
        all_events = [pending, dated, timed]
        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 26, 4, tzinfo=timezone.utc).astimezone(tz)
        for status, expected in (('pending', [pending]), ('done', [dated, timed]), ('all', all_events)):
            for action in ('list', 'summary'):
                with self.subTest(status=status, action=action), patch.object(agenda, 'datetime', FixedDatetime):
                    result = agenda.execute(self.path, {'action': action, 'period': 'today', 'status': status})
                    self.assertEqual({e['id'] for e in result['events']}, {e['id'] for e in expected})
        self.assertEqual(len(agenda.execute(self.path, {'action': 'list'})['events']), 3)
        for action in ('list', 'summary'):
            with self.assertRaisesRegex(agenda.AgendaError, 'status 只支持'):
                agenda.execute(self.path, {'action': action, 'status': 'unknown'})
        text = agenda.summary(all_events, 'today', ZoneInfo('Asia/Shanghai'),
                              datetime(2026, 10, 26, 4, tzinfo=timezone.utc))
        self.assertIn('• 时间未定  待处理', text)
        self.assertIn('• 时间未定  ✅ 健身', text)
        self.assertIn('• 09:00  ✅ 精读论文', text)
        self.assertEqual(text.count('✅'), 2)
        self.assertIn('📝 保留备注', text)

    def test_omitted_start_uses_local_registration_date(self):
        # Fixed near UTC midnight, with zones on both sides of the date line.
        fixed = datetime(2026, 10, 26, 23, 59, 59, tzinfo=timezone.utc)
        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz)
        with patch.object(agenda, 'datetime', FixedDatetime):
            for zone in ('Pacific/Kiritimati', 'America/Adak', 'Asia/Shanghai'):
                with self.subTest(zone=zone):
                    expected = FixedDatetime.now(ZoneInfo(zone)).date().isoformat()
                    result = agenda.execute(self.path, {'action': 'add', 'title': '登记', 'timezone': zone})
                    self.assertTrue(result['ok'])
                    self.assertEqual(result['event']['start'], expected)
            default = agenda.execute(self.path, {'action': 'add', 'title': '默认时区'})
            self.assertEqual(default['event']['start'], FixedDatetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat())

    def test_date_precision_validation_and_multiday_selection(self):
        for fields in (
            {'start': '2026-13-01'}, {'start': '2026-02-30'}, {'start': '20261026'},
            {'start': '2026-10-26T09:00:00'}, {'start': None},
            {'start': '2026-10-26', 'end': '2026-10-27T09:00:00+08:00'},
            {'end': '2026-10-27'},
            {'start': '2026-10-26', 'end': '2026-10-26'},
            {'start': '2026-10-26', 'end': '2026-10-25'},
        ):
            with self.subTest(fields=fields), self.assertRaises(agenda.AgendaError):
                self.add(**fields)
        event = self.add(start='2026-10-26', end='2026-10-28')['event']
        for day, expected in (('2026-10-27', [event]), ('2026-10-28', [])):
            selected = agenda.execute(self.path, {'action': 'list', 'from': day})['events']
            self.assertEqual(selected, expected)

    def test_update_time_precision_and_merge_validation(self):
        event = self.add(end='2026-10-26T10:00:00+08:00')['event']
        def update(changes):
            return agenda.execute(self.path, {'action': 'update', 'id': event['id'], 'changes': changes})
        before = self.path.read_bytes()
        with self.assertRaises(agenda.AgendaError):
            update({'start': '2026-10-26'})
        self.assertEqual(self.path.read_bytes(), before)
        result = update({'start': '2026-10-26', 'end': None})
        self.assertTrue(result['ok'])
        self.assertEqual(result['event']['start'], '2026-10-26')
        result = update({'start': '2026-10-26T11:00:00+08:00', 'end': '2026-10-26T12:00:00+08:00'})
        self.assertTrue(result['ok'])
        self.assertEqual(agenda.read_events(self.path), [result['event']])
        with self.assertRaises(agenda.AgendaError):
            update({'start': None})

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
