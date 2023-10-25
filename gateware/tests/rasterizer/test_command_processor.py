import random
import struct

from amaranth.sim import *
from zynq_gpu.rasterizer.command_processor import Command, CommandProcessor
import unittest
from .utils import Vertex
from ..utils import wait_until, AxiEmulator, make_testbench_process


def pack_vertex(v: Vertex):
    return v.x | (v.y << 11) | (v.z << 22) | (v.r << 38) | (v.g << 46) | (v.b << 54)


class CommandProcessorTest(unittest.TestCase):
    def test_triangles(self):
        dut = CommandProcessor()

        base_addr = 0x4000_0000

        triangles = [
            (
                Vertex(0x1, 0x2, 0x3, 0x4, 0x5, 0x6),
                Vertex(0x7, 0x8, 0x9, 0xA, 0xB, 0xC),
                Vertex(0xD, 0xE, 0xF, 0x0, 0x1, 0x2),
                False,
                0b00,
            ),
            (
                Vertex(0x3, 0x4, 0x5, 0x6, 0x7, 0x8),
                Vertex(0x9, 0xA, 0xB, 0xC, 0xD, 0xE),
                Vertex(0xF, 0x0, 0x1, 0x2, 0x3, 0x4),
                True,
                0b11,
            ),
            (
                Vertex(0x7FF, 0,     0,      0, 0, 0),
                Vertex(0,     0x7FF, 0,      0, 0, 0),
                Vertex(0,     0,     0xFFFF, 0, 0, 0),
                False,
                0b11,
            ),
            (
                Vertex(0, 0, 0, 0xFF, 0,    0),
                Vertex(0, 0, 0, 0,    0xFF, 0),
                Vertex(0, 0, 0, 0,    0,    0xFF),
                True,
                0b00,
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
            triangles += [(rand_vert(), rand_vert(), rand_vert(), random.randrange(2), random.randrange(4))]

        command_mem = bytes()
        for triangle in triangles:
            command_mem += bytes([
                *struct.pack("<I", Command.DRAW_TRIANGLE.value | (int(triangle[3]) << 8) | (triangle[4] << 9)),
                *struct.pack("<3Q", *[pack_vertex(v) for v in triangle[:3]]),
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
                texture_enable = (yield dut.triangles.payload.texture_enable)
                texture_buffer = (yield dut.triangles.payload.texture_buffer)
                assert texture_enable == int(t[3])
                assert texture_buffer == t[4]
                for sig, v in [("v0", t[0]), ("v1", t[1]), ("v2", t[2])]:
                    p_v = getattr(dut.triangles.payload, sig)
                    for attr in "xyzrgb":
                        expected = getattr(v, attr)
                        actual = (yield getattr(p_v, attr))
                        assert expected == actual, (f"Mismatch at {i}.{sig}.{attr} ({t}): "
                                                    f"expected {expected}, got {actual}")
                yield

        sim = Simulator(dut)
        emulator.add_to_sim(sim)
        sim.add_sync_process(make_testbench_process(control))
        sim.add_sync_process(make_testbench_process(check))
        sim.add_clock(1e-6)
        sim.run()

    def test_texture(self):
        dut = CommandProcessor()

        base_addr = 0x4000_0000
        command_mem = bytes()

        textures = [
            bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99, 0xAA, 0xBB, 0xCC] * (128 * 128 // 4)),
        ]

        for buffer, tex in enumerate(textures):
            command_mem += struct.pack("<I", Command.READ_TEXTURE.value | (buffer << 8))
            command_mem += tex
        assert len(command_mem) % 4 == 0

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
            yield from wait_until(dut.control.idle, len(textures) * (128 * 128 // 2 * 3) + 30)

        def check():
            yield dut.triangles.ready.eq(1)
            for i, t in enumerate(textures):
                for addr, pixel_bytes in enumerate(zip(*([iter(t)] * 6), strict=True)):
                    pixel1 = (pixel_bytes[0] << 0) | (pixel_bytes[1] << 8) | (pixel_bytes[2] << 16)
                    pixel2 = (pixel_bytes[3] << 0) | (pixel_bytes[4] << 8) | (pixel_bytes[5] << 16)
                    data = pixel1 | (pixel2 << 24)
                    yield from wait_until(dut.texture_writes.en)
                    act_buffer = (yield dut.texture_writes.buffer)
                    act_addr = (yield dut.texture_writes.addr)
                    act_data = (yield dut.texture_writes.data)
                    assert act_buffer == i, f"{act_buffer} / {i}"
                    assert act_addr == addr, f"{hex(act_addr)} / {hex(addr)}"
                    assert act_data == data, f"{hex(addr)}: {hex(act_data)} / {hex(data)}"

                    yield

        sim = Simulator(dut)
        emulator.add_to_sim(sim)
        sim.add_sync_process(make_testbench_process(control))
        sim.add_sync_process(make_testbench_process(check))
        sim.add_clock(1e-6)
        sim.run()
