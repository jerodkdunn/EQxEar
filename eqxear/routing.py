# SPDX-License-Identifier: Apache-2.0
"""PipeWire graph and Pulse-compatible desktop routing."""
import json
from contextlib import contextmanager
from contextvars import ContextVar
import os
import re
from pathlib import Path
import subprocess
import time
import sys
import uuid
from .build import build_plugin, URI

SINK = 'eqxear_v2'
# EasyEffects excludes output_level nodes from automatic stream capture.
# Name this actual output/level stage accordingly, and identify its DSP role.
PLAYBACK = 'eqxear_v2_output_level'


class GraphUnavailable(RuntimeError):
    """The owned PipeWire graph is gone or its control channel failed."""


def spa(value):
    if isinstance(value,dict): return '{ '+' '.join(json.dumps(k)+' = '+spa(v) for k,v in value.items())+' }'
    if isinstance(value,list): return '[ '+' '.join(spa(v) for v in value)+' ]'
    return json.dumps(value)

_command_deadline = ContextVar('eqxear_command_deadline', default=None)


@contextmanager
def command_budget(seconds):
    deadline = time.monotonic()+seconds
    outer = _command_deadline.get()
    token = _command_deadline.set(min(deadline, outer) if outer is not None else deadline)
    try:
        yield
    finally:
        _command_deadline.reset(token)


def command(args, timeout=5):
    deadline = _command_deadline.get()
    if deadline is not None:
        timeout = min(timeout, deadline-time.monotonic())
        if timeout <= 0:
            raise TimeoutError('Audio command time budget expired')
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Command failed: '+args[0])
    return result.stdout.strip()

def pulse_list(kind):
    return json.loads(command(['pactl','-f','json','list',kind]))

def outputs():
    devices = [(s['name'],s.get('description',s['name'])) for s in pulse_list('sinks') if s['name'] not in (SINK,'easyeffects_sink')]
    default = command(['pactl','get-default-sink'])
    return sorted(devices,key=lambda d:d[0]!=default)

def controls(profile, bypass=False):
    profile.validate()
    result = {'preamp':profile.preamp,'mute':float(profile.muted),'bypass':float(bypass)}
    for i in range(8):
        b = profile.bands[i] if i<len(profile.bands) else None
        result.update({f'b{i}_type': {'Bell':1,'Lo-shelf':2,'Hi-shelf':3}[b.kind] if b and b.enabled else 0,
                       f'b{i}_freq':b.frequency if b else 1000, f'b{i}_gain':b.gain if b else 0, f'b{i}_q':b.q if b else 1})
    return result

