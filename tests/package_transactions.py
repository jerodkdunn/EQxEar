# SPDX-License-Identifier: Apache-2.0
"""Actual pacman upgrade/removal during playback, only in a marked container.

The root controller changes packages while an unprivileged worker owns a separate
PipeWire session. Never run this against a desktop's package database.
"""
import argparse
import json
import os
from pathlib import Path
import select
import subprocess
import sys

MARKER = Path('/etc/eqxear-package-test-container')


def transaction(*arguments):
    result = subprocess.run(['pacman', '--noconfirm', *map(str, arguments)],
                            capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout + result.stderr


def controller(args):
    if os.geteuid() != 0:
        raise RuntimeError('Package transactions require container root')
    previous, package = args.previous.resolve(), args.package.resolve()
    assert previous.is_file() and package.is_file() and previous != package
    transaction('-U', previous)
    worker_command = [sys.executable, str(Path(__file__).resolve()), '--worker']
    if args.user != 'root':
        worker_command = ['runuser', '-u', args.user, '--', *worker_command]
    process = subprocess.Popen(worker_command,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               text=True, bufsize=1, start_new_session=True,
                               env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    expected = iter(('upgrade', 'remove', 'done'))
    try:
        while True:
            if not select.select([process.stdout], [], [], 120)[0]:
                raise TimeoutError('Package transaction worker stalled')
            line = process.stdout.readline()
            if not line:
                raise RuntimeError(f'Package transaction worker exited early: {process.poll()}')
            event = json.loads(line)['event']
            assert event == next(expected), event
            if event == 'done':
                break
            output = transaction('-U', package) if event == 'upgrade' else transaction('-R', 'eqxear')
            assert 'background service' in output, output
            process.stdin.write('ok\n')
            process.stdin.flush()
        assert process.wait(timeout=10) == 0
        print('PASS: live package upgrade/removal, safe-path restart, output restoration and preset preservation')
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()


def worker():
    import array
    from collections import deque
    import math
    import threading
    import time
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from isolated_audio import session
    sys.path.insert(0, '/usr/lib/eqxear')
    from eqxear.engine import Engine
    from eqxear.model import Band, Profile, Library
    from eqxear.routing import SINK, command, pulse_list
    samples = deque(maxlen=40)
    captured_blocks = 0
    player = recorder = None
    threads = []

    def exchange(event):
        print(json.dumps({'event': event}), flush=True)
        assert sys.stdin.readline().strip() == 'ok'

    def instance():
        return next(s['properties']['eqxear.instance'] for s in pulse_list('sinks') if s['name'] == SINK)

    def rms():
        before = captured_blocks
        time.sleep(.8)
        assert captured_blocks > before + 16, 'Capture stopped receiving fresh audio'
        tail = list(samples)[-16:]
        assert tail, 'No captured audio'
        return math.sqrt(sum(tail) / len(tail))

    with session():
        # The 0.1.0 baseline has the safe-path bug. Enable it for the fresh,
        # upgraded client and service below, after verifying uninterrupted audio.
        os.environ.pop('PYTHONSAFEPATH', None)
        engine = Engine()
        profile = Profile('Package transaction test', [Band(1000, -3)])
        library = Library()
        library.save([profile])
        saved = library.path.read_bytes()
        try:
            assert engine.start(profile, 'test_speakers')['running']
            old_instance = instance()
            recorder = subprocess.Popen(['parec', '--raw', '--format=float32le', '--rate=48000', '--channels=2', '--latency-msec=20', '--device=test_speakers.monitor', '--property=node.dont-reconnect=true', '--property=node.dont-fallback=true'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            player = subprocess.Popen(['pacat', '--playback', '--raw', '--format=float32le', '--rate=48000', '--channels=2', '--latency-msec=20'], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
            def produce():
                block = array.array('f', [.1*math.sin(2*math.pi*1000*i/48000) for i in range(480) for _ in range(2)]).tobytes()
                try:
                    while True:
                        player.stdin.write(block)
                        player.stdin.flush()
                except (OSError, ValueError):
                    pass
            def capture():
                nonlocal captured_blocks
                while data := recorder.stdout.read(3840):
                    values = array.array('f')
                    values.frombytes(data)
                    samples.append(sum(v*v for v in values)/len(values))
                    captured_blocks += 1
            for target in (produce, capture):
                thread = threading.Thread(target=target, daemon=True)
                threads.append(thread)
                thread.start()
            time.sleep(.5)
            baseline = rms()
            assert .045 < baseline < .055, baseline
            exchange('upgrade')
            assert engine.status()['running'] and instance() == old_instance
            level = rms()
            assert abs(20*math.log10(max(level, 1e-15)/baseline)) < .2, (level, baseline, player.poll())
            assert library.path.read_bytes() == saved
            # Reopening the upgraded client can still control the old service.
            # Quit it explicitly, then start a fresh installed backend in safe mode.
            engine.disconnect()
            code = '''import sys; sys.path.insert(0, '/usr/lib/eqxear')
from eqxear.engine import Engine
from eqxear.model import Profile, Band
assert Engine().start(Profile('Package transaction test', [Band(1000, -3)]), 'test_speakers')['running']
'''
            subprocess.run([sys.executable, '-c', code], cwd='/tmp',
                           env=dict(os.environ, PYTHONSAFEPATH='1', PYTHONPATH=''), check=True, timeout=30)
            new_instance = instance()
            assert new_instance != old_instance
            level = rms()
            assert abs(20*math.log10(max(level, 1e-15)/baseline)) < .2, (level, baseline, player.poll())
            exchange('remove')
            assert not Path('/usr/lib/eqxear').exists()
            assert not Path('/usr/bin/eqxear').exists()
            assert engine.status()['running'] and instance() == new_instance
            level = rms()
            assert abs(20*math.log10(max(level, 1e-15)/baseline)) < .2, (level, baseline, player.poll())
            assert library.path.read_bytes() == saved
            # This existing client remains usable after package removal, just as
            # an already-open GUI can still request a graceful backend quit.
            engine.disconnect()
            assert command(['pactl', 'get-default-sink']) == 'test_speakers'
            assert not any(s['name'] == SINK for s in pulse_list('sinks'))
            assert rms() > .065
            assert library.path.read_bytes() == saved
        finally:
            try:
                engine.disconnect()
            except (OSError, RuntimeError):
                pass
            for process in (player, recorder):
                if process is not None:
                    process.terminate()
                    process.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=2)
            if player:
                try:
                    player.stdin.close()
                except BrokenPipeError:
                    pass
            if recorder:
                recorder.stdout.close()
    print(json.dumps({'event': 'done'}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--package', type=Path)
    parser.add_argument('--previous', type=Path)
    parser.add_argument('--user', default='listener')
    args = parser.parse_args()
    if not MARKER.is_file() or MARKER.read_text() != 'Disposable packaging test root\n':
        raise SystemExit('Refusing package changes outside a marked disposable container')
    if args.worker:
        worker()
    else:
        if args.package is None or args.previous is None:
            parser.error('--package and --previous are required')
        controller(args)
