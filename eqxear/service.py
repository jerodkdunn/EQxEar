# SPDX-License-Identifier: Apache-2.0
"""Background controller. Audio processing lives in the native PipeWire plugin."""
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import socket
import stat
import time
from .model import Profile
from .routing import Graph, GraphUnavailable, outputs


def runtime():
    path = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))/'eqxear-v2'
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('EQxEar runtime directory must be a directory owned by the current user.')
    if stat.S_IMODE(info.st_mode) != 0o700:
        path.chmod(0o700)
    return path


class Controller:
    """Separate request/state handling from the socket and signal lifecycle."""
    def __init__(self, root):
        self.root = root
        self.graph = None
        self.processing = False
        self.stopping = False
        self.last = {}
        self.health = {'running': False, 'connected': False}
        self.warning = ''
        self.checked_at = time.monotonic()

    def release(self):
        graph, self.graph = self.graph, None
        self.processing = False
        self.health = {'running': False, 'connected': False}
        if graph:
            self.last = {'profile': graph.profile.to_dict(), 'output': graph.output}
            try:
                warning = graph.stop()
            except Exception as error:
                warning = str(error)
            if warning:
                self.warning = warning

    def check(self):
        if self.graph:
            try:
                self.health = self.graph.status()
            except Exception as error:
                self.health = {'running': False, 'connected': False, 'warning': str(error)}
            if not self.health.get('connected'):
                warning = self.health.get('warning') or 'The EQ audio graph disconnected.'
                self.release()
                self.warning = warning+' Start system EQ to reconnect after audio returns.'
        self.checked_at = time.monotonic()
        return self.state()

    def state(self):
        if self.graph:
            result = self.graph.snapshot()
            result['connected'] = self.health.get('connected', False)
            result['running'] = bool(self.processing and self.health.get('running'))
            result['warning'] = self.health.get('warning', '')
            return result
        return {'running': False, 'connected': False, **self.last, 'warning': self.warning}

    def handle(self, request):
        if not isinstance(request, dict) or not isinstance(request.get('action'), str):
            raise ValueError('Expected a command object with an action')
        action = request['action']
        if action not in ('start', 'update', 'bypass', 'output', 'stop', 'disconnect', 'status'):
            raise ValueError('Unknown command')
        if 'bypass' in request and type(request['bypass']) is not bool:
            raise ValueError('Bypass must be a boolean')
        if 'output' in request and request['output'] is not None and not isinstance(request['output'], str):
            raise ValueError('Output must be a device name')
        if action == 'disconnect':
            # Set terminal state before best-effort routing restoration.
            self.stopping = True
            self.release()
            return self.state()
        if action == 'status':
            return self.check()
        if action == 'start':
            profile = Profile.from_dict(request['profile'])
            self.check()
            if self.graph:
                if request.get('output') and request['output'] != self.graph.output:
                    self.graph.select_output(request['output'])
                self.graph.update(profile, request.get('bypass', False))
            else:
                choices = outputs() if not request.get('output') else []
                output = request.get('output') or (choices[0][0] if choices else None)
                candidate = Graph(self.root, profile, output, request.get('bypass', False))
                candidate.start()
                self.graph = candidate
            self.processing = True
            self.warning = ''
            return self.check()
        if action == 'stop':
            if self.graph:
                self.graph.update(self.graph.profile, True)
            self.processing = False
        elif not self.graph:
            raise GraphUnavailable('Start system EQ first.')
        elif action == 'update':
            self.graph.update(Profile.from_dict(request['profile']))
        elif action == 'bypass':
            if not self.processing and not request['bypass']:
                raise ValueError('Start system EQ before enabling processing.')
            self.graph.update(self.graph.profile, request['bypass'])
        elif action == 'output':
            self.graph.select_output(request['output'])
            return self.check()
        return self.state()


def receive(connection):
    deadline = time.monotonic()+3
    data = bytearray()
    while b'\n' not in data:
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Command timed out')
        connection.settimeout(remaining)
        chunk = connection.recv(65536-len(data))
        if not chunk or len(data)+len(chunk) >= 65536:
            raise ValueError('Invalid or oversized command')
        data.extend(chunk)
    return json.loads(data.split(b'\n', 1)[0])


def main():
    root = runtime()
    fd = os.open(root/'service.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        os.fchmod(lock.fileno(), 0o600)
        path = root/'control.sock'
        path.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX)
        previous_umask = os.umask(0o077)
        try:
            server.bind(str(path))
        finally:
            os.umask(previous_umask)
        server.listen(8)
        controller = Controller(root)
        def stop(*_):
            controller.stopping = True
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            while not controller.stopping:
                child = controller.graph.process if controller.graph else None
                if (child is not None and child.poll() is not None) or time.monotonic()-controller.checked_at >= 5:
                    controller.check()
                ready, _, _ = select.select([server], [], [], .25)
                if not ready:
                    continue
                connection, _ = server.accept()
                with connection:
                    try:
                        result = controller.handle(receive(connection))
                        connection.settimeout(1)
                        connection.sendall((json.dumps({'ok': True, **result})+'\n').encode())
                    except Exception as error:
                        if isinstance(error, GraphUnavailable):
                            controller.release()
                            controller.warning = str(error)
                        try:
                            connection.settimeout(1)
                            connection.sendall((json.dumps({'ok': False, 'error': str(error)})+'\n').encode())
                        except OSError:
                            pass
        finally:
            controller.release()
            server.close()
            path.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