class Graph:
    def __init__(self, directory, profile, output, bypass=False):
        self.directory = Path(directory)
        self.profile, self.output = profile, output
        self.process = None
        self.original_default = None
        self.moved = {}
        self.node_id = None
        self.bypassed = bypass
        self.routing_warning = ''
        self.instance = uuid.uuid4().hex
        self.log = None
        self.sample_rate = None
        self.rate_offset = 0
        self.rate_pending = b''

    def start(self):
        lv2 = build_plugin()
        # A legacy orphan or another application instance is not ours to control.
        deadline = time.monotonic()+3
        while any(n.get('info', {}).get('props', {}).get('node.name') == SINK
                  for n in self.nodes()):
            if time.monotonic() >= deadline:
                raise RuntimeError('An unmanaged EQxEar output already exists. Stop the older instance before starting EQxEar.')
            time.sleep(.1)
        available = outputs()
        if self.output not in dict(available):
            raise RuntimeError('The selected output is disconnected.')
        self.original_default = command(['pactl','get-default-sink'])
        config = {
            'context.properties': {'log.level': 1},
            'context.spa-libs': {'audio.convert.*':'audioconvert/libspa-audioconvert','support.*':'support/libspa-support'},
            'context.modules': [
                {'name':'libpipewire-module-rt','flags':['ifexists','nofail']},
                {'name':'libpipewire-module-protocol-native'},
                {'name':'libpipewire-module-client-node'},
                {'name':'libpipewire-module-adapter'},
                {'name':'libpipewire-module-filter-chain','args': {
                    'node.description':'EQxEar', 'media.name':'EQxEar',
                    'filter.graph': {'nodes':[{'type':'lv2','name':'eq','plugin':URI,'control':controls(self.profile,self.bypassed)}],
                                     'inputs':['eq:in_l','eq:in_r'],'outputs':['eq:out_l','eq:out_r']},
                    'audio.channels':2,'audio.position':['FL','FR'],
                    'capture.props': {'eqxear.instance':self.instance,'node.name':SINK,'node.description':'EQxEar','media.class':'Audio/Sink',
                                      'node.virtual':True,'priority.session':0,'node.latency':'256/48000'},
                    'playback.props': {'eqxear.instance':self.instance,'node.name':PLAYBACK,'node.description':'EQxEar output',
                                       'media.role':'DSP','media.category':'Filter',
                                       'application.name':'EQxEar','application.id':'com.eqxear.App',
                                       'node.passive':True,'target.object':self.output,'node.dont-fallback':True,
                                       'stream.dont-remix':False,'node.latency':'256/48000'},
                }},
            ],
        }
        path = self.directory/'pipewire.conf'; path.write_text('\n'.join(key+' = '+spa(value) for key,value in config.items()))
        env = dict(os.environ, LV2_PATH=str(lv2)+':'+os.environ.get('LV2_PATH','/usr/lib/lv2'), EQXEAR_REPORT_RATE='1')
        self.log = (self.directory/'audio.log').open('a')
        self.rate_offset = os.fstat(self.log.fileno()).st_size
        self.rate_pending = b''
        self.sample_rate = None
        try:
            self.process = subprocess.Popen([sys.executable, '-m', 'eqxear.child', str(os.getpid()),
                                             'pipewire','-c',str(path)], cwd=Path(__file__).resolve().parents[1],
                                            env=env, stdout=self.log, stderr=self.log)
        except Exception:
            self.log.close(); self.log = None
            raise
        try:
            deadline = time.monotonic()+8
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError('PipeWire EQ stopped: '+(self.directory/'audio.log').read_text()[-2000:])
                nodes = self.nodes()
                found = [n for n in nodes if self.owns(n, SINK)]
                if found and any(self.owns(n, PLAYBACK) for n in nodes) and any(s['name'] == SINK and s.get('properties', {}).get('eqxear.instance') == self.instance for s in pulse_list('sinks')):
                    self.node_id = found[0]['id']; break
                time.sleep(.1)
            if self.node_id is None:
                raise RuntimeError('The EQxEar PipeWire sink did not become available.')
            self.update(self.profile)
            command(['pactl','set-default-sink',SINK])
            self.move_streams()
        except Exception:
            try:
                (self.directory/'failed.json').write_text(command(['pw-dump'], timeout=1))
            except Exception:
                pass
            self.stop()
            raise

    @staticmethod
    def nodes():
        values = json.loads(command(['pw-dump'], timeout=2))
        return [n for n in values if n.get('type') == 'PipeWire:Interface:Node' and isinstance(n.get('info'), dict)]

    def owns(self, node, name):
        props = node.get('info', {}).get('props', {})
        return props.get('node.name') == name and props.get('eqxear.instance') == self.instance

    def require_node(self):
        if self.process is None or self.process.poll() is not None:
            raise GraphUnavailable('The native audio engine is not running. Start system EQ to reconnect.')
        try:
            nodes = self.nodes()
        except Exception as error:
            raise GraphUnavailable('PipeWire is unavailable. Start system EQ after audio returns.') from error
        if not any(n['id'] == self.node_id and self.owns(n, SINK) for n in nodes):
            raise GraphUnavailable('The EQ audio graph was disconnected. Start system EQ to reconnect.')
        if not any(self.owns(n, PLAYBACK) for n in nodes):
            raise GraphUnavailable('The EQ output graph was disconnected. Start system EQ to reconnect.')

    def move_streams(self):
        sinks = {s['index']:s['name'] for s in pulse_list('sinks')}
        warnings = []
        for stream in pulse_list('sink-inputs'):
            props=stream.get('properties',{})
            name=props.get('node.name','')
            app=props.get('application.name','').lower()
            if name==PLAYBACK or 'easyeffects' in name.lower() or 'easy effects' in app:
                continue
            if sinks.get(stream['sink'])==SINK:
                continue
            index=str(stream['index'])
            self.moved.setdefault(index,sinks.get(stream['sink'],self.output))
            try: command(['pactl','move-sink-input',index,SINK])
            except RuntimeError: warnings.append(props.get('application.name',index))
        self.routing_warning = 'Could not route: '+', '.join(warnings) if warnings else ''

    def update(self, profile, bypass=None):
        self.require_node()
        bypass = self.bypassed if bypass is None else bypass
        params = []
        for key,value in controls(profile,bypass).items(): params.extend(['eq:'+key,float(value)])
        result = command(['pw-cli','set-param',str(self.node_id),'Props',spa({'params':params})])
        # pw-cli may report a missing object while exiting with status zero.
        if any(word in result.lower() for word in ('error', 'unknown', 'not found', 'invalid')):
            raise GraphUnavailable('PipeWire rejected the EQ update: '+result.strip())
        self.profile, self.bypassed = profile, bypass

    def select_output(self, output):
        self.require_node()
        if output not in dict(outputs()): raise RuntimeError('The selected output is disconnected.')
        streams = pulse_list('sink-inputs')
        playback = next((s for s in streams if s.get('properties',{}).get('eqxear.instance')==self.instance and s.get('properties',{}).get('node.name')==PLAYBACK),None)
        if playback is None: raise RuntimeError('The EQ output stream is not available.')
        command(['pactl','move-sink-input',str(playback['index']),output])
        self.output = output

    def read_sample_rate(self):
        """Read only this child's setup records, including later rate renegotiation."""
        try:
            with (self.directory/'audio.log').open('rb') as log:
                size = os.fstat(log.fileno()).st_size
                if size < self.rate_offset:
                    self.rate_offset = 0
                    self.rate_pending = b''
                    self.sample_rate = None
                if size-self.rate_offset > 65536:
                    self.rate_offset = size-65536
                    self.rate_pending = b''
                    self.sample_rate = None
                    log.seek(self.rate_offset)
                    log.readline()  # Discard a possible partial record.
                else:
                    log.seek(self.rate_offset)
                data = self.rate_pending+log.read(65536)
                self.rate_offset = log.tell()
            lines = data.split(b'\n')
            self.rate_pending = lines.pop()[-128:]
            for line in lines:
                match = re.fullmatch(rb'EQXEAR_SAMPLE_RATE=([0-9]{4,6})', line)
                if match:
                    rate = int(match[1])
                    self.sample_rate = rate if 1000 <= rate <= 768000 else None
        except OSError:
            self.sample_rate = None
        return self.sample_rate

    def snapshot(self):
        """Cheap command acknowledgement; status() performs the full health check."""
        alive = self.process is not None and self.process.poll() is None
        return {'running': alive, 'connected': alive, 'output': self.output,
                'bypassed': self.bypassed, 'profile': self.profile.to_dict(),
                'warning': self.routing_warning,
                'sample_rate': self.read_sample_rate() if alive else None}

    @command_budget(2)
    def status(self):
        result = self.snapshot()
        try:
            self.require_node()
            sinks = pulse_list('sinks')
            if not any(s.get('properties', {}).get('eqxear.instance') == self.instance and s['name'] == SINK for s in sinks):
                raise GraphUnavailable('The EQ playback device disappeared. Start system EQ to reconnect.')
            playback = next((s for s in pulse_list('sink-inputs')
                             if s.get('properties', {}).get('eqxear.instance') == self.instance
                             and s.get('properties', {}).get('node.name') == PLAYBACK), None)
            names = {s['index']:s['name'] for s in sinks}
            if playback is None:
                raise GraphUnavailable('The EQ output stream disappeared. Start system EQ to reconnect.')
            if self.output not in names.values():
                result['warning'] = 'Selected output is disconnected. Choose another output.'
                result['running'] = False
            elif names.get(playback['sink']) != self.output:
                result['warning'] = 'Another audio processor redirected the EQ output. Choose Use output again.'
                result['running'] = False
        except Exception as error:
            result.update(running=False, connected=False, warning=str(error))
        return result

    @command_budget(2)
    def stop(self):
        """Always tear down our child, even when restoring routing fails."""
        warnings = []
        try:
            sink_list = pulse_list('sinks')
            sinks={s['index']:s['name'] for s in sink_list}
            owned = {s['index'] for s in sink_list if s.get('properties', {}).get('eqxear.instance') == self.instance}
            if owned and list(sinks.values()).count(SINK) > 1:
                warnings.append('Another device uses the EQxEar output name. The default output was left unchanged; select your playback output if needed.')
            available=set(sinks.values())-{SINK,'easyeffects_sink'}
            target=self.original_default if self.original_default in available else self.output
            if target not in available: target=next(iter(available),None)
            if target:
                if owned and list(sinks.values()).count(SINK) == 1 and command(['pactl','get-default-sink'])==SINK:
                    command(['pactl','set-default-sink',target])
                for stream in pulse_list('sink-inputs'):
                    if stream['sink'] in owned:
                        old=self.moved.get(str(stream['index']),target)
                        if old not in available: old=target
                        try: command(['pactl','move-sink-input',str(stream['index']),old])
                        except Exception as error: warnings.append(str(error))
        except Exception as error:
            warnings.append(str(error))
        finally:
            process, self.process = self.process, None
            self.node_id = None
            self.sample_rate = None
            try:
                if process:
                    if process.poll() is None:
                        process.terminate()
                    try: process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(timeout=1)
            except ProcessLookupError:
                pass
            except Exception as error:
                warnings.append(str(error))
            finally:
                if self.log:
                    self.log.close(); self.log = None
        return '; '.join(warnings)
