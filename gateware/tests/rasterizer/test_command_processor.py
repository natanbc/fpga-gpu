from dataclasses import dataclass
import random
import struct

from amaranth.sim import *
from zynq_gpu.rasterizer.command_processor import Command, CommandProcessor
import unittest
from .utils import Vertex
from ..utils import wait_until, AxiEmulator, make_testbench_process


@dataclass
class ReadTexture:
    buffer: int
    s_start: int
    s_end: int
    t_half_start: int
    t_half_end: int
    data: bytes


@dataclass
class BufferClear:
    pattern: int
    addr: int
    words: int


def pack_vertex(v: Vertex):
    return v.x | (v.y << 11) | (v.z << 22) | (v.r << 38) | (v.g << 46) | (v.b << 54)


def pack_read_texture(r: ReadTexture):
    assert 0 <= r.buffer < 4, f"{r.buffer}"
    assert 0 <= r.s_start < 128, f"{r.s_start}"
    assert 0 <= r.s_end < 128, f"{r.s_end}"
    assert 0 <= r.t_half_start < 64, f"{r.t_half_start}"
    assert 0 <= r.t_half_end < 64, f"{r.t_half_end}"

    assert r.s_start <= r.s_end, f"{r.s_start} / {r.s_end}"
    assert r.t_half_start < r.t_half_end, f"{r.t_half_start} / {r.t_half_end}"

    expected_len = (r.s_end - r.s_start + 1) * ((r.t_half_end - r.t_half_start + 1) * 2) * 3
    assert len(r.data) == expected_len, f"{len(r.data)} / {expected_len}"
    s_high = r.s_start >> 6
    assert s_high == (r.s_end >> 6), f"{r.s_start} / {r.s_end}"
    t_high = r.t_half_start >> 5
    assert t_high == (r.t_half_end >> 5), f"{r.t_half_start} / {r.t_half_end}"

    cmd = (
            Command.READ_TEXTURE.value |
            (r.buffer << 6) |
            (s_high << 8) |
            ((r.s_start & 0b111_111) << 9) |
            ((r.s_end & 0b111_111) << 15) |
            (t_high << 21) |
            ((r.t_half_start & 0b11_111) << 22) |
            ((r.t_half_end & 0b11_111) << 27)
    )
    return struct.pack("<I", cmd)


def pack_buffer_clear(c: BufferClear):
    cmd = (
        Command.CLEAR_BUFFER.value |
        (c.pattern << 8)
    )
    return struct.pack("<3I", cmd, c.addr, c.words)


