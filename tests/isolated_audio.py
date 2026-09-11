# SPDX-License-Identifier: Apache-2.0
"""Temporary PipeWire/Pulse/WirePlumber session with no hardware monitors."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import tempfile
import time


@contextmanager
def session(control=None):
    before = dict(os.environ)
    with tempfile.TemporaryDirectory(prefix='eqxear-v2-test-') as temp:
        root = Path(temp)
        processes = []
        for key, name in [('XDG_RUNTIME_DIR','run'),('XDG_CONFIG_HOME','config'),('XDG_DATA_HOME','data'),('XDG_STATE_HOME','state'),('XDG_CACHE_HOME','cache')]:
            path = root/name
            path.mkdir(mode=0o700)
            os.environ[key] = str(path)
        os.environ['PIPEWIRE_RUNTIME_DIR'] = str(root/'run')
        os.environ['PULSE_SERVER'] = 'unix:'+str(root/'run/pulse/native')
        os.environ['GIO_USE_VFS'] = 'local'
        os.environ['GIO_USE_VOLUME_MONITOR'] = 'unix'
        os.environ.pop('PIPEWIRE_REMOTE', None)
        config = root/'config/wireplumber/wireplumber.conf.d'
        config.mkdir(parents=True)
        (config/'00-test.conf').write_text('wireplumber.profiles = { main = { monitor.alsa = disabled monitor.bluez = disabled monitor.bluez-midi = disabled monitor.v4l2 = disabled monitor.libcamera = disabled } }')
        log = (root/'session.log').open('w')

        def stop_servers():
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=3)
            processes.clear()

        def start_servers():
            for executable in ('pipewire', 'pipewire-pulse', 'wireplumber'):
                processes.append(subprocess.Popen([executable], stdout=log, stderr=log))
                time.sleep(.4)
            for _ in range(30):
                result = subprocess.run(['pactl', 'info'], capture_output=True, timeout=2)
                if result.returncode == 0:
                    break
                time.sleep(.1)
            else:
                raise RuntimeError('Isolated audio server did not start')
            subprocess.run(['pactl','load-module','module-null-sink','sink_name=test_speakers',
                            'sink_properties=device.description=TestSpeakers'], capture_output=True,
                           text=True, check=True, timeout=3)
            time.sleep(.4)

        if control is not None:
            control.update(start=start_servers, stop=stop_servers, processes=processes)
        try:
            start_servers()
            yield root
        finally:
            stop_servers()
            log.close()
            os.environ.clear()
            os.environ.update(before)
