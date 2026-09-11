# SPDX-License-Identifier: Apache-2.0
"""Verify DSP-reported rates and normalization with isolated live PipeWire audio."""
import array
from collections import deque
import math
from pathlib import Path
import subprocess
import sys
import threading
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from isolated_audio import session
from eqxear.engine import Engine
from eqxear.model import Band, Profile
from eqxear.routing import command, SINK


def wait_rate(engine, expected):
    deadline = time.monotonic()+8
    while time.monotonic() < deadline:
        state = engine.status()
        if state.get('sample_rate') == expected:
            return state
        time.sleep(.1)
    raise AssertionError(f'DSP did not report {expected}: {state}')


with session():
    engine = Engine()
    playing = recording = None
    stopped = threading.Event()
    samples = deque(maxlen=80)
    try:
        command(['pw-metadata', '-n', 'settings', '0', 'clock.force-rate', '96000'])
        profile = Profile('Rate test', [Band(12000, 6, .3), Band(20000, 6, .3)])
        engine.start(Profile('Flat', []), 'test_speakers')
        recording = subprocess.Popen(['parec', '--raw', '--format=float32le', '--rate=96000', '--channels=2', '--latency-msec=20', '--device=test_speakers.monitor', '--property=node.dont-reconnect=true', '--property=node.dont-fallback=true'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        playing = subprocess.Popen(['pacat', '--playback', '--raw', '--format=float32le', '--rate=96000', '--channels=2', '--latency-msec=20', '--device='+SINK], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        data = array.array('f', [.03*math.sin(2*math.pi*15600*i/96000) for i in range(960) for _ in range(2)]).tobytes()
        def produce():
            try:
                while not stopped.is_set():
                    playing.stdin.write(data); playing.stdin.flush()
            except (OSError, ValueError):
                pass
        def capture():
            while not stopped.is_set():
                data = recording.stdout.read(7680)
                if not data:
                    break
                values = array.array('f'); values.frombytes(data)
                samples.append(sum(v*v for v in values)/len(values))
        writer = threading.Thread(target=produce, daemon=True)
        reader = threading.Thread(target=capture, daemon=True)
        writer.start(); reader.start()
        def rms():
            time.sleep(.7)
            tail = list(samples)[-15:]
            assert tail, 'No isolated capture data'
            return math.sqrt(sum(tail)/len(tail))
        state = wait_rate(engine, 96000)
        baseline = rms()
        assert .015 < baseline < .03, baseline
        profile.preamp = profile.normalization_preamp(rate=state['sample_rate'])
        engine.apply(profile)
        gain = 20*math.log10(rms()/baseline)
        expected = profile.response(15600, rate=96000)+profile.preamp
        assert -.11 < gain <= .01 and abs(gain-expected) < .06, (gain, expected)
        print(f'PASS: DSP reports 96 kHz; normalized live gain {gain:.4f} dB agrees with preview {expected:.4f} dB')
        # Force actual graph renegotiation while streams and service remain alive.
        for rate in (48000, 44100, 32000):
            command(['pw-metadata', '-n', 'settings', '0', 'clock.force-rate', str(rate)])
            state = wait_rate(engine, rate)
            assert state['running'] and state['connected']
        print('PASS: live DSP sample-rate changes to 48, 44.1 and 32 kHz reach the service client')
    finally:
        stopped.set()
        for process in (playing, recording):
            if process:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=2)
        for thread in (locals().get('writer'), locals().get('reader')):
            if thread:
                thread.join(timeout=1)
        for pipe in (playing.stdin if playing else None, recording.stdout if recording else None):
            if pipe:
                try:
                    pipe.close()
                except OSError:
                    pass
        engine.disconnect()
