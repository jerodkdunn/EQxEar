# SPDX-License-Identifier: Apache-2.0
"""Run under dbus-run-session. Uses virtual audio only."""
import array
import math
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from isolated_audio import session
from eqxear.spectrum import Analyzer, CENTERS

with session():
    analyzer = Analyzer()
    player = subprocess.Popen(['pacat','--playback','--raw','--format=float32le','--rate=48000','--channels=2','--device=test_speakers','--property=node.dont-reconnect=true','--property=node.dont-fallback=true'],stdin=subprocess.PIPE)
    def write():
        chunk = array.array('f',[.1*math.sin(2*math.pi*1000*i/48000) for i in range(480) for _ in (0,1)]).tobytes()
        try:
            while True:
                player.stdin.write(chunk); player.stdin.flush()
        except (OSError,ValueError):
            pass
    thread = threading.Thread(target=write,daemon=True);thread.start()
    try:
        analyzer.set_source('test_speakers.monitor')
        deadline = time.monotonic()+6
        while time.monotonic()<deadline and max(analyzer.levels)<-30:
            time.sleep(.05)
        time.sleep(.5)
        peak = max(range(31),key=analyzer.levels.__getitem__)
        assert 890<CENTERS[peak]<1130, (analyzer.levels,analyzer.message)
        assert -25<analyzer.levels[peak]<-21,analyzer.levels[peak]
        process = analyzer.process
        def monitor_source():
            streams=json.loads(subprocess.check_output(['pactl','--format=json','list','source-outputs'],text=True))
            return next(stream['source'] for stream in streams if stream['properties'].get('application.process.id') == str(process.pid))
        sources=json.loads(subprocess.check_output(['pactl','--format=json','list','sources'],text=True))
        expected=next(source['index'] for source in sources if source['name']=='test_speakers.monitor')
        assert monitor_source()==expected
        subprocess.run(['pactl','load-module','module-null-sink','sink_name=other_speakers'],check=True,capture_output=True,timeout=3)
        subprocess.run(['pactl','set-default-sink','other_speakers'],check=True,timeout=3)
        time.sleep(.7)
        assert monitor_source()==expected, 'RTA followed the default sink instead of the chosen output'
        assert -25<analyzer.levels[peak]<-21,analyzer.levels[peak]
        sinks=json.loads(subprocess.check_output(['pactl','--format=json','list','sinks'],text=True))
        module=next(sink['owner_module'] for sink in sinks if sink['name']=='test_speakers')
        subprocess.run(['pactl','unload-module',str(module)],check=True,timeout=3)
        deadline=time.monotonic()+4
        while time.monotonic()<deadline and 'RTA unavailable' not in analyzer.message:
            time.sleep(.05)
        assert 'RTA unavailable' in analyzer.message, analyzer.message
        assert analyzer.process is None, 'Missing output must stop capture instead of using another source'
        analyzer.stop()
        process.wait(timeout=3)
        assert analyzer.levels == (-90.,)*31
        print('PASS: real output monitor capture, 1 kHz frequency and level, monitor stays pinned across default changes, no fallback on output removal, clean capture shutdown')
    finally:
        analyzer.stop()
        player.terminate();player.wait(timeout=3)
        thread.join(timeout=1)
        try: player.stdin.close()
        except BrokenPipeError: pass
