# SPDX-License-Identifier: Apache-2.0
"""Deterministic lifecycle races without connecting to an audio server."""
import array
import os
from pathlib import Path
import signal
import subprocess
import threading
import tempfile
import sys
import unittest
from unittest.mock import patch

from eqxear.spectrum import Analyzer
from eqxear.tone import Tone


def reached(event):
    if not event.wait(3):
        raise AssertionError('Worker did not reach its synchronization point')


class Pipe:
    def __init__(self, process):
        self.process = process
        self.entered = threading.Event()
        self.closed = threading.Event()
        self.writes = []

    def read(self, size):
        self.entered.set()
        reached(self.process.exited)
        return b''

    def write(self, data):
        self.writes.append((threading.current_thread().name, data))
        self.entered.set()
        reached(self.process.exited)
        raise BrokenPipeError('stopped')

    def flush(self):
        pass

    def close(self):
        self.closed.set()


class Process:
    def __init__(self, ignore_terminate=False):
        self.exited = threading.Event()
        self.terminated = threading.Event()
        self.killed = threading.Event()
        self.ignore_terminate = ignore_terminate
        self.stdout = Pipe(self)
        self.stdin = Pipe(self)

    def poll(self):
        return 0 if self.exited.is_set() else None

    def terminate(self):
        self.terminated.set()
        if not self.ignore_terminate:
            self.exited.set()

    def kill(self):
        self.killed.set()
        self.exited.set()

    def wait(self, timeout=None):
        if self.ignore_terminate and not self.exited.is_set():
            raise subprocess.TimeoutExpired('fake', timeout)
        reached(self.exited)
        return 0


class FakeSpectrum:
    size = 8192

    def analyze(self, samples):
        return (-20.,)*31

    def close(self):
        pass


