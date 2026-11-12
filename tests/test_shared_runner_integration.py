"""Independent multi-process runner checks; fake models and HTTP only, no GPU."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest

from test_local_ocr_runners import FAKE_SERVER, LocalOCRTestBase, SCRIPTS


class SharedRunnerIntegrationTest(LocalOCRTestBase):
    def setUp(self):
        super().setUp()
        # Freeze the runtime so edits by another developer cannot change a
        # broker's code identity halfway through these process-level checks.
        self.scripts = self.base / 'scripts'
        self.scripts.mkdir()
        for name in ('run_hunyuanocr.py', '_hunyuan_shared.py', '_gpu_queue.py', '_phase_budget.py'):
            shutil.copy2(SCRIPTS / name, self.scripts / name)
        fake = FAKE_SERVER.replace('BaseHTTPRequestHandler, HTTPServer',
                                   'BaseHTTPRequestHandler, ThreadingHTTPServer')
        fake = fake.replace("HTTPServer(('127.0.0.1', a.port), Handler)",
                            "ThreadingHTTPServer(('127.0.0.1', a.port), Handler)")
        fake = fake.replace("record('page:' + str(page))",
                            "record('page:' + str(page)); record('begin:' + str(page)); time.sleep(0.12)")
        fake = fake.replace("self.reply({'model':", "record('end:' + str(page))\n        self.reply({'model':")
        fake = fake.replace("'content': '  輙與弑\\n\\n[原書符號]  \\n'",
                            "'content': 'page payload ' + str(page)")
        self.binary.write_text('#!' + sys.executable + '\n' + fake)
        self.processes = []
        self.addCleanup(self.cleanup_children)
        self.env['OCR_HUNYUAN_MODE'] = 'shared'
        self.env['OCR_HUNYUAN_SLOTS'] = '4'

    def cleanup_children(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        # All membership EOFs must permit the owned fake backend to exit. The
        # temporary files must outlive that shutdown, including its final log.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            events = self.events()
            if events.count('started') == events.count('stopped'):
                return
            time.sleep(0.05)

    def launch(self, label, *, extra=(), env=None, pages=(1, 2, 3), output=None):
        renders = self.base / ('renders-' + label)
        renders.mkdir(exist_ok=True)
        for page in pages:
            (renders / f'page-{page:04d}.png').write_text(f'scan {page}')
        command = [sys.executable, str(self.scripts / 'run_hunyuanocr.py'),
                   str(renders), str(output or self.base / ('draft-' + label)),
                   '--model', str(self.model), '--mmproj', str(self.proj),
                   '--llama-server', str(self.binary), '--server-mode', 'shared',
                   '--server-slots', '4', '--workers', '2',
                   '--report', str(self.base / ('report-' + label + '.json')), *extra]
        process = subprocess.Popen(command, env=dict(self.env, **(env or {})),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(process)
        return process

    def finish(self, process, expected=0):
        stdout, stderr = process.communicate(timeout=15)
        self.assertEqual(process.returncode, expected, stdout + stderr)
        self.assertNotIn('Traceback', stderr, stderr)

    def wait_event(self, event):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if event in self.events():
                return
            time.sleep(0.02)
        self.fail(f'missing {event}: {self.events()}')

    def wait_stopped(self):
        self.wait_event('stopped')

    def report_for(self, label):
        return json.loads((self.base / ('report-' + label + '.json')).read_text())

    def test_independent_documents_share_one_backend_and_keep_page_outputs_separate(self):
        first = self.launch('a', pages=(1, 2, 3))
        second = self.launch('b', pages=(4, 5, 6))
        self.finish(first)
        self.finish(second)
        self.wait_stopped()
        events = self.events()
        self.assertEqual(events.count('started'), 1, events)
        busy = peak = 0
        for event in events:
            if event.startswith('begin:'):
                busy += 1
                peak = max(peak, busy)
            elif event.startswith('end:'):
                busy -= 1
        self.assertGreater(peak, 1, events)
        self.assertLessEqual(peak, 4, events)
        for label, pages in (('a', (1, 2, 3)), ('b', (4, 5, 6))):
            report = self.report_for(label)
            self.assertEqual(report['counts']['completed'], 3)
            self.assertEqual(report['ocrProfile']['deployment']['contextPerSlot'], 16384)
            self.assertEqual(report['ocrProfile']['deployment']['totalContext'], 65536)
            self.assertEqual(report['gpuQueue']['slots'], 4)
            for page in pages:
                record = json.loads((self.base / f'draft-{label}/page-{page:04d}.json').read_text())
                self.assertEqual(record['scanPage'], page)
                self.assertEqual(record['rawText'], f'page payload {page}')
                self.assertIn(f'renders-{label}', record['renderFile'])
            # Current outputs bypass the shared broker entirely after shutdown.
            self.finish(self.launch(label, pages=pages))
            self.assertEqual(self.report_for(label)['counts']['skippedCurrent'], 3)
        self.assertEqual(self.events().count('started'), 1)

    def test_killing_first_runner_does_not_kill_other_membership(self):
        gate = self.base / 'gate'
        first = self.launch('first', pages=(1,), env={'FAKE_RELEASE_FILE': str(gate)})
        self.wait_event('page:1')
        second = self.launch('second', pages=(2,))
        self.wait_event('page:2')
        first.kill()
        first.communicate(timeout=5)
        self.assertIsNone(second.poll())
        self.assertNotIn('stopped', self.events())
        gate.touch()
        self.finish(second)
        self.wait_stopped()
        self.assertFalse((self.base / 'draft-first/page-0001.json').exists())
        self.assertEqual(self.report_for('second')['counts']['completed'], 1)
        self.assertEqual(self.events().count('started'), 1)

    def test_deadline_while_waiting_for_gpu_defers_without_starting_backend(self):
        helper = """
