# SPDX-License-Identifier: Apache-2.0
"""GUI client for the independent EQxEar engine."""
import errno
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from .service import runtime
from .routing import outputs
from .tone import Tone

REQUEST_TIMEOUT = 5.0
START_TIMEOUT = 20.0
POLL_INTERVAL = .1
MAX_REPLY_BYTES = 65536


def service_absent(error):
    return isinstance(error, OSError) and error.errno in (errno.ENOENT, errno.ECONNREFUSED)


class Engine:
    def __init__(self):
        self.socket_path = str(runtime()/'control.sock')
        self.owned = False

    def request(self, action, **fields):
        timeout = START_TIMEOUT if action == 'start' else REQUEST_TIMEOUT
        return self._request(action, time.monotonic()+timeout, **fields)

    def _request(self, action, deadline, **fields):
        def remaining():
            duration = deadline-time.monotonic()
            if duration <= 0:
                raise TimeoutError('Audio service did not respond before the deadline.')
            return duration

        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(remaining())
            connection.connect(self.socket_path)
            connection.settimeout(remaining())
            connection.sendall((json.dumps({'action': action, **fields}, allow_nan=False)+'\n').encode())
            data = bytearray()
            while b'\n' not in data:
                connection.settimeout(remaining())
                chunk = connection.recv(min(65536, MAX_REPLY_BYTES-len(data)))
                if not chunk:
                    raise RuntimeError('Audio service disconnected before replying.')
                data.extend(chunk)
                if len(data) >= MAX_REPLY_BYTES and b'\n' not in data:
                    raise RuntimeError('Audio service response is too large.')
            try:
                reply = json.loads(data.split(b'\n', 1)[0])
            except (ValueError, UnicodeError) as error:
                raise RuntimeError('Audio service sent invalid JSON.') from error
            if not isinstance(reply, dict) or type(reply.get('ok')) is not bool:
                raise RuntimeError('Audio service sent an invalid response object.')
            for key in ('running', 'connected', 'bypassed'):
                if key in reply and type(reply[key]) is not bool:
                    raise RuntimeError(f'Audio service sent an invalid {key} flag.')
            if not reply['ok']:
                message = reply.get('error', 'Audio service error')
                raise RuntimeError(message if isinstance(message, str) else 'Audio service error')
            self.owned = reply.get('running', False)
            return reply

    def start(self, profile, output=None, bypass=False):
        deadline = time.monotonic()+START_TIMEOUT
        try:
            self._request('status', min(deadline, time.monotonic()+REQUEST_TIMEOUT))
        except OSError as error:
            if not service_absent(error):
                raise
            root = Path(__file__).resolve().parents[1]
            with (runtime()/'service.log').open('a') as log:
                subprocess.Popen([sys.executable, '-m', 'eqxear.service'], cwd=root,
                                 stdout=log, stderr=log, start_new_session=True)
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Audio service did not start before the deadline.')
                try:
                    self._request('status', min(deadline, time.monotonic()+REQUEST_TIMEOUT))
                    break
                except OSError as error:
                    if not service_absent(error):
                        raise
                    time.sleep(min(POLL_INTERVAL, max(0, deadline-time.monotonic())))
        return self._request('start', deadline, profile=profile.to_dict(), output=output, bypass=bypass)

    def apply(self, profile): return self.request('update', profile=profile.to_dict())
    def bypass(self, bypass): return self.request('bypass', bypass=bypass)
    def select_output(self, output): return self.request('output', output=output)
    def stop(self): return self.request('stop')
    def disconnect(self): return self.request('disconnect')

    def status(self):
        try:
            return self.request('status')
        except OSError as error:
            if not service_absent(error):
                raise
            self.owned = False
            return {'running': False}
