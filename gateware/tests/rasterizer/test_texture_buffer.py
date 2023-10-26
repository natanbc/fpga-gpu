from amaranth import Module, ClockDomain
from amaranth.sim import *
from zynq_gpu.rasterizer import TextureBuffer, TextureBufferRead
import unittest
from ..utils import make_testbench_process


def texture_emulator(buffers, read):
    yield Passive()

    read1 = None
    sel1 = None

    while True:
        read2 = read1
        sel2 = sel1
        if (yield read.en):
            buf, s, t = (yield read.buffer), (yield read.s), (yield read.t)
            addr = (s * 128 + t) // 2
            read1 = int.from_bytes(buffers[buf][addr*6:(addr+1)*6], byteorder="little")
        sel1 = (yield read.t) & 1

        if sel2 is not None and read2 is not None:
            yield read.color.eq(read2 >> (24 * sel2))

        yield


def check_values(read):
    yield
    yield

    yield read.s.eq(0)
    yield read.t.eq(0)
    yield read.en.eq(1)
    yield

    pix = (yield read.color)
    assert pix == 0, f"{hex(pix)}"

    yield read.en.eq(0)
    yield read.t.eq(1)
    yield

    pix = (yield read.color)
    assert pix == 0xBBBCBD, f"{hex(pix)}"

    # Should return value in t=0, because en=0
    yield read.t.eq(2)
    yield

    pix = (yield read.color)
    assert pix == 0xAAABAC, f"{hex(pix)}"

    yield read.en.eq(1)
    yield

    pix = (yield read.color)
    assert pix == 0xBBBCBD, f"{hex(pix)}"

    yield

    pix = (yield read.color)
    assert pix == 0xDDDEDF, f"{hex(pix)}"


class TextureBufferTest(unittest.TestCase):
    def test_rtl(self):
        dut = TextureBuffer(_test_side=16)

        def test():
            yield dut.write.en.eq(1)
            yield dut.write.addr.eq(0)
            yield dut.write.data.eq(0xAAABAC_BBBCBD)
            yield
            yield dut.write.addr.eq(1)
            yield dut.write.data.eq(0xCCCDCE_DDDEDF)
            yield
            yield dut.write.en.eq(0)
            yield

            yield from check_values(dut.read)

        sim = Simulator(dut)
        sim.add_sync_process(make_testbench_process(test))
        sim.add_clock(1e-6)
        sim.run()

    def test_emulation(self):
        read = TextureBufferRead.create()

        def emulator():
            buffers = [
                bytes([0xBD, 0xBC, 0xBB, 0xAC, 0xAB, 0xAA, 0xDF, 0xDE, 0xDD, 0xCE, 0xCD, 0xCC])
            ]
            yield from texture_emulator(buffers, read)

        def test():
            yield from check_values(read)

        m = Module()
        m.domains += ClockDomain("sync")
        sim = Simulator(m)
        sim.add_sync_process(emulator)
        sim.add_sync_process(make_testbench_process(test))
        sim.add_clock(1e-6)
        sim.run()
