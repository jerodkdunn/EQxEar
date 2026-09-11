# SPDX-License-Identifier: Apache-2.0
"""Exercise the client against isolated Unix sockets; never starts real audio."""
from contextlib import contextmanager
import errno
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from eqxear.engine import Engine
from eqxear.model import Profile


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime_patch = patch('eqxear.engine.runtime', return_value=self.root)
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)
        self.engine = Engine()

    @contextmanager
    def server(self, handler, connections=1):
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(self.engine.socket_path)
        listener.listen()
        listener.settimeout(1)
        errors = []
        def work():
            try:
                for _ in range(connections):
                    connection, _ = listener.accept()
                    with connection:
                        connection.settimeout(1)
                        request = bytearray()
                        while b'\n' not in request:
                            part = connection.recv(4096)
                            if not part:
                                return
                            request.extend(part)
                        handler(connection, json.loads(request))
            except (BrokenPipeError, ConnectionResetError):
                pass  # Deadline tests deliberately disconnect from the fake server.
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        try:
            yield
        finally:
            thread.join(2)
            listener.close()
            Path(self.engine.socket_path).unlink(missing_ok=True)
            self.assertFalse(thread.is_alive())
            if errors:
                raise errors[0]

    def test_valid_reply_and_malformed_objects(self):
        replies = [b'{"ok":true,"running":true}\n', b'null\n', b'[]\n',
                   b'{"ok":1}\n', b'{"ok":true,"running":"yes"}\n', b'garbage\n']
        for reply in replies:
            with self.subTest(reply=reply), self.server(lambda c, _: c.sendall(reply)):
                if reply == replies[0]:
                    self.assertTrue(self.engine.status()['running'])
                    self.assertIs(self.engine.owned, True)
                else:
                    with self.assertRaises(RuntimeError): self.engine.status()
                    self.assertIs(self.engine.owned, True)  # Malformed responses cannot corrupt state.

    def test_missing_service_does_not_hide_permission_or_timeout_errors(self):
        self.engine.owned = True
        self.assertEqual(self.engine.status(), {'running': False})
        self.assertFalse(self.engine.owned)
        for error in (PermissionError(13, 'denied'), TimeoutError('hung')):
            with patch.object(self.engine, 'request', side_effect=error):
                with self.assertRaises(type(error)): self.engine.status()

    def test_hung_service_is_not_respawned(self):
        with self.server(lambda c, _: time.sleep(.15)), patch('eqxear.engine.REQUEST_TIMEOUT', .04), patch('eqxear.engine.subprocess.Popen') as spawn:
            start = time.monotonic()
            with self.assertRaises(TimeoutError): self.engine.start(Profile('Test', []))
            self.assertLess(time.monotonic()-start, .12)
            spawn.assert_not_called()

    def test_trickling_reply_has_total_deadline(self):
        def trickle(connection, _):
            for _ in range(20):
                connection.sendall(b' ')
                time.sleep(.015)
        with self.server(trickle), patch('eqxear.engine.REQUEST_TIMEOUT', .06):
            start = time.monotonic()
            with self.assertRaises(TimeoutError): self.engine.status()
            self.assertLess(time.monotonic()-start, .2)

    def test_reply_size_and_disconnect_are_bounded(self):
        with self.server(lambda c, _: c.sendall(b'x'*128)), patch('eqxear.engine.MAX_REPLY_BYTES', 64):
            with self.assertRaisesRegex(RuntimeError, 'too large'): self.engine.status()
        with self.server(lambda c, _: None):
            with self.assertRaisesRegex(RuntimeError, 'disconnected'): self.engine.status()

    def test_missing_service_startup_has_one_overall_deadline(self):
        with patch('eqxear.engine.START_TIMEOUT', .06), patch('eqxear.engine.POLL_INTERVAL', .005), patch('eqxear.engine.subprocess.Popen') as spawn:
            start = time.monotonic()
            with self.assertRaisesRegex(TimeoutError, 'did not start'):
                self.engine.start(Profile('Test', []))
            self.assertLess(time.monotonic()-start, .2)
            spawn.assert_called_once()

    def test_existing_service_start_preserves_request_fields(self):
        received = []
        def handler(connection, request):
            received.append(request)
            connection.sendall(b'{"ok":true,"running":true,"bypassed":true}\n')
        profile = Profile('Saved', [], -3)
        with self.server(handler, connections=2), patch('eqxear.engine.subprocess.Popen') as spawn:
            result = self.engine.start(profile, 'speakers', bypass=True)
            spawn.assert_not_called()
        self.assertTrue(result['running'])
        self.assertTrue(self.engine.owned)
        self.assertEqual([r['action'] for r in received], ['status', 'start'])
        self.assertEqual(received[1]['profile'], profile.to_dict())
        self.assertEqual(received[1]['output'], 'speakers')
        self.assertIs(received[1]['bypass'], True)

    def test_refused_socket_is_retried_until_service_is_ready(self):
        absent = ConnectionRefusedError(errno.ECONNREFUSED, 'not ready')
        reply = {'ok': True, 'running': True}
        with patch.object(self.engine, '_request', side_effect=[absent, absent, reply, reply]) as request, patch('eqxear.engine.POLL_INTERVAL', 0), patch('eqxear.engine.subprocess.Popen') as spawn:
            self.assertEqual(self.engine.start(Profile('Test', [])), reply)
            spawn.assert_called_once()
            self.assertEqual([call.args[0] for call in request.call_args_list], ['status', 'status', 'status', 'start'])

    def test_status_and_start_share_deadline(self):
        def handler(connection, request):
            time.sleep(.05)
            connection.sendall(b'{"ok":true,"running":false}\n')
        with self.server(handler, connections=2), patch('eqxear.engine.START_TIMEOUT', .08):
            start = time.monotonic()
            with self.assertRaises(TimeoutError): self.engine.start(Profile('Test', []))
            self.assertLess(time.monotonic()-start, .13)


if __name__ == '__main__':
    unittest.main()
