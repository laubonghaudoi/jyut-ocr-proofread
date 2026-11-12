"""Process-level queue tests using sleeping children, no OCR imports or GPU."""
from __future__ import annotations

import os
import fcntl
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'plugins/jyut-ocr-proofread/skills/jyut-ocr-proofread/scripts'

CLIENT = r'''
import os, signal, subprocess, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from _gpu_queue import GPUQueue
base, label = Path(sys.argv[2]), sys.argv[3]
queue = GPUQueue(directory=base / 'queue', resource=os.environ.get('TEST_RESOURCE', 'gpu'))
def cancelled(signum, frame): raise SystemExit(128 + signum)
signal.signal(signal.SIGTERM, cancelled)
try:
    timeout = float(os.environ['TEST_TIMEOUT']) if 'TEST_TIMEOUT' in os.environ else None
    deadline = float(os.environ['TEST_DEADLINE']) if 'TEST_DEADLINE' in os.environ else None
    (base / (label + '.attempt')).touch()
    queue.acquire(timeout=timeout, phase_deadline=deadline)
    with (base / 'events').open('a') as f: f.write(label + '\n')
    (base / (label + '.acquired')).touch()
    if os.environ.get('TEST_INHERIT'):
        child = subprocess.Popen([sys.executable, '-c',
            'import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); '
            '\nwhile not p.exists(): time.sleep(0.01)',
            str(base / (label + '.release'))], pass_fds=queue.pass_fds,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        (base / (label + '.child')).write_text(str(child.pid))
    else:
        while not (base / (label + '.release')).exists(): time.sleep(0.01)
except Exception as error:
    print(type(error).__name__ + ': ' + str(error), file=sys.stderr)
    sys.exit(3)
finally:
    queue.close()
'''


