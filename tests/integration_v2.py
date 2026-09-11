# SPDX-License-Identifier: Apache-2.0
"""dbus-run-session -- python tests/integration_v2.py
Measures actual desktop playback through EQxEar and a virtual speaker monitor.
"""
import array
from collections import deque
import math
import os
import socket
import shutil
from pathlib import Path
import subprocess
import sys
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from isolated_audio import session
from eqxear.model import Profile,Band
from eqxear.routing import Graph,command,SINK,pulse_list
from eqxear.engine import Engine

with session() as root:
    graph=Graph(root,Profile('Test',[Band(1000,0,1)]),'test_speakers')
    playing=recording=None
    ee=None
    # Reproduce the user's competing processor and its saved destination.
    config=root/'config/easyeffects/db';config.mkdir(parents=True,exist_ok=True)
    (config/'easyeffectsrc').write_text('[StreamOutputs]\noutputDevice=eqxear_v2\n[EffectsPipelines]\nbypass=true\n')
    if shutil.which('easyeffects'):
        ee_log=(root/'easyeffects.log').open('w')
        ee=subprocess.Popen(['easyeffects','--service-mode'],env=dict(os.environ,QT_QPA_PLATFORM='offscreen'),stdout=ee_log,stderr=ee_log)
        for _ in range(50):
            if 'easyeffects_sink' in {s['name'] for s in pulse_list('sinks')}:break
            time.sleep(.1)
    else:
        print('SKIP: optional EasyEffects coexistence check; EasyEffects is not installed')
    samples=deque(maxlen=80)
    try:
        graph.start()
        assert command(['pactl','get-default-sink'])==SINK
        pid,node=graph.process.pid,graph.node_id
        time.sleep(.5)
        sink_names={s['index']:s['name'] for s in pulse_list('sinks')}
        own=next(s for s in pulse_list('sink-inputs') if s.get('properties',{}).get('node.name')=='eqxear_v2_output_level')
        assert sink_names[own['sink']]=='test_speakers',sink_names[own['sink']]
        print('PASS: native output stage reaches the selected speakers' + (' with EasyEffects running' if ee else ''))
        recording=subprocess.Popen(['parec','--raw','--format=float32le','--rate=48000','--channels=2','--latency-msec=20','--device=test_speakers.monitor'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
        playing=subprocess.Popen(['pacat','--playback','--raw','--format=float32le','--rate=48000','--channels=2','--latency-msec=20','--device='+SINK],stdin=subprocess.PIPE,stderr=subprocess.DEVNULL)
        def produce():
            chunk=array.array('f',[.1*math.sin(2*math.pi*1000*i/48000) for i in range(480) for ch in range(2)]).tobytes()
            try:
                while True:playing.stdin.write(chunk);playing.stdin.flush()
            except (OSError,ValueError):pass
        def capture():
            while True:
                data=recording.stdout.read(3840)
                if not data:break
                values=array.array('f');values.frombytes(data)
                samples.append(sum(v*v for v in values)/len(values))
        writer=threading.Thread(target=produce,daemon=True);reader=threading.Thread(target=capture,daemon=True)
        writer.start();reader.start()
        def rms():
            time.sleep(.8)
            tail=list(samples)[-16:]
            assert tail,'No monitor audio received'
            return math.sqrt(sum(tail)/len(tail))
        # Allow the competing processor and session manager to finish initial routing.
        time.sleep(1.5)
        baseline=rms();assert .06<baseline<.08,baseline
        graph.update(Profile('Live',[Band(1000,6,1)]))
        boosted=rms();delta=20*math.log10(boosted/baseline)
        assert abs(delta-6)<.1,delta
        graph.update(Profile('Normalized',[Band(1000,6,1)],-6))
        normalized=rms();assert abs(20*math.log10(normalized/baseline))<.1
        graph.update(Profile('Cut',[Band(1000,-12,1)]),True)
        bypass=rms();assert abs(20*math.log10(bypass/baseline))<.1
        graph.update(Profile('Mute',[],0,True),False)
        muted=rms();assert muted<1e-10,muted
        graph.update(Profile('Restored',[]))
        restored=rms();assert abs(20*math.log10(restored/baseline))<.1
        assert graph.process.pid==pid and graph.node_id==node
        command(['pactl','load-module','module-null-sink','sink_name=test_headphones'])
        time.sleep(.3)
        graph.select_output('test_headphones')
        assert graph.output=='test_headphones'
        print(f'PASS: real audio live gain {delta:.3f} dB; normalized unity, bypass, exact mute, unmute, output switching; same native process and node throughout')
    except Exception:
        print(command(['pw-link','-l']))
        print(command(['pactl','list','short','sink-inputs']))
        print(command(['pactl','list','short','source-outputs']))
        print((root/'audio.log').read_text()[-5000:])
        print((root/'session.log').read_text()[-3000:])
        raise
    finally:
        for process in (playing,recording):
            if process:process.terminate();process.wait(timeout=3)
        if playing:
            writer.join(timeout=1)
            try:playing.stdin.close()
            except BrokenPipeError:pass
        if recording:
            reader.join(timeout=1);recording.stdout.close()
        graph.stop()
        if ee:
            try:
                with socket.socket(socket.AF_UNIX) as control:
                    control.connect(str(root/'run/EasyEffectsServer'));control.sendall(b'quit_app\n')
                ee.wait(timeout=3)
            except (OSError,subprocess.TimeoutExpired):
                ee.kill();ee.wait()
            ee_log.close()
    assert command(['pactl','get-default-sink'])=='test_speakers'
    print('PASS: original default output restored')
    engine=Engine()
    try:
        state=engine.start(Profile('Background',[Band(1000,-3)]),'test_speakers')
        assert state['running']
        second=Engine()
        assert second.status()['profile']['name']=='Background'
        second.apply(Profile('Recalled',[], -12))
        assert engine.status()['profile']['preamp']==-12
        second.bypass(True)
        assert engine.status()['bypassed']
        second.apply(Profile('Edited while bypassed',[], -6))
        assert engine.status()['bypassed']
        transport=subprocess.Popen(['pacat','--playback','--raw','--format=float32le','--rate=48000','--channels=2','--latency-msec=20','--device='+SINK],stdin=subprocess.PIPE,stderr=subprocess.DEVNULL)
        transport.stdin.write(bytes(3840));transport.stdin.flush();time.sleep(.2)
        stream_before=next(s for s in pulse_list('sink-inputs') if s.get('properties',{}).get('application.process.id')==str(transport.pid))
        sink_before=next(s for s in pulse_list('sinks') if s['name']==SINK)['index']
        stopped=second.stop()
        assert not stopped['running'] and stopped['connected']
        assert next(s for s in pulse_list('sinks') if s['name']==SINK)['index']==sink_before
        assert command(['pactl','get-default-sink'])==SINK
        resumed=second.start(Profile('Resume',[], -6),'test_speakers')
        assert resumed['running'] and not resumed['bypassed']
        assert next(s for s in pulse_list('sinks') if s['name']==SINK)['index']==sink_before
        stream_after=next(s for s in pulse_list('sink-inputs') if s['index']==stream_before['index'])
        assert stream_after['sink']==stream_before['sink']
        assert not stream_after['corked']
        transport.terminate();transport.wait(timeout=3);transport.stdin.close()
        print('PASS: stop/start retains the same playback device and stream without corking playback')
        print('PASS: background service survives client disconnect, accepts another client, and preserves bypass during live edits')
    finally:
        engine.disconnect()
    assert command(['pactl','get-default-sink'])=='test_speakers'