def check_triangle(dut, i, triangle):
    texture_enable = (yield dut.triangles.payload.texture_enable)
    texture_buffer = (yield dut.triangles.payload.texture_buffer)
    assert texture_enable == int(triangle[3])
    assert texture_buffer == triangle[4]
    for sig, v in [("v0", triangle[0]), ("v1", triangle[1]), ("v2", triangle[2])]:
        p_v = getattr(dut.triangles.payload, sig)
        for attr in "xyzrgb":
            expected = getattr(v, attr)
            actual = (yield getattr(p_v, attr))
            assert expected == actual, (f"Mismatch at {i}.{sig}.{attr} ({triangle}): "
                                        f"expected {expected}, got {actual}")


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
                *struct.pack("<I", Command.DRAW_TRIANGLE.value | (int(triangle[3]) << 6) | (triangle[4] << 7)),
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
                yield from check_triangle(dut, i, t)
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

        # Too slow for normal simulations
        # t_128 = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99, 0xAA, 0xBB, 0xCC] * (128 * 128 // 4))
        # t_128_quarter = len(t_128) // 4
        # assert t_128_quarter % 6 == 0

        t_16 = bytes([0xAA, 0xBB, 0xCC] * 16 * 16)
        textures = [
            ReadTexture(1, 29, 29+16-1, 5, 5+(16//2)-1, t_16),
            # ReadTexture(0, 0, 63, 0, 31, t_128[0:t_128_quarter]),
            # ReadTexture(0, 0, 63, 32, 63, t_128[t_128_quarter:2*t_128_quarter]),
            # ReadTexture(0, 64, 127, 0, 31, t_128[2*t_128_quarter:3*t_128_quarter]),
            # ReadTexture(0, 64, 127, 32, 63, t_128[3*t_128_quarter:4*t_128_quarter]),
        ]
        triangle = (
            Vertex(0x1, 0x2, 0x3, 0x4, 0x5, 0x6),
            Vertex(0x7, 0x8, 0x9, 0xA, 0xB, 0xC),
            Vertex(0xD, 0xE, 0xF, 0x0, 0x1, 0x2),
            False,
            0b00,
        )

        for tex in textures:
            command_mem += pack_read_texture(tex)
            command_mem += tex.data
        command_mem += struct.pack("<I", Command.DRAW_TRIANGLE.value | (int(triangle[3]) << 8) | (triangle[4] << 9))
        command_mem += struct.pack("<3Q", *[pack_vertex(v) for v in triangle[:3]])

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
            for texture in textures:
                base = texture.s_start * 64 + texture.t_half_start
                width = texture.t_half_end - texture.t_half_start + 1

                def idx_to_addr(i):
                    return base + 64 * (i // width) + (i % width)

                for offset, pixel_bytes in enumerate(zip(*([iter(texture.data)] * 6), strict=True)):
                    pixel1 = (pixel_bytes[0] << 0) | (pixel_bytes[1] << 8) | (pixel_bytes[2] << 16)
                    pixel2 = (pixel_bytes[3] << 0) | (pixel_bytes[4] << 8) | (pixel_bytes[5] << 16)
                    data = pixel1 | (pixel2 << 24)
                    yield from wait_until(dut.texture_writes.en)
                    act_buffer = (yield dut.texture_writes.buffer)
                    act_addr = (yield dut.texture_writes.addr)
                    act_data = (yield dut.texture_writes.data)
                    addr = idx_to_addr(offset)
                    assert act_buffer == texture.buffer, f"{act_buffer} / {texture.buffer}"
                    assert act_addr == addr, f"{hex(act_addr)} / {hex(addr)}"
                    assert act_data == data, f"{hex(addr)}: {hex(act_data)} / {hex(data)}"
                    yield
            yield dut.triangles.ready.eq(1)
            yield from wait_until(dut.triangles.valid)
            yield from check_triangle(dut, 0, triangle)
            yield

        sim = Simulator(dut)
        emulator.add_to_sim(sim)
        sim.add_sync_process(make_testbench_process(control))
        sim.add_sync_process(make_testbench_process(check))
        sim.add_clock(1e-6)
        sim.run()

    def test_clear(self):
        dut = CommandProcessor()

        base_addr = 0x4000_0000
        command_mem = bytes()

        clears = [
            BufferClear(0xFFFFFF, 0x1AABBCC, 0x69420),
            BufferClear(0x010203, 0x1694200, 0x1234),
        ]
        triangle = (
            Vertex(0x1, 0x2, 0x3, 0x4, 0x5, 0x6),
            Vertex(0x7, 0x8, 0x9, 0xA, 0xB, 0xC),
            Vertex(0xD, 0xE, 0xF, 0x0, 0x1, 0x2),
            False,
            0b00,
        )

        for clr in clears:
            command_mem += pack_buffer_clear(clr)
        command_mem += struct.pack("<I", Command.DRAW_TRIANGLE.value | (int(triangle[3]) << 8) | (triangle[4] << 9))
        command_mem += struct.pack("<3Q", *[pack_vertex(v) for v in triangle[:3]])

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
            yield from wait_until(dut.control.idle, 1000)

        def check():
            yield dut.buffer_clears.ready.eq(1)
            for clear in clears:
                yield from wait_until(dut.buffer_clears.valid)
                act_pattern = (yield dut.buffer_clears.payload.pattern)
                act_base = (yield dut.buffer_clears.payload.base_addr)
                act_words = (yield dut.buffer_clears.payload.words)
                assert act_pattern == clear.pattern, f"{act_pattern:06X} / {clear.pattern:06X}"
                assert act_base == clear.addr, f"{act_base:08X} / {clear.addr:08X}"
                assert act_words == clear.words, f"{act_words} / {clear.words}"
                yield
            yield dut.triangles.ready.eq(1)
            yield from wait_until(dut.triangles.valid)
            yield from check_triangle(dut, 0, triangle)
            yield

        sim = Simulator(dut)
        emulator.add_to_sim(sim)
        sim.add_sync_process(make_testbench_process(control))
        sim.add_sync_process(make_testbench_process(check))
        sim.add_clock(1e-6)
        sim.run()
