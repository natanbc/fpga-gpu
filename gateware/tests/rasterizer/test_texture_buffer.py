from amaranth.sim import *
from zynq_gpu.rasterizer import TextureBuffer
import unittest
from ..utils import make_testbench_process


class TextureBufferTest(unittest.TestCase):
    def test(self):
        dut = TextureBuffer(_test_side=16)

        def test():
            yield dut.write.en.eq(1)
            yield dut.write.addr.eq(0)
            yield dut.write.data.eq(0xAAAAAA_BBBBBB)
            yield
            yield dut.write.addr.eq(1)
            yield dut.write.data.eq(0xCCCCCC_DDDDDD)
            yield
            yield dut.write.en.eq(0)
            yield

            yield dut.read.s.eq(0)
            yield dut.read.t.eq(0)
            yield dut.read.en.eq(1)
            yield

            pix = (yield dut.read.color)
            assert pix == 0, f"{hex(pix)}"

            yield dut.read.en.eq(0)
            yield dut.read.t.eq(1)
            yield

            pix = (yield dut.read.color)
            assert pix == 0xBBBBBB, f"{hex(pix)}"

            # Should return value in t=0, because en=0
            yield dut.read.t.eq(2)
            yield

            pix = (yield dut.read.color)
            assert pix == 0xAAAAAA, f"{hex(pix)}"

            yield dut.read.en.eq(1)
            yield

            pix = (yield dut.read.color)
            assert pix == 0xBBBBBB, f"{hex(pix)}"

            yield

            pix = (yield dut.read.color)
            assert pix == 0xDDDDDD, f"{hex(pix)}"

        sim = Simulator(dut)
        sim.add_sync_process(make_testbench_process(test))
        sim.add_clock(1e-6)
        sim.run()