import sys,time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from _gpu_queue import GPUQueue
q=GPUQueue(sys.argv[2], 'offline-test-gpu');q.acquire()
Path(sys.argv[3]).touch()
time.sleep(30)
"""
        ready = self.base / 'held'
        holder = subprocess.Popen([sys.executable, '-c', helper, str(self.scripts),
                                   str(self.queue_dir), str(ready)], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
        self.processes.append(holder)
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(ready.exists())
        process = self.launch('deadline', pages=(1,),
                              env={'OCR_PHASE_DEADLINE': str(time.time() + 0.4)})
        self.finish(process, 75)
        report = self.report_for('deadline')
        self.assertEqual(report['counts']['deferred'], 1)
        self.assertEqual(report['counts']['failed'], 0)
        self.assertNotIn('started', self.events())
        self.assertFalse((self.base / 'draft-deadline/page-0001.json').exists())
        holder.kill()
        holder.communicate(timeout=5)
        # Releasing the queued resource during the idle grace must not load a
        # model for a membership whose phase deadline has already expired.
        time.sleep(0.7)
        self.assertNotIn('started', self.events())

    def test_parallel_truncated_page_is_not_sealed_and_other_pages_complete(self):
        process = self.launch('truncated', env={'FAKE_BAD_PAGE': '2'})
        self.finish(process, 1)
        self.wait_stopped()
        report = self.report_for('truncated')
        self.assertEqual(report['counts']['completed'], 2)
        self.assertEqual(report['counts']['failed'], 1)
        self.assertFalse((self.base / 'draft-truncated/page-0002.json').exists())
        self.assertTrue((self.base / 'draft-truncated/page-0001.json').is_file())
        self.assertTrue((self.base / 'draft-truncated/page-0003.json').is_file())

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux parent-death semantics')
    def test_parent_bound_exec_dies_with_parent_without_preexec_callback(self):
        child_pid = self.base / 'child.pid'
        helper = """
import subprocess,sys,time
sys.path.insert(0, sys.argv[1])
from _phase_budget import parent_bound_command
code = 'import os,time;from pathlib import Path;Path(' + repr(sys.argv[2]) + ').write_text(str(os.getpid()));time.sleep(30)'
subprocess.Popen(parent_bound_command([sys.executable, '-c', code]))
time.sleep(30)
"""
        parent = subprocess.Popen([sys.executable, '-c', helper, str(self.scripts), str(child_pid)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(parent)
        deadline = time.monotonic() + 5
        while not child_pid.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(child_pid.exists())
        pid = int(child_pid.read_text())
        parent.kill()
        parent.communicate(timeout=5)
        stat_path = Path('/proc') / str(pid) / 'stat'
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not stat_path.exists() or stat_path.read_text().split(') ', 1)[1].startswith('Z '):
                break
            time.sleep(0.01)
        else:
            self.fail('parent-bound child survived parent SIGKILL')


if __name__ == '__main__':
    unittest.main()
