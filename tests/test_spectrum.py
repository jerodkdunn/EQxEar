# SPDX-License-Identifier: Apache-2.0
import math
import unittest
from eqxear.spectrum import Spectrum, CENTERS

class SpectrumTests(unittest.TestCase):
    def test_silence_and_calibrated_stereo_tone(self):
        fft = Spectrum()
        try:
            self.assertEqual(fft.analyze([0.]*(fft.size*2)), (-90.,)*31)
            for frequency in (100,1000,10000):
                samples = []
                for i in range(fft.size):
                    v = .1*math.sin(2*math.pi*frequency*i/48000)
                    samples.extend((v,-v))  # Phase cancellation must not hide stereo energy.
                levels = fft.analyze(samples)
                peak = max(range(31),key=levels.__getitem__)
                self.assertAlmostEqual(CENTERS[peak],frequency,delta=frequency*.13)
                total = 10*math.log10(sum(10**(v/10) for v in levels))
                self.assertAlmostEqual(total,-23.0103,delta=.03)
        finally:
            fft.close()
