import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'sieve.py'
spec = importlib.util.spec_from_file_location('sieve', SCRIPT)
sieve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sieve)


class SieveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / 'input.log'

    def cli(self, *args):
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                                capture_output=True, text=True,
                                env={**os.environ, 'CODEX_HOME': str(self.root / 'codex')})
        return result.returncode, json.loads(result.stdout)

    def test_preserves_raw_bytes_stderr_and_failure_exit(self):
        code, result = self.cli('run', '--', sys.executable, '-c',
                               'import os;os.write(1,b"hello\\x00\\xff\\n");os.write(2,b"ERROR test\\n");raise SystemExit(7)')
        self.assertEqual(code, 7)
        self.assertEqual(result['exit_code'], 7)
        raw = Path(result['file']['path'])
        self.assertEqual(raw.read_bytes(), b'hello\x00\xff\nERROR test\n')
        if os.name == 'posix':
            self.assertEqual(raw.stat().st_mode & 0o777, 0o600)
            self.assertEqual(raw.parent.stat().st_mode & 0o777, 0o700)

    def test_argv_does_not_interpret_shell(self):
        value = '$(touch SHOULD_NOT_EXIST); `echo unsafe`'
        code, result = self.cli('run', '--cwd', self.root, '--', sys.executable, '-c',
                               'import sys;print(sys.argv[1])', value)
        self.assertEqual(code, 0)
        self.assertEqual(Path(result['file']['path']).read_text().strip(), value)
        self.assertFalse((self.root / 'SHOULD_NOT_EXIST').exists())

    def test_timeout_is_not_success(self):
        code, result = self.cli('run', '--timeout', '1', '--', sys.executable, '-u', '-c',
                               'import time;print("started");time.sleep(30)')
        self.assertEqual(code, 124)
        self.assertTrue(result['timed_out'])
        self.assertIn('started', Path(result['file']['path']).read_text())

    def test_launch_failure(self):
        code, result = self.cli('run', '--', str(self.root / 'absent-command'))
        self.assertEqual(code, 127)
        self.assertTrue(result['launch_error'])

    def test_summary_keeps_middle_failure_and_final_result(self):
        self.log.write_text('start\n' + 'noise\n' * 300 + 'ERROR sentinel\n' + 'noise\n' * 300 + 'final result\n')
        result = sieve.summarize(self.log, 2400)
        text = '\n'.join(r['text'] for r in result['excerpt'])
        self.assertIn('ERROR sentinel', text)
        self.assertIn('final result', text)
        self.assertTrue(result['incomplete'])
        self.assertGreater(result['omitted_lines'], 500)

    def test_many_warnings_cannot_consume_tail_budget(self):
        self.log.write_text(''.join(f'warning {i} '+('x'*1000)+'\n' for i in range(100))+'final result\n')
        result = sieve.summarize(self.log, 2400)
        self.assertIn('final result', [r['text'] for r in result['excerpt']])
        self.assertLessEqual(sum(len(r['text']) for r in result['excerpt']), 2400)

    def test_unittest_fail_marker_is_selected_in_middle(self):
        self.log.write_text('ok\n' * 300 + 'FAIL: invariant_sentinel\n' + 'ok\n' * 300)
        result = sieve.summarize(self.log, 2400)
        self.assertIn('FAIL: invariant_sentinel', [r['text'] for r in result['excerpt']])

    def test_ansi_is_cleaned_only_in_summary(self):
        self.log.write_text('\x1b[31mERROR\x1b[0m 日本語\n')
        summary = sieve.summarize(self.log, 2400)
        self.assertEqual(summary['excerpt'][0]['text'], 'ERROR 日本語')
        read = sieve.read_range(self.log, 1, 10, None, 0, 2400)
        self.assertIn('\x1b[31m', read['excerpt'][0]['text'])

    def test_large_line_retains_line_numbers_and_marks_clipping(self):
        self.log.write_bytes(b'x'*1000000+b'\nERROR next\n')
        rows = list(sieve.bounded_lines(self.log))
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0][1]), 8192)
        self.assertTrue(rows[0][2])
        self.assertEqual(rows[1][:2], (2, 'ERROR next'))

    def test_read_range_pages_without_skipping_lines(self):
        self.log.write_text(''.join(f'line {n}\n' for n in range(1, 11)))
        first = sieve.read_range(self.log, 1, 3, None, 0, 2400)
        self.assertEqual(first['next_line'], 4)
        second = sieve.read_range(self.log, first['next_line'], 20, None, 0, 2400)
        self.assertEqual([r['line'] for r in first['excerpt']+second['excerpt']], list(range(1, 11)))
        self.assertFalse(second['has_more'])

    def test_literal_search_context_and_no_duplicates(self):
        self.log.write_text('before\n[error]\n[error]\nafter\nother\n')
        result = sieve.read_range(self.log, 1, 20, '[error]', 1, 2400)
        self.assertEqual([r['line'] for r in result['excerpt']], [1, 2, 3, 4])

    def test_character_budget_and_resume(self):
        self.log.write_text('12345\n67890\nrest\n')
        result = sieve.read_range(self.log, 1, 20, None, 0, 7)
        self.assertEqual(sum(len(r['text']) for r in result['excerpt']), 7)
        self.assertEqual(result['clipped_lines'], [2])
        self.assertTrue(result['has_more'])
        self.assertEqual(result['next_line'], 3)

    def test_empty_file_and_no_match(self):
        self.log.write_text('')
        self.assertEqual(sieve.summarize(self.log, 2400)['lines'], 0)
        self.assertFalse(sieve.summarize(self.log, 2400)['incomplete'])
        self.assertEqual(sieve.read_range(self.log, 1, 10, 'absent', 2, 2400)['excerpt'], [])

    def test_missing_file_reports_error(self):
        code, result = self.cli('summarize', self.log)
        self.assertEqual(code, 2)
        self.assertIn('error', result)

    @unittest.skipUnless(os.name == 'posix', 'POSIX signal semantics')
    def test_signal_status_preserved(self):
        code, result = self.cli('run', '--', sys.executable, '-c',
                               'import os,signal;os.kill(os.getpid(),signal.SIGTERM)')
        self.assertEqual(code, 143)
        self.assertEqual(result['exit_code'], -15)


if __name__ == '__main__':
    unittest.main()
