# SPDX-License-Identifier: Apache-2.0
import cmath
from concurrent.futures import ThreadPoolExecutor
import math
import random
import unittest
from eqxear.fft import RealFFT


class FFTTests(unittest.TestCase):
    def transform(self, values):
        fft = RealFFT(len(values))
        try:
            fft.input[:] = values
            fft.execute()
            return [complex(fft.output[2*k],fft.output[2*k+1]) for k in range(len(values)//2+1)]
        finally:
            fft.close()

    def test_matches_independent_direct_transform(self):
        randomizer = random.Random(71)
        for n in (2,4,16,64,128):
            cases = [[0.]*n, [1.]+[0.]*(n-1), [1.]*n,
                     [(-1.)**i for i in range(n)],
                     [randomizer.uniform(-1,1) for _ in range(n)]]
            for values in cases:
                with self.subTest(size=n, values=values[:3]):
                    actual = self.transform(values)
                    expected = [sum(v*cmath.exp(-2j*math.pi*k*i/n) for i,v in enumerate(values))
                                for k in range(n//2+1)]
                    for a,b in zip(actual,expected):
                        self.assertLess(abs(a-b),1e-10)

    def test_reuse_and_closed_transform(self):
        fft = RealFFT(8192)
        fft.input[0] = 1
        fft.execute()
        self.assertAlmostEqual(fft.output[8192],1)
        fft.input[0] = 0
        fft.execute()
        self.assertTrue(all(value == 0 for value in fft.output))
        fft.close(); fft.close()
        with self.assertRaises(RuntimeError):
            fft.execute()

    def test_independent_instances_in_parallel(self):
        values = [math.sin(i*.31) for i in range(1024)]
        expected = self.transform(values)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for result in pool.map(self.transform,[values]*8):
                self.assertEqual(result,expected)

    def test_invalid_sizes(self):
        for size in (0,1,3,-2,2**21,2.5):
            with self.assertRaises(ValueError):
                RealFFT(size)