class AudioLifecycleTests(unittest.TestCase):
    def test_analyzer_stop_during_spawn_reaps_unpublished_child(self):
        analyzer = Analyzer()
        entered, release = threading.Event(), threading.Event()
        process = Process()
        def spawn(*args, **kwargs):
            entered.set(); reached(release)
            return process
        with patch('eqxear.spectrum.Spectrum', FakeSpectrum), patch('eqxear.spectrum.subprocess.Popen', spawn):
            analyzer.set_source('first.monitor'); reached(entered)
            analyzer.stop(); release.set(); reached(process.stdout.closed)
        self.assertIsNone(analyzer.process)
        self.assertIsNone(analyzer.source)
        self.assertTrue(process.terminated.is_set())
        self.assertEqual(analyzer.message, 'Analyzer idle')

    def test_analyzer_stale_spawn_cannot_replace_new_child(self):
        analyzer = Analyzer()
        entered, release = threading.Event(), threading.Event()
        old, new = Process(), Process()
        def spawn(args, **kwargs):
            if '--device=old.monitor' in args:
                entered.set(); reached(release); return old
            return new
        with patch('eqxear.spectrum.Spectrum', FakeSpectrum), patch('eqxear.spectrum.subprocess.Popen', spawn):
            analyzer.set_source('old.monitor'); reached(entered)
            analyzer.set_source('new.monitor'); reached(new.stdout.entered)
            release.set(); reached(old.stdout.closed)
            self.assertIs(analyzer.process, new)
            self.assertEqual(analyzer.source, 'new.monitor')
            analyzer.stop(); reached(new.stdout.closed)

    def test_analyzer_stale_analysis_cannot_publish_levels(self):
        analyzer = Analyzer()
        analyzing, release = threading.Event(), threading.Event()
        old, new = Process(), Process()
        old.stdout.read = lambda size: array.array('f', [0.]*(size//4)).tobytes()
        class HeldSpectrum(FakeSpectrum):
            def analyze(self, samples):
                analyzing.set(); reached(release)
                return (-1.,)*31
        with patch('eqxear.spectrum.Spectrum', HeldSpectrum), patch('eqxear.spectrum.subprocess.Popen', side_effect=[old,new]):
            analyzer.set_source('old.monitor'); reached(analyzing)
            analyzer.set_source('new.monitor'); reached(new.stdout.entered)
            release.set(); reached(old.stdout.closed)
            self.assertEqual(analyzer.levels, (-90.,)*31)
            self.assertEqual(analyzer.message, 'Listening to output…')
            analyzer.stop(); reached(new.stdout.closed)

    def test_analyzer_failure_cooldown_is_specific_to_device(self):
        analyzer = Analyzer()
        old, new = Process(), Process()
        old.exited.set()
        with patch('eqxear.spectrum.Spectrum', FakeSpectrum), patch('eqxear.spectrum.subprocess.Popen', side_effect=[old,new]) as spawn:
            analyzer.set_source('old.monitor'); reached(old.stdout.closed)
            self.assertIn('RTA unavailable', analyzer.message)
            analyzer.set_source('old.monitor')
            self.assertEqual(spawn.call_count, 1)
            analyzer.set_source('new.monitor'); reached(new.stdout.entered)
            self.assertIs(analyzer.process, new)
            analyzer.stop(); reached(new.stdout.closed)

    def test_analyzer_stop_kills_child_ignoring_terminate(self):
        analyzer = Analyzer(); process = Process(ignore_terminate=True)
        with patch('eqxear.spectrum.Spectrum', FakeSpectrum), patch('eqxear.spectrum.subprocess.Popen', return_value=process):
            analyzer.set_source('test.monitor'); reached(process.stdout.entered)
            analyzer.stop(); reached(process.stdout.closed)
            self.assertTrue(process.killed.is_set())

    def test_tone_stop_during_spawn_reaps_unpublished_child(self):
        tone = Tone(); process = Process()
        entered, release = threading.Event(), threading.Event()
        def spawn(*args, **kwargs):
            entered.set(); reached(release); return process
        with patch('eqxear.tone.subprocess.Popen', spawn):
            starter=threading.Thread(target=tone.start)
            starter.start(); reached(entered)
            tone.stop(); release.set(); starter.join(3)
            self.assertFalse(starter.is_alive())
            reached(process.stdin.closed)
        self.assertIsNone(tone.process)
        self.assertEqual(process.stdin.writes, [])

    def test_tone_old_producer_keeps_own_pipe_and_cancellation(self):
        tone = Tone(); old, new = Process(), Process()
        # Hold the first writer beyond Stop/Start to expose a reused event or
        # self.process reference. Termination does not release this fake write.
        release = threading.Event()
        def held_write(data):
            old.stdin.writes.append((threading.current_thread().name,data))
            old.stdin.entered.set(); reached(release)
        old.stdin.write = held_write
        with patch('eqxear.tone.subprocess.Popen', side_effect=[old,new]):
            tone.start(); reached(old.stdin.entered)
            old_thread, old_event = tone.thread, tone.stop_event
            tone.start(); reached(new.stdin.entered)
            self.assertTrue(old_event.is_set())
            self.assertIsNot(old_event, tone.stop_event)
            release.set(); old_thread.join(3)
            self.assertFalse(old_thread.is_alive())
            self.assertEqual(len(new.stdin.writes), 1)
            self.assertNotEqual(old_thread.name, new.stdin.writes[0][0])
            self.assertIs(tone.process, new)
            self.assertIsNone(tone.error)
            tone.stop(); reached(new.stdin.closed)

    def test_tone_stop_kills_child_ignoring_terminate(self):
        tone = Tone(); process = Process(ignore_terminate=True)
        with patch('eqxear.tone.subprocess.Popen', return_value=process):
            tone.start(); reached(process.stdin.entered)
            tone.stop(); reached(process.stdin.closed)
            self.assertTrue(process.killed.is_set())

    def test_tone_stale_spawn_cannot_replace_new_child(self):
        tone = Tone(); old, new = Process(), Process()
        entered, release = threading.Event(), threading.Event()
        call_lock = threading.Lock()
        calls = 0
        def spawn(*args, **kwargs):
            nonlocal calls
            with call_lock:
                calls += 1
                number = calls
            if number == 1:
                entered.set(); reached(release); return old
            return new
        with patch('eqxear.tone.subprocess.Popen', spawn):
            starter=threading.Thread(target=tone.start)
            starter.start(); reached(entered)
            tone.start(); reached(new.stdin.entered)
            release.set(); starter.join(3)
            self.assertFalse(starter.is_alive())
            reached(old.stdin.closed)
            self.assertIs(tone.process, new)
            self.assertEqual(old.stdin.writes, [])
            tone.stop(); reached(new.stdin.closed)

    def test_interpreter_exit_cleans_active_and_retired_children(self):
        # Use actual child processes, but replace the audio executable with a
        # silent Python sleeper. It ignores TERM to exercise bounded escalation.
        script = r'''
import os, pathlib, subprocess, sys, time
from unittest.mock import patch
from eqxear.spectrum import Analyzer
from eqxear.tone import Tone
from tests.test_audio_lifecycle import FakeSpectrum
kind, mode, marker = sys.argv[1:]
original = subprocess.Popen
child = "import os,pathlib,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); pathlib.Path(%r).write_text(str(os.getpid())); time.sleep(60)" % marker
def spawn(args, **kwargs):
    return original([sys.executable, '-c', child], **kwargs)
patch('subprocess.Popen', spawn).start()
patch('eqxear.spectrum.Spectrum', FakeSpectrum).start()
audio = Analyzer() if kind == 'analyzer' else Tone()
if kind == 'analyzer': audio.set_source('fake.monitor')
else: audio.start()
deadline = time.monotonic()+3
while not pathlib.Path(marker).exists():
    if time.monotonic()>deadline: raise RuntimeError('fake child did not start')
    time.sleep(.01)
if mode == 'stop': audio.stop()
if mode == 'repeat':
    import eqxear.spectrum as spectrum
    spectrum._shutdown_at_exit()
    spectrum._shutdown_at_exit()
if mode == 'exception': raise RuntimeError('intentional unhandled exception')
raise SystemExit(0)
'''
        for kind, mode in [('analyzer','exit'), ('tone','exit'),
                           ('analyzer','exception'), ('tone','exception'),
                           ('analyzer','stop'), ('tone','stop'), ('analyzer','repeat')]:
            with self.subTest(kind=kind, mode=mode), tempfile.TemporaryDirectory() as directory:
                marker=Path(directory)/'child.pid'
                try:
                    result=subprocess.run([sys.executable,'-c',script,kind,mode,str(marker)],
                                          capture_output=True,text=True,timeout=8)
                    self.assertEqual(result.returncode,1 if mode=='exception' else 0,result.stderr)
                    pid=int(marker.read_text())
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid,0)
                finally:
                    if marker.exists():
                        try: os.kill(int(marker.read_text()),signal.SIGKILL)
                        except ProcessLookupError: pass
