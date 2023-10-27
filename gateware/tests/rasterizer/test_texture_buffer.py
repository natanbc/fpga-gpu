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

    expected = [
        0xBBBCBD,
        0xAAABAC,
        0xDDDEDF,
        0xCCCDCE,
        0xFFF0F1,
        0xEEEFE0,
        0x111213,
        0x000102,
    ]
    expected += [x ^ 0x555555 for x in expected]
    expected += [x ^ 0xAAAAAA for x in expected]
    expected += [x ^ 0x696969 for x in expected]
    expected += [x ^ 0x424242 for x in expected]
    expected += [x ^ 0xFFFFFF for x in expected]
    for buffer in range(4):
        flips = (0x121212 << buffer) if buffer != 0 else 0
        yield read.buffer.eq(buffer)
        for i in range(len(expected) + 2):
            if i < len(expected):
                yield read.s.eq(i // 128)
                yield read.t.eq(i % 128)
                yield read.en.eq(1)
            else:
                yield read.en.eq(0)
            if i >= 2:
                pix = (yield read.color)
                assert pix ^ flips == expected[i - 2], f"{i - 2}: {hex(pix)} / {hex(expected[i - 2])}"
            yield


class TextureBufferTest(unittest.TestCase):
    def test_rtl(self):
        dut = TextureBuffer()

        def test():
            data = [
                0xAAABAC_BBBCBD,
                0xCCCDCE_DDDEDF,
                0xEEEFE0_FFF0F1,
                0x000102_111213,
            ]
            data += [x ^ 0x555555_555555 for x in data]
            data += [x ^ 0xAAAAAA_AAAAAA for x in data]
            data += [x ^ 0x696969_696969 for x in data]
            data += [x ^ 0x424242_424242 for x in data]
            data += [x ^ 0xFFFFFF_FFFFFF for x in data]
            for buffer in range(4):
                flips = (0x121212_121212 << buffer) if buffer != 0 else 0
                yield dut.write.buffer.eq(buffer)
                for i, v in enumerate(data):
                    yield dut.write.en.eq(1)
                    yield dut.write.addr.eq(i)
                    yield dut.write.data.eq(v ^ flips)
                    yield
            yield dut.write.en.eq(0)

            yield from check_values(dut.read)

        sim = Simulator(dut)
        sim.add_sync_process(make_testbench_process(test))
        sim.add_clock(1e-6)
        sim.run()

    def test_emulation(self):
        read = TextureBufferRead.create()

        def emulator():
            buf = bytes([
                0xBD, 0xBC, 0xBB, 0xAC, 0xAB, 0xAA,
                0xDF, 0xDE, 0xDD, 0xCE, 0xCD, 0xCC,
                0xF1, 0xF0, 0xFF, 0xE0, 0xEF, 0xEE,
                0x13, 0x12, 0x11, 0x02, 0x01, 0x00,
            ])
            buf += bytes(x ^ 0x55 for x in buf)
            buf += bytes(x ^ 0xAA for x in buf)
            buf += bytes(x ^ 0x69 for x in buf)
            buf += bytes(x ^ 0x42 for x in buf)
            buf += bytes(x ^ 0xFF for x in buf)
            buffers = [
                bytes(((0x12 << i) if i != 0 else 0) ^ x for x in buf) for i in range(4)
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
