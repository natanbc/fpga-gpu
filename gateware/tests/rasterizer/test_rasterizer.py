import functools
import struct
from dataclasses import dataclass
from amaranth.sim import *
from zynq_gpu.rasterizer.rasterizer_sequential import Rasterizer as SequentialRasterizer
from zynq_gpu.rasterizer.rasterizer_pipelined import Rasterizer as PipelinedRasterizer
import unittest
from .utils import points_raster, Vertex
from ..utils import wait_until, AxiEmulator, make_testbench_process


@dataclass
class Triangle:
    v0: Vertex
    v1: Vertex
    v2: Vertex


class RasterizerTest(unittest.TestCase):
    def test_sequential(self):
        self._test(SequentialRasterizer)

    def test_pipelined(self):
        self._test(PipelinedRasterizer)

    @staticmethod
    def _test(mod, *args, **kwargs):
        width = 1920
        height = 1080

        dut = mod(*args, **kwargs)

        mem = bytearray(width * height * 3 + width * height * 2)
        fb_base = 0x1000_0000
        fb_end = 0x1000_0000 + width * height * 3
        z_base = fb_end
        z_end = z_base + width * height * 2

        expected_mem = bytearray(len(mem))
        expected_z_off = width * height * 3

        def read(lo, hi, addr, _):
            assert lo <= addr < hi, f"{hex(lo)} <= {hex(addr)} < {hex(hi)}"
            off = addr - 0x1000_0000
            return struct.unpack("<Q", mem[off:off+8])[0]

        def write(lo, hi, addr, _, value, mask):
            assert lo <= addr < hi, f"{hex(lo)} <= {hex(addr)} < {hex(hi)} => {hex(value)}/{bin(mask)}"
            off = addr - 0x1000_0000
            v = mem[off:off+8]
            for i in range(8):
                if mask & (1 << i):
                    v[i] = (value >> (8 * i)) & 0xFF
            mem[off:off+8] = v

        emulator_framebuffer = AxiEmulator(
            dut.axi,
            None,
            functools.partial(write, fb_base, fb_end),
            ar_buffer=4,
            aw_buffer=4,
            w_buffer=4,
            read_latency=2,
            write_latency=2,
        )
        emulator_z_buffer = AxiEmulator(
            dut.axi2,
            functools.partial(read, z_base, z_end),
            functools.partial(write, z_base, z_end),
            ar_buffer=8,
            aw_buffer=4,
            w_buffer=4,
            read_latency=2,
            write_latency=2,
        )

        def submit_trig(t: Triangle):
            for v in points_raster(t.v0, t.v1, t.v2):
                off = width*v.y + v.x
                z_off = expected_z_off + off*2
                z_actual = struct.unpack("<H", expected_mem[z_off:z_off+2])[0]
                if z_actual < v.z:
                    expected_mem[z_off:z_off+2] = struct.pack("<H", v.z)
                    expected_mem[off*3:(off + 1)*3] = bytes([v.b, v.g, v.r])

            for v in ["v0", "v1", "v2"]:
                d = getattr(dut.triangles.payload, v)
                for n in "xyzrgb":
                    yield getattr(d, n).eq(getattr(getattr(t, v), n))
            yield dut.triangles.valid.eq(1)
            yield from wait_until(dut.triangles.ready, 100_000_000)
            yield
            yield dut.triangles.valid.eq(0)

        def submit_trigs():
            yield dut.width.eq(width)
            yield dut.fb_base.eq(0x1000_0000)
            yield dut.z_base.eq(0x1000_0000 + width*height*3)

            n = 10
            v0 = Vertex(0, 0, 0xFF00 | 3, 0xFF, 0x00, 0x00)
            v1 = Vertex(n, 0, 0xFF00 | 3, 0x00, 0xFF, 0x00)
            v2 = Vertex(0, n, 0xFF00 | 3, 0x00, 0x00, 0xFF)
            v3 = Vertex(n, n, 0xFF00 | 3, 0xFF, 0x00, 0x00)

            # Draw behind the previous region, shouldn't show up
            b1 = Vertex(0, 0, 0xFF00 | 2, 0xFF, 0xFF, 0xFF)
            b2 = Vertex(3, 0, 0xFF00 | 2, 0xFF, 0xFF, 0xFF)
            b3 = Vertex(0, 3, 0xFF00 | 2, 0xFF, 0xFF, 0xFF)

            yield from submit_trig(Triangle(v0, v1, v2))
            yield from submit_trig(Triangle(v1, v3, v2))
            yield from submit_trig(Triangle(b1, b2, b3))
            yield dut.command_idle.eq(1)
            yield from wait_until(dut.idle, 100_000_000)
            # Give it a few more cycles to finish writing, idle goes high too early
            if mod is SequentialRasterizer:
                for _ in range(3):
                    yield

        idles = 0

        def count_idles():
            nonlocal idles

            yield Passive()
            idle_last = 0
            while True:
                yield
                idle = (yield dut.idle)
                if idle_last == 0 and idle != 0:
                    idles += 1
                    assert idles <= 1
                idle_last = idle

        cycles = 0

        def count_cycles():
            nonlocal cycles

            yield Passive()
            while True:
                yield
                cycles += 1
                if cycles > 10_000:
                    raise Exception("Took too long")

        sim = Simulator(dut)
        emulator_framebuffer.add_to_sim(sim)
        emulator_z_buffer.add_to_sim(sim)
        sim.add_sync_process(make_testbench_process(submit_trigs))
        sim.add_sync_process(make_testbench_process(count_cycles))
        sim.add_sync_process(make_testbench_process(count_idles))
        sim.add_clock(1/1e6)
        sim.run()

        if expected_mem != mem:
            for idx in range(len(expected_mem)):
                if expected_mem[idx] != mem[idx]:
                    offset, tp = ((idx - expected_z_off) // 2, "z") if idx >= expected_z_off else (idx // 3, "rgb")
                    y = offset // width
                    x = offset % width
                    print(f"Mismatch @ {tp}[{x},{y}/{hex(idx + 0x1000_0000)}/"
                          f"{hex((idx & ~0b111) + 0x1000_0000)}+0b{(1 << (idx & 0b111)):08b}] "
                          f"=> exp={expected_mem[idx]:02X},act={mem[idx]:02X}")

            size = 30
            with open("actual.ppm", "wb") as f:
                f.write(f"P6\n{size} {size}\n255\n".encode())
                for y in range(size):
                    line = mem[y * width*3:(y + 1) * width*3]
                    for x in range(size):
                        pix = line[x * 3:(x + 1) * 3]
                        tmp = pix[0]
                        pix[0] = pix[2]
                        pix[2] = tmp
                        f.write(pix)
            with open("expected.ppm", "wb") as f:
                f.write(f"P6\n{size} {size}\n255\n".encode())
                for y in range(size):
                    line = expected_mem[y * width*3:(y + 1) * width*3]
                    for x in range(size):
                        pix = line[x * 3:(x + 1) * 3]
                        tmp = pix[0]
                        pix[0] = pix[2]
                        pix[2] = tmp
                        f.write(pix)

            raise AssertionError("Results don't match")

        print(cycles, "cycles")
