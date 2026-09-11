# SPDX-License-Identifier: Apache-2.0
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from eqxear.model import Profile
from eqxear.routing import Graph, GraphUnavailable, SINK, PLAYBACK
from eqxear.service import Controller, runtime
import os
import subprocess
from eqxear.routing import command, command_budget


class RecoveryTests(unittest.TestCase):
    def test_diagnostics_failure_cannot_skip_cleanup(self):
        for diagnostic in ('command', 'file'):
            with self.subTest(diagnostic=diagnostic), tempfile.TemporaryDirectory() as directory:
                graph = Graph(directory, Profile('Test', []), 'speakers')
                child = Mock(); child.poll.return_value = None
                def command(args, **kwargs):
                    if args[0] == 'pw-dump':
                        if diagnostic == 'command':
                            raise RuntimeError('Diagnostic command failed')
                        return '[]'
                    return 'speakers'
                original = Path.write_text
                def write(path, *args, **kwargs):
                    if path.name == 'failed.json':
                        raise OSError('Diagnostic disk full')
                    return original(path, *args, **kwargs)
                with patch('eqxear.routing.build_plugin', return_value=Path(directory)), \
                     patch('eqxear.routing.outputs', return_value=[('speakers','Speakers')]), \
                     patch.object(graph, 'nodes', side_effect=[[], RuntimeError('Original startup failure')]), \
                     patch('eqxear.routing.command', side_effect=command), \
                     patch('eqxear.routing.pulse_list', side_effect=RuntimeError('Audio unavailable')), \
                     patch('eqxear.routing.subprocess.Popen', return_value=child), \
                     patch.object(Path, 'write_text', new=write):
                    with self.assertRaisesRegex(RuntimeError, 'Original startup failure'):
                        graph.start()
                child.terminate.assert_called_once()
                self.assertIsNone(graph.process)
                self.assertIsNone(graph.log)

    def test_foreign_or_stale_node_is_never_updated(self):
        graph = Graph('/unused', Profile('Test', []), 'speakers')
        graph.process = Mock(); graph.process.poll.return_value = None
        graph.node_id = 17
        for instance in ('foreign-instance', graph.instance):
            nodes = [{'id': 18 if instance == graph.instance else 17,
                      'info': {'props': {'node.name':SINK, 'eqxear.instance':instance}}}]
            with self.subTest(instance=instance), patch.object(graph, 'nodes', return_value=nodes), \
                 patch('eqxear.routing.command') as command:
                with self.assertRaises(GraphUnavailable):
                    graph.update(Profile('Edit', [], -3))
                command.assert_not_called()

    def test_zero_exit_control_error_is_not_success(self):
        graph = Graph('/unused', Profile('Before', []), 'speakers')
        graph.node_id = 17
        with patch.object(graph, 'require_node'), patch('eqxear.routing.command', return_value='Error: unknown global 17'):
            with self.assertRaises(GraphUnavailable):
                graph.update(Profile('After', [], -3))
        self.assertEqual(graph.profile.name, 'Before')

    def test_disconnect_clears_state_even_if_cleanup_raises(self):
        controller = Controller('/unused')
        graph = Mock(profile=Profile('Saved', []), output='speakers')
        graph.stop.side_effect = RuntimeError('Connection refused')
        controller.graph = graph; controller.processing = True
        result = controller.handle({'action':'disconnect'})
        self.assertTrue(controller.stopping)
        self.assertIsNone(controller.graph)
        self.assertFalse(result['running'])
        self.assertIn('Connection refused', result['warning'])

    def test_failed_stop_does_not_claim_processing_stopped(self):
        controller = Controller('/unused')
        graph = Mock(profile=Profile('Active', []), output='speakers')
        graph.update.side_effect = RuntimeError('Control command failed')
        graph.snapshot.return_value = {'running':True, 'connected':True}
        controller.graph = graph; controller.processing = True
        controller.health = {'running':True, 'connected':True}
        with self.assertRaisesRegex(RuntimeError, 'Control command failed'):
            controller.handle({'action':'stop'})
        self.assertTrue(controller.state()['running'])
        controller.processing = False
        graph.update.reset_mock()
        with self.assertRaisesRegex(ValueError, 'Start system EQ'):
            controller.handle({'action':'bypass', 'bypass':False})
        graph.update.assert_not_called()

    def test_lost_graph_can_be_started_again(self):
        controller = Controller('/unused')
        graph = Mock(profile=Profile('Saved', []), output='speakers')
        graph.status.return_value = {'running':False, 'connected':False, 'warning':'Gone'}
        graph.stop.side_effect = RuntimeError('Restoration failed')
        controller.graph = graph; controller.processing = True
        self.assertFalse(controller.check()['running'])
        self.assertIsNone(controller.graph)
        new = Mock(profile=Profile('New', []), output='speakers')
        new.status.return_value = {'running':True,'connected':True}
        new.snapshot.return_value = {'running':True,'connected':True}
        with patch('eqxear.service.Graph', return_value=new):
            self.assertTrue(controller.handle({'action':'start','profile':Profile('New', []).to_dict(),'output':'speakers'})['running'])
        new.start.assert_called_once()

    def test_stop_restores_only_owned_streams(self):
        graph = Graph('/unused', Profile('Test', []), 'speakers')
        graph.original_default = 'speakers'
        sinks = [{'index':1,'name':'speakers'},
                 {'index':2,'name':SINK,'properties':{'eqxear.instance':'other'}},
                 {'index':3,'name':SINK,'properties':{'eqxear.instance':graph.instance}}]
        streams = [{'index':11,'sink':2}, {'index':12,'sink':3}]
        with patch('eqxear.routing.pulse_list', side_effect=[sinks,streams]), patch('eqxear.routing.command') as command:
            warning = graph.stop()
        self.assertIn('default output was left unchanged', warning)
        command.assert_called_once_with(['pactl','move-sink-input','12','speakers'])

    def test_command_budget_is_shared_across_commands(self):
        clock = [100.0]
        timeouts = []
        def run(args, **kwargs):
            timeouts.append(kwargs['timeout'])
            clock[0] += 1.25
            return Mock(returncode=0, stdout='', stderr='')
        with patch('eqxear.routing.time.monotonic', side_effect=lambda: clock[0]), \
             patch('eqxear.routing.subprocess.run', side_effect=run):
            with command_budget(2):
                command(['first'])
                command(['second'])
                with self.assertRaises(TimeoutError):
                    command(['must-not-start'])
            command(['outside'])
        self.assertEqual(timeouts, [2, .75, 5])

    def test_hung_restore_still_terminates_child_with_bounded_waits(self):
        graph = Graph('/unused', Profile('Test', []), 'speakers')
        child = Mock(); child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('child', 1), 0]
        graph.process = child
        def hung(args, **kwargs):
            self.assertLessEqual(kwargs['timeout'], 2)
            raise subprocess.TimeoutExpired(args, kwargs['timeout'])
        with patch('eqxear.routing.subprocess.run', side_effect=hung):
            self.assertTrue(graph.stop())
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual([c.kwargs['timeout'] for c in child.wait.call_args_list], [1, 1])
        self.assertIsNone(graph.process)

    def test_runtime_rejects_symlink_and_repairs_permissions(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_RUNTIME_DIR':directory}):
            path = runtime()
            path.chmod(0o755)
            self.assertEqual(runtime().stat().st_mode & 0o777, 0o700)
            path.rmdir()
            target = Path(directory)/'other'; target.mkdir()
            path.symlink_to(target, target_is_directory=True)
            with self.assertRaises(RuntimeError):
                runtime()

    def test_requests_reject_invalid_structure_and_flags(self):
        controller = Controller('/unused')
        for request in ([], None, {}, {'action':'bad'}, {'action':'bypass','bypass':'false'}, {'action':'output','output':42}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                controller.handle(request)