@unittest.skipUnless(os.name == 'posix', 'uses POSIX advisory file locks')
class GPUQueueProcessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.processes = []
        self.child_pids = []
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
        for pid in self.child_pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def launch(self, label, **settings):
        environment = dict(os.environ)
        for key in ('TEST_RESOURCE', 'TEST_TIMEOUT', 'TEST_DEADLINE', 'TEST_INHERIT'):
            environment.pop(key, None)
        environment.update({key: str(value) for key, value in settings.items()})
        process = subprocess.Popen([sys.executable, '-c', CLIENT, str(SCRIPTS),
                                    str(self.base), label], env=environment,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.processes.append(process)
        return process

    def wait_for(self, predicate, message, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail(message)

    def acquired(self, label):
        return (self.base / (label + '.acquired')).exists()

    def wait_acquired(self, label):
        self.wait_for(lambda: self.acquired(label), f'{label} did not acquire')

    def release(self, label):
        (self.base / (label + '.release')).touch()

    def finish(self, process, expected=0, timeout=5):
        stdout, stderr = process.communicate(timeout=timeout)
        self.assertEqual(process.returncode, expected, stdout + stderr)
        return stdout, stderr

    def ticket_files(self):
        return {path for path in (self.base / 'queue').rglob('*') if path.is_file()}

    def test_three_independent_clients_receive_fifo_admission(self):
        first = self.launch('A')
        self.wait_acquired('A')
        before = self.ticket_files()
        second = self.launch('B')
        self.wait_for(lambda: len(self.ticket_files() - before) >= 1, 'B did not enroll')
        before = self.ticket_files()
        third = self.launch('C')
        self.wait_for(lambda: len(self.ticket_files() - before) >= 1, 'C did not enroll')
        self.assertFalse(self.acquired('B'))
        self.assertFalse(self.acquired('C'))
        self.release('A')
        self.finish(first)
        self.wait_acquired('B')
        self.assertFalse(self.acquired('C'))
        self.release('B')
        self.finish(second)
        self.wait_acquired('C')
        self.release('C')
        self.finish(third)
        self.assertEqual((self.base / 'events').read_text().splitlines(), ['A', 'B', 'C'])

    def test_cancelled_waiter_is_swept_and_does_not_block_following_client(self):
        first = self.launch('A')
        self.wait_acquired('A')
        before = self.ticket_files()
        cancelled = self.launch('B')
        self.wait_for(lambda: len(self.ticket_files() - before) >= 1, 'B did not enroll')
        cancelled.kill()
        cancelled.communicate(timeout=5)
        third = self.launch('C')
        self.release('A')
        self.finish(first)
        self.wait_acquired('C')
        self.release('C')
        self.finish(third)
        self.assertEqual((self.base / 'events').read_text().splitlines(), ['A', 'C'])

    def test_inherited_lease_survives_parent_close_until_child_exits(self):
        parent = self.launch('A', TEST_INHERIT=1)
        self.wait_acquired('A')
        self.finish(parent)
        self.child_pids.append(int((self.base / 'A.child').read_text()))
        failed = self.launch('immediate', TEST_TIMEOUT=0)
        self.finish(failed, 3)
        self.assertFalse(self.acquired('immediate'))
        following = self.launch('B')
        self.release('A')
        self.wait_acquired('B')
        self.release('B')
        self.finish(following)
        self.assertEqual((self.base / 'events').read_text().splitlines(), ['A', 'B'])

    def test_owner_abrupt_death_releases_uninherited_lease(self):
        first = self.launch('A')
        self.wait_acquired('A')
        first.kill()
        first.communicate(timeout=5)
        following = self.launch('B')
        self.wait_acquired('B')
        self.release('B')
        self.finish(following)

    def test_zero_timeout_allows_free_queue_and_busy_queue_fails(self):
        first = self.launch('A', TEST_TIMEOUT=0)
        self.wait_acquired('A')
        failed = self.launch('B', TEST_TIMEOUT=0)
        self.finish(failed, 3)
        self.assertFalse(self.acquired('B'))
        self.release('A')
        self.finish(first)
        following = self.launch('C', TEST_TIMEOUT=0)
        self.wait_acquired('C')
        self.release('C')
        self.finish(following)

    def test_phase_deadline_limits_wait_without_retaining_ticket(self):
        first = self.launch('A')
        self.wait_acquired('A')
        expired = self.launch('B', TEST_DEADLINE=time.time() + 0.2)
        self.finish(expired, 3)
        self.assertFalse(self.acquired('B'))
        self.release('A')
        self.finish(first)
        following = self.launch('C', TEST_TIMEOUT=0)
        self.wait_acquired('C')
        self.release('C')
        self.finish(following)

    def test_explicitly_different_gpu_resources_do_not_block_each_other(self):
        first = self.launch('A', TEST_RESOURCE='gpu-one')
        self.wait_acquired('A')
        second = self.launch('B', TEST_RESOURCE='gpu-two', TEST_TIMEOUT=0)
        self.wait_acquired('B')
        self.release('A')
        self.release('B')
        self.finish(first)
        self.finish(second)

    def test_busy_registry_guard_obeys_timeout_and_phase_deadline(self):
        first = self.launch('A')
        self.wait_acquired('A')
        guard_path = next((self.base / 'queue').rglob('guard.lock'))
        with guard_path.open('r+') as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            for label, settings in (
                    ('immediate', {'TEST_TIMEOUT': 0}),
                    ('timed', {'TEST_TIMEOUT': 0.1}),
                    ('deadline', {'TEST_DEADLINE': time.time() + 0.5})):
                with self.subTest(label=label):
                    process = self.launch(label, **settings)
                    _, stderr = self.finish(process, 3, timeout=2)
                    self.assertIn('GPUQueueTimeout', stderr)
                    self.assertFalse(self.acquired(label))
        self.release('A')
        self.finish(first)
        following = self.launch('B', TEST_TIMEOUT=0)
        self.wait_acquired('B')
        self.release('B')
        self.finish(following)

    def test_cancelled_waiter_and_owner_close_do_not_block_on_registry_guard(self):
        first = self.launch('A')
        self.wait_acquired('A')
        before = self.ticket_files()
        waiter = self.launch('B')
        self.wait_for(lambda: len(self.ticket_files() - before) >= 1, 'B did not enroll')
        guard_path = next((self.base / 'queue').rglob('guard.lock'))
        with guard_path.open('r+') as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            waiter.terminate()
            self.finish(waiter, 143, timeout=2)
            self.release('A')
            self.finish(first, timeout=2)
        # Cleanup deferred to the next operation must sweep both unlocked tickets.
        following = self.launch('C', TEST_TIMEOUT=0)
        self.wait_acquired('C')
        self.release('C')
        self.finish(following)
        self.assertEqual((self.base / 'events').read_text().splitlines(), ['A', 'C'])


if __name__ == '__main__':
    unittest.main()
