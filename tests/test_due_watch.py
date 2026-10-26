import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import agenda
import due_watch


class DueWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / 'events.json'
        self.state = self.data.parent / 'due-reminders.json'

    def save(self, due='2026-10-27T09:00:00+08:00', **changes):
        event = dict(id='homework', title='经济学作业', start='2026-10-26',
                     end=None, notes=None, done=False, due=due)
        event.update(changes)
        agenda.write_events(self.data, [event])
        return event

    def run_watch(self, *extra, now='2026-10-27T07:40:00+08:00'):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = due_watch.main(['--data', str(self.data), '--now', now, *extra])
        self.assertEqual(code, 0, errors.getvalue())
        self.assertEqual(errors.getvalue(), '')
        return output.getvalue()

    def test_missing_and_nothing_due(self):
        self.assertEqual(self.run_watch(), '')
        self.save(due=None)
        self.assertEqual(self.run_watch(), '')

    def test_within_window_and_dedupe(self):
        event = self.save()
        self.assertEqual(self.run_watch(),
                         '⏰ 截止提醒\n• 经济学作业 · 截止 10-27 09:00 · 还有 1 小时 20 分\n')
        state = json.loads(self.state.read_text())
        self.assertEqual(state, {'version': 1, 'sent': {
            event['id'] + '@' + event['due']: '2026-10-26T23:40:00+00:00'}})
        self.assertEqual(self.run_watch(), '')
        self.assertEqual(self.run_watch('--dry-run'), '')

    def test_window_and_grace_boundaries(self):
        self.save()
        for now, expected in [
                ('2026-10-27T06:59:59+08:00', ''),
                ('2026-10-27T07:00:00+08:00', '还有 2 小时 0 分'),
                ('2026-10-27T09:00:00+08:00', '已过期 0 分'),
                ('2026-10-27T09:35:00+08:00', '已过期 35 分'),
                ('2026-10-27T21:00:00+08:00', ''),
                ('2026-10-28T09:00:00+08:00', '')]:
            with self.subTest(now=now):
                text = self.run_watch('--dry-run', now=now)
                if expected:
                    self.assertIn(expected, text)
                else:
                    self.assertEqual(text, '')

    def test_done_skipped(self):
        self.save(done=True)
        self.assertEqual(self.run_watch(), '')

    def test_date_only(self):
        self.save(due='2026-10-27')
        self.assertEqual(self.run_watch('--dry-run', now='2026-10-26T23:00:00+08:00'),
                         '⏰ 截止提醒\n• 经济学作业 · 截止 10-27\n')
        self.assertEqual(self.run_watch('--dry-run', now='2026-10-27T00:35:00+08:00'),
                         '⏰ 截止提醒\n• 经济学作业 · 截止 10-27 · 已过期\n')

    def test_dry_run_does_not_touch_state(self):
        self.save()
        self.assertIn('经济学作业', self.run_watch('--dry-run'))
        self.assertFalse(self.state.exists())
        self.assertFalse(Path(str(self.state) + '.lock').exists())
        self.assertIn('经济学作业', self.run_watch())
        before = self.state.read_bytes()
        self.save(due='2026-10-27T09:10:00+08:00')
        self.assertIn('经济学作业', self.run_watch('--dry-run'))
        self.assertEqual(self.state.read_bytes(), before)
        self.assertIn('经济学作业', self.run_watch())

    def test_changed_due_announces_again_and_keeps_old_key(self):
        original = self.save()
        self.run_watch()
        self.save(due='2026-10-27T09:10:00+08:00')
        self.assertIn('09:10', self.run_watch())
        self.save(due=original['due'])
        self.assertEqual(self.run_watch(), '')
        self.assertEqual(len(json.loads(self.state.read_text())['sent']), 2)

    def test_prune_deleted_and_completed(self):
        for completed in (False, True):
            with self.subTest(completed=completed):
                self.save()
                self.run_watch()
                if completed:
                    self.save(done=True)
                else:
                    self.data.unlink()
                self.assertEqual(self.run_watch(), '')
                self.assertEqual(json.loads(self.state.read_text())['sent'], {})

    def test_options_timezone_and_verbatim_title(self):
        title = '  原始标题\t' + '长' * 250
        self.save(title=title)
        self.assertEqual(self.run_watch('--dry-run', '--window-minutes', '60'), '')
        text = self.run_watch('--dry-run', '--timezone', 'UTC')
        self.assertIn(title, text)
        self.assertIn('截止 10-27 01:00', text)
        self.assertEqual(self.run_watch('--dry-run', '--grace-minutes', '30',
                                        now='2026-10-27T09:35:00+08:00'), '')
        custom = self.data.parent / 'nested' / 'state.json'
        self.assertIn(title, self.run_watch('--state', str(custom)))
        self.assertTrue(custom.exists())

    def test_cli_now_and_errors(self):
        self.save()
        command = [sys.executable, str(SCRIPTS / 'due_watch.py'), '--data', str(self.data)]
        result = subprocess.run(command + ['--now', '2026-10-27T06:00:00+08:00'],
                                capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))
        result = subprocess.run(command + ['--now', '2026-10-27T07:40:00+08:00'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('还有 1 小时 20 分', result.stdout)
        for invalid in ('not-json', '{"version": 1, "sent": []}'):
            self.state.write_text(invalid)
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
            self.assertIn('due_watch:', result.stderr)
            self.assertEqual(self.state.read_text(), invalid)


if __name__ == '__main__':
    unittest.main()
