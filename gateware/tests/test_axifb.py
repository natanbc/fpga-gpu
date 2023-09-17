from amaranth.sim import *
from zynq_gpu.axifb import PixelAdapter, AxiFramebuffer
import struct
import unittest
from .utils import wait_until, AxiEmulator


class PixelAdapterTests(unittest.TestCase):
    @staticmethod
    def _do_test(slow_memory: bool, slow_read: bool):
        dut = PixelAdapter()

        data = bytes([
            0x11, 0x11, 0x11,
            0x11, 0x11, 0xFF,
            0x11, 0xFF, 0x11,
            0x11, 0xFF, 0xFF,
            0xFF, 0x11, 0x11,
            0xFF, 0x11, 0xFF,
            0xFF, 0xFF, 0x11,
            0xFF, 0xFF, 0xFF,
        ] * 32)
        assert len(data) % 8 == 0

        def axi_feed():
            yield dut.memory_stream.valid.eq(1)
            for word in struct.unpack("<{}Q".format(len(data) // 8), data):
                yield dut.memory_stream.data.eq(word)
                yield from wait_until(dut.memory_stream.ready)
                if slow_memory:
                    yield dut.memory_stream.valid.eq(0)
                    for _ in range(10):
                        yield
                    yield dut.memory_stream.valid.eq(1)
            yield dut.memory_stream.valid.eq(0)

        def pixel_read():
            yield dut.pixel_stream.ready.eq(1)
            for pixel_bytes in zip(*([iter(data)] * 3), strict=True):
                pixel = (pixel_bytes[0] << 0) | (pixel_bytes[1] << 8) | (pixel_bytes[2] << 16)
                yield from wait_until(dut.pixel_stream.valid)
                actual = yield dut.pixel_stream.pixel
                assert actual == pixel, f"Mismatched pixel, expected {hex(pixel)}, got {hex(actual)}"
                if slow_read:
                    yield dut.pixel_stream.ready.eq(0)
                    for _ in range(10):
                        yield
                    yield dut.pixel_stream.ready.eq(1)

        sim = Simulator(dut)
        sim.add_sync_process(axi_feed)
        sim.add_sync_process(pixel_read)
        sim.add_clock(1e-6)
        sim.run()

    def test_basic(self):
        self._do_test(False, False)

    def test_slow_reads(self):
        self._do_test(False, True)

    def test_slow_memory(self):
        self._do_test(True, False)

    def test_slow_memory_slow_reads(self):
        self._do_test(True, True)


class AxiFramebufferTests(unittest.TestCase):
    @staticmethod
    def _do_test(slow_read: bool):
        dut = AxiFramebuffer()

        base_addr = 0x4000_0000

        data = bytes([
            0x11, 0x11, 0x11,
            0x11, 0x11, 0xFF,
            0x11, 0xFF, 0x11,
            0x11, 0xFF, 0xFF,
            0xFF, 0x11, 0x11,
            0xFF, 0x11, 0xFF,
            0xFF, 0xFF, 0x11,
            0xFF, 0xFF, 0xFF,
        ] * 12)  # Fewer pixels than other tests because these tests are slower
        assert len(data) % 8 == 0

        def read(addr, bytes_per_beat):
            assert bytes_per_beat == 8
            offset = addr - base_addr
            return struct.unpack("<Q", data[offset:offset+8])[0]

        emulator = AxiEmulator(dut.axi, read, None)

        def control():
            yield dut.control.base_addr.eq(base_addr)
            yield dut.control.words.eq(len(data) // 8)
            yield dut.control.trigger.eq(1)
            yield
            yield dut.control.trigger.eq(0)

        def pixel_read():
            yield dut.pixel_stream.ready.eq(1)
            for pixel_bytes in zip(*([iter(data)] * 3), strict=True):
                pixel = (pixel_bytes[0] << 0) | (pixel_bytes[1] << 8) | (pixel_bytes[2] << 16)
                yield from wait_until(dut.pixel_stream.valid)
                actual = yield dut.pixel_stream.pixel
                assert actual == pixel, f"Mismatched pixel, expected {hex(pixel)}, got {hex(actual)}"
                if slow_read:
                    yield dut.pixel_stream.ready.eq(0)
                    for _ in range(10):
                        yield
                    yield dut.pixel_stream.ready.eq(1)

        sim = Simulator(dut)
        emulator.add_to_sim(sim)
        sim.add_sync_process(control)
        sim.add_sync_process(pixel_read)
        sim.add_clock(1e-6)
        sim.run()

    def test_normal(self):
        return self._do_test(False)

    def test_slow_read(self):
        return self._do_test(True)
