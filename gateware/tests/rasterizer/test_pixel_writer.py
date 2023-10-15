from amaranth.sim import *
from dataclasses import dataclass
from zynq_gpu.rasterizer import PixelWriter
from zynq_gpu.zynq_ifaces import SAxiHP
import unittest
from ..utils import wait_until, AxiEmulator


@dataclass
class Write:
    addr: int
    value: int
    mask: int


class PixelWriterTest(unittest.TestCase):
    @staticmethod
    def get_writes_for(target_addr: int, pixel: int):
        iface = SAxiHP.create()
        dut = PixelWriter()
        dut.axi_addr = iface.write_address
        dut.axi_data = iface.write_data
        dut.axi_resp = iface.write_response

        writes = []

        def write(addr, size, value, mask):
            assert size == 8
            writes.append(Write(addr, value, mask))

        emulator = AxiEmulator(iface, None, write)

        def pixel_feed():
            yield dut.pixel_valid.eq(1)
            yield dut.pixel_addr.eq(target_addr)
            yield dut.pixel_data.eq(pixel)
            yield from wait_until(dut.pixel_ready)
            yield dut.pixel_valid.eq(0)
            yield from wait_until(dut.pixel_ready)

        sim = Simulator(dut)
        emulator.add_to_sim(sim)
        sim.add_sync_process(pixel_feed)
        sim.add_clock(1e-6)
        sim.run()

        return writes

    @staticmethod
    def check_writes(target_addr: int, pixel: int, *writes):
        actual = PixelWriterTest.get_writes_for(target_addr, pixel)
        assert len(writes) == len(actual),\
            f"Mismatched amount of writes, expected {len(writes)}, got {len(actual)}: {writes} / {actual}"
        for exp, act in zip(writes, actual):
            assert exp == act, f"Mismatched write: expected {exp}, got {act}"

    def test_single_write(self):
        for base_scale in range(4):
            base = 8 * base_scale
            for i in range(6):
                self.check_writes(base + i, 0xAABBCC, Write(base, 0xAABBCC << (8 * i), 0b111 << i))

    def test_split_writes(self):
        for base_scale in range(4):
            base = 8 * base_scale
            self.check_writes(
                base + 6, 0xAABBCC,
                Write(base, 0xBBCC << 48, 0b1100_0000),
                Write(base + 8, 0xAA, 0b0000_0001),
            )
            self.check_writes(
                base + 7, 0xAABBCC,
                Write(base, 0xCC << 56, 0b1000_0000),
                Write(base + 8, 0xAABB, 0b0000_0011),
            )

    def test_cross_4KiB(self):
        self.check_writes(
            0xFFF, 0xAABBCC,
            Write(0xFF8, 0xCC << 56, 0b1000_0000),
            Write(0x1000, 0xAABB, 0b0000_0011),
        )
