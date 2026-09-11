# SPDX-License-Identifier: Apache-2.0
"""Run the actual native plugin without speakers or an audio server."""
import ctypes as c
import math
import json
from pathlib import Path
import unittest
from eqxear.build import build_plugin
from eqxear.model import Band, Profile

Instantiate=c.CFUNCTYPE(c.c_void_p,c.c_void_p,c.c_double,c.c_char_p,c.c_void_p)
Connect=c.CFUNCTYPE(None,c.c_void_p,c.c_uint32,c.c_void_p)
Run=c.CFUNCTYPE(None,c.c_void_p,c.c_uint32)
Cleanup=c.CFUNCTYPE(None,c.c_void_p)
class Descriptor(c.Structure):
    _fields_=[('uri',c.c_char_p),('instantiate',Instantiate),('connect',Connect),('activate',c.c_void_p),('run',Run),('deactivate',c.c_void_p),('cleanup',Cleanup),('extension',c.c_void_p)]

class Plugin:
    def __init__(self,rate=48000):
        self.rate=rate
        self.lib=c.CDLL(str(build_plugin()/'eqxear.lv2/eq.so'))
        self.lib.lv2_descriptor.restype=c.POINTER(Descriptor)
        ptr=self.lib.lv2_descriptor(0);self.desc=ptr.contents
        self.instance=self.desc.instantiate(ptr,rate,None,None)
        self.buffers=[(c.c_float*512)() for _ in range(4)]
        self.controls=[c.c_float(0) for _ in range(35)]
        for i in range(8): self.controls[3+4*i+1].value=1000;self.controls[3+4*i+3].value=1
        for i,buffer in enumerate(self.buffers): self.desc.connect(self.instance,i,buffer)
        for i,value in enumerate(self.controls,4):self.desc.connect(self.instance,i,c.byref(value))
        self.sample=0

    def measure(self,freq=1000,blocks=100):
        ss=cc=sc=ys=yc=0.;peak=0
        for block in range(blocks):
            for i in range(512):
                self.buffers[0][i]=.1*math.sin(2*math.pi*freq*(self.sample+i)/self.rate)
                self.buffers[1][i]=0
            self.desc.run(self.instance,512)
            for i in range(512):
                out=self.buffers[2][i]; assert math.isfinite(out)
                peak=max(peak,abs(out))
                if block>=blocks-10:
                    angle=2*math.pi*freq*(self.sample+i)/self.rate
                    sn,cs=math.sin(angle),math.cos(angle)
                    ss+=sn*sn;cc+=cs*cs;sc+=sn*cs;ys+=out*sn;yc+=out*cs
                assert abs(self.buffers[3][i])<1e-10
            self.sample+=512
        determinant=ss*cc-sc*sc
        amplitude=math.hypot((ys*cc-yc*sc)/determinant,(yc*ss-ys*sc)/determinant)
        return 20*math.log10(max(amplitude,1e-100)/.1),peak
    def close(self):self.desc.cleanup(self.instance)

