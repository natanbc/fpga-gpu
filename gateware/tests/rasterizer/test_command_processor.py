import random
import struct

from amaranth.sim import *
from zynq_gpu.rasterizer.command_processor import Command, CommandProcessor
import unittest
from .utils import Vertex
from ..utils import wait_until, AxiEmulator


def pack_vertex(v: Vertex):
    return v.x | (v.y << 11) | (v.z << 22) | (v.r << 38) | (v.g << 46) | (v.b << 54)


class CommandProcessorTest(unittest.TestCase):
    def test(self):
        dut = CommandProcessor()

        base_addr = 0x4000_0000

        triangles = [
            (
                Vertex(0x1, 0x2, 0x3, 0x4, 0x5, 0x6),
                Vertex(0x7, 0x8, 0x9, 0xA, 0xB, 0xC),
                Vertex(0xD, 0xE, 0xF, 0x0, 0x1, 0x2),
            ),
            (
                Vertex(0x3, 0x4, 0x5, 0x6, 0x7, 0x8),
                Vertex(0x9, 0xA, 0xB, 0xC, 0xD, 0xE),
                Vertex(0xF, 0x0, 0x1, 0x2, 0x3, 0x4),
            ),
            (
                Vertex(0x7FF, 0,     0,      0, 0, 0),
                Vertex(0,     0x7FF, 0,      0, 0, 0),
                Vertex(0,     0,     0xFFFF, 0, 0, 0),
            ),
            (
                Vertex(0, 0, 0, 0xFF, 0,    0),
                Vertex(0, 0, 0, 0,    0xFF, 0),
                Vertex(0, 0, 0, 0,    0,    0xFF),
            ),
        ]
        for _ in range(10):
            def rand_vert():
                return Vertex(
                    random.randrange(1 << 11),
                    random.randrange(1 << 11),
                    random.randrange(1 << 16),
                    random.randrange(1 << 8),
                    random.randrange(1 << 8),
                    random.randrange(1 << 8),
                )
            triangles += [(rand_vert(), rand_vert(), rand_vert())]

        command_mem = bytes()
        for triangle in triangles:
            command_mem += bytes([
                *struct.pack("<I", Command.TRIANGLE.value),
                *struct.pack("<3Q", *[pack_vertex(v) for v in triangle]),
            ])

        def read(addr, _):
            off = addr - base_addr
            return struct.unpack("<I", command_mem[off:off+4])[0]

        emulator = AxiEmulator(dut.axi, read, None)

        def control():
            yield dut.control.base_addr.eq(base_addr >> 6)
            yield dut.control.words.eq(len(command_mem) // 4)
            yield dut.control.trigger.eq(1)
            yield
            yield dut.control.trigger.eq(0)

            while (yield dut.control.idle):
                yield
            yield from wait_until(dut.control.idle, 1000)

        def check():
            yield dut.triangles.ready.eq(1)
            for i, t in enumerate(triangles):
                yield from wait_until(dut.triangles.valid)
                for sig, v in [("v0", t[0]), ("v1", t[1]), ("v2", t[2])]:
                    p_v = getattr(dut.triangles.payload, sig)
                    for attr in "xyzrgb":
                        expected = getattr(v, attr)
                        actual = (yield getattr(p_v, attr))
                        assert expected == actual, (f"Mismatch at {i}.{sig}.{attr} ({t}): "
                                                    f"expected {expected}, got {actual}")

        sim = Simulator(dut)
        emulator.add_to_sim(sim)
        sim.add_sync_process(control)
        sim.add_sync_process(check)
        sim.add_clock(1e-6)
        sim.run()
