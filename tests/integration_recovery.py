# SPDX-License-Identifier: Apache-2.0
"""Crash/restart regressions. Run under dbus-run-session; virtual audio only."""
import array
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from isolated_audio import session
from eqxear.engine import Engine
from eqxear.model import Profile, Band
from eqxear.routing import Graph, SINK, command, pulse_list
from eqxear.bootstrap import module_command


def eventually(fn, timeout=6):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(.05)
    raise AssertionError('Condition did not become true before deadline')


def nodes():
    return [n for n in Graph.nodes() if n['info'].get('props', {}).get('node.name') == SINK]


def session_service(root):
    matches = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            args = (path/'cmdline').read_bytes().split(b'\0')
            env = (path/'environ').read_bytes().split(b'\0')
            expected = [os.fsencode(part) for part in module_command('service')[1:]]
            if args[1:-1] == expected and ('XDG_RUNTIME_DIR='+str(root/'run')).encode() in env:
                matches.append(int(path.name))
        except (OSError, PermissionError):
            pass
    assert len(matches) == 1, matches
    return matches[0]


@contextmanager
def audio_probe():
    """Track playback through the default output across an engine restart."""
    recording = subprocess.Popen(['parec','--raw','--format=float32le','--rate=48000','--channels=2',
                                  '--latency-msec=20','--device=test_speakers.monitor',
                                  '--property=node.dont-reconnect=true','--property=node.dont-fallback=true'], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL)
    playing = subprocess.Popen(['pacat','--playback','--raw','--format=float32le','--rate=48000','--channels=2',
                                '--latency-msec=20'], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    powers = []
    def capture():
        while True:
            chunk = recording.stdout.read(3840)
            if not chunk:
                return
            samples = array.array('f'); samples.frombytes(chunk)
            powers.append(sum(v*v for v in samples)/len(samples))
    def produce():
        chunk = array.array('f',[.1*math.sin(2*math.pi*1000*i/48000) for i in range(480) for _ in (0,1)]).tobytes()
        try:
            while True:
                playing.stdin.write(chunk); playing.stdin.flush()
        except (OSError, ValueError):
            pass
    reader = threading.Thread(target=capture, daemon=True)
    writer = threading.Thread(target=produce, daemon=True)
    try:
        reader.start(); writer.start()
        def gain():
            time.sleep(1.5)
            values = powers[-20:]
            assert values, 'No recovered audio'
            return 10*math.log10(sum(values)/len(values)/.005)
        yield playing, gain
    finally:
        for process in (playing, recording):
            process.terminate(); process.wait(timeout=3)
        writer.join(timeout=1); reader.join(timeout=1)
        try: playing.stdin.close()
        except BrokenPipeError: pass
        recording.stdout.close()


control = {}
with session(control) as root:
    engine = Engine()
    try:
        engine.start(Profile('Before crash', [Band(1000,6)]), 'test_speakers')
        assert len(nodes()) == 1
        original = nodes()[0]['info']['props']['eqxear.instance']
        with audio_probe() as (playing, measure):
            assert abs(measure()-6) < .15
            stream_before = next(s for s in pulse_list('sink-inputs')
                                 if s.get('properties', {}).get('application.process.id') == str(playing.pid))
            os.kill(session_service(root), signal.SIGKILL)
            eventually(lambda: not nodes())
            restarted = Engine()
            state = restarted.start(Profile('After crash', [Band(1000,-6)]), 'test_speakers')
            assert state['running']
            assert len(nodes()) == 1
            assert nodes()[0]['info']['props']['eqxear.instance'] != original
            gain = measure()
            assert abs(gain+6) < .15, gain
            stream_after = next(s for s in pulse_list('sink-inputs') if s['index'] == stream_before['index'])
            assert not stream_after['corked']
            own_sink = next(s['index'] for s in pulse_list('sinks') if s['name'] == SINK)
            assert stream_after['sink'] == own_sink
        print(f'PASS: service SIGKILL removes child; restart has one owned sink; existing playback rerouted, gain {gain:.3f} dB')

        # Removing an owned node invalidates the old ID even if its process lives.
        command(['pw-cli', 'destroy', str(nodes()[0]['id'])])
        state = eventually(lambda: (s if not (s := restarted.status())['running'] else None))
        assert not state['connected']
        assert restarted.start(Profile('After node loss', []), 'test_speakers')['running']
        assert len(nodes()) == 1
        print('PASS: owned-node loss is detected; Start reconstructs the graph')

        control['stop']()
        state = eventually(lambda: (s if not (s := restarted.status())['running'] else None))
        assert not state['connected']
        # Failed routing restoration must not wedge or prevent disconnect.
        assert not restarted.disconnect()['connected']
        eventually(lambda: not (root/'run/eqxear-v2/control.sock').exists())
        control['start']()
        assert restarted.start(Profile('After server return', [Band(1000,-3)]), 'test_speakers')['running']
        assert len(nodes()) == 1
        with audio_probe() as (_, measure):
            gain = measure()
            assert abs(gain+3) < .15, gain
        print(f'PASS: server outage, disconnect and restart recover; measured gain {gain:.3f} dB')
        restarted.disconnect()
        eventually(lambda: not (root/'run/eqxear-v2/control.sock').exists())

        # A foreign same-name device must remain untouched, never receive EQ controls.
        module = command(['pactl','load-module','module-null-sink','sink_name='+SINK])
        try:
            try:
                Engine().start(Profile('Collision', []), 'test_speakers')
            except RuntimeError as error:
                assert 'unmanaged EQxEar output' in str(error), error
            else:
                raise AssertionError('Accepted an unowned existing sink')
            assert len(nodes()) == 1
            assert not nodes()[0]['info']['props'].get('eqxear.instance')
            print('PASS: unmanaged same-name sink is refused and preserved')
        finally:
            command(['pactl','unload-module',module])
    finally:
        try: Engine().disconnect()
        except (OSError, RuntimeError): pass