class NativeTests(unittest.TestCase):
    def test_live_bell_gain_preamp_bypass_mute(self):
        p=Plugin()
        try:
            self.assertAlmostEqual(p.measure()[0],0,places=4)
            p.controls[3].value=1;p.controls[5].value=6
            self.assertAlmostEqual(p.measure()[0],6,places=3)
            p.controls[0].value=-6
            self.assertAlmostEqual(p.measure()[0],0,places=3)
            p.controls[5].value=-12
            self.assertAlmostEqual(p.measure()[0],-18,places=3)
            p.controls[2].value=1
            self.assertAlmostEqual(p.measure()[0],0,places=3)
            p.controls[2].value=0;p.controls[1].value=1
            self.assertLess(p.measure()[0],-300)
            p.controls[1].value=0
            self.assertAlmostEqual(p.measure()[0],-18,places=3)
        finally:p.close()

    def test_shelves_and_sample_rates(self):
        for rate in (44100,48000,96000):
            p=Plugin(rate)
            try:
                p.controls[3].value=2;p.controls[4].value=1000;p.controls[5].value=6;p.controls[6].value=.707
                self.assertAlmostEqual(p.measure(1000)[0],3,places=3)
                p.controls[3].value=3
                self.assertAlmostEqual(p.measure(1000)[0],3,places=3)
            finally:p.close()

    def test_extreme_control_changes_remain_finite(self):
        p=Plugin()
        try:
            for kind in (1,2,3,0):
                for gain in (-12,12):
                    p.controls[3].value=kind;p.controls[4].value=29;p.controls[5].value=gain;p.controls[6].value=.3
                    level,peak=p.measure(29,40)
                    self.assertLess(peak,20)
        finally:p.close()

    def test_invalid_samples_recover_and_leave_other_channel_unchanged(self):
        p, reference = Plugin(), Plugin()
        try:
            for plugin in (p, reference):
                plugin.controls[3].value=1; plugin.controls[5].value=6
            for malformed in (float('nan'), float('inf'), -float('inf')):
                for plugin in (p, reference):
                    for channel in (0, 1):
                        for i in range(512):
                            plugin.buffers[channel][i]=.1*math.sin(i*.13)
                p.buffers[0][113]=malformed
                for plugin in (p, reference): plugin.desc.run(plugin.instance,512)
                self.assertEqual(list(p.buffers[3]),list(reference.buffers[3]))
                self.assertTrue(all(math.isfinite(value) for value in p.buffers[2]))
                self.assertGreater(max(abs(value) for value in p.buffers[2][114:]),.05)
                for plugin in (p, reference):
                    for channel in (0, 1):
                        for i in range(512): plugin.buffers[channel][i]=0
                    for _ in range(20): plugin.desc.run(plugin.instance,512)
                    self.assertAlmostEqual(plugin.measure()[0],6,places=3)
        finally:
            p.close(); reference.close()

    def test_float_output_overflow_recovers_without_limiting_regular_boosts(self):
        p=Plugin()
        try:
            p.controls[0].value=18
            p.measure()
            p.buffers[0][0]=c.c_float(3.4028234663852886e38).value
            p.desc.run(p.instance,512)
            self.assertEqual(p.buffers[2][0],0)
            self.assertTrue(all(math.isfinite(value) for value in p.buffers[2]))
            self.assertAlmostEqual(p.measure()[0],18,places=3)
            for i in range(512): p.buffers[0][i]=.5
            p.desc.run(p.instance,512)
            self.assertGreater(p.buffers[2][511],1)
        finally:p.close()

    def test_bypass_preserves_finite_samples_and_rejects_invalid_samples(self):
        p=Plugin()
        try:
            p.controls[2].value=1
            p.measure()
            values=[-2.5,0,.25,2.5,float('nan'),float('inf')]
            for i,value in enumerate(values):p.buffers[0][i]=value
            p.desc.run(p.instance,512)
            self.assertEqual(list(p.buffers[2])[:6],[-2.5,0,.25,2.5,0,0])
        finally:p.close()

    def test_corrupted_history_is_reset(self):
        class Coeff(c.Structure):
            _fields_=[(name,c.c_double) for name in ('b0','b1','b2','a1','a2')]
        class History(c.Structure):
            _fields_=[(name,c.c_double) for name in ('x1','x2','y1','y2')]
        class State(c.Structure):
            _fields_=[('ports',c.c_void_p*39),('rate',c.c_double),('gain',c.c_double),
                      ('target_gain',c.c_double),('coeff',Coeff*8),('target',Coeff*8),
                      ('history',(History*8)*2)]
        p=Plugin()
        try:
            p.measure()
            state=c.cast(p.instance,c.POINTER(State)).contents
            state.history[0][3].y1=float('nan')
            self.assertAlmostEqual(p.measure()[0],0,places=4)
            self.assertTrue(math.isfinite(state.history[0][3].y1))
        finally:p.close()


    def test_shared_response_fixtures(self):
        fixtures = json.loads((Path(__file__).parent/'fixtures/response-cases.json').read_text())
        for case in fixtures['cases']:
            profile = Profile.from_dict(case['profile'])
            plugin = Plugin(case['sample_rate_hz'])
            try:
                plugin.controls[0].value = profile.preamp
                for i, band in enumerate(profile.bands):
                    for offset,value in enumerate(({'Bell':1,'Lo-shelf':2,'Hi-shelf':3}[band.kind],band.frequency,band.gain,band.q)):
                        plugin.controls[3+4*i+offset].value = value
                for point in case['response_points']:
                    with self.subTest(case=case['id'],frequency=point['frequency_hz']):
                        self.assertAlmostEqual(plugin.measure(point['frequency_hz'])[0],point['gain_db'],delta=fixtures['tolerance_db'])
            finally: plugin.close()

    def test_normalization_matches_native_at_multiple_rates(self):
        profile = Profile('Treble overlap',[Band(12000,6,.3),Band(20000,6,.3)])
        for rate in (32000,44100,48000,96000):
            plugin = Plugin(rate)
            try:
                preamp = profile.normalization_preamp(rate)
                plugin.controls[0].value = preamp
                for i,band in enumerate(profile.bands):
                    for offset,value in enumerate((1,band.frequency,band.gain,band.q)):
                        plugin.controls[3+4*i+offset].value = value
                frequencies = [20*((rate*.499)/20)**(i/3000) for i in range(3001)]
                peak, frequency = max((profile.response(f,rate),f) for f in frequencies)
                actual = plugin.measure(frequency)[0]
                self.assertAlmostEqual(actual,peak+preamp,delta=1e-5)
                self.assertLessEqual(actual,1e-6)
                self.assertGreater(actual,-.101)
            finally: plugin.close()
