from amaranth.sim import *
from zynq_gpu.rasterizer.buffer_clearer import BufferClearer
import unittest
from ..utils import wait_until, AxiEmulator


class BufferClearerTest(unittest.TestCase):
    def test(self):
        dut = BufferClearer()

        base_addr = 0x4000_0000

        data = bytes([
             0xAA, 0xBB, 0xCC
        ] * 48)
        mem = bytearray(len(data))

        def write(addr, _, value, mask):
            off = addr - base_addr
            v = mem[off:off+8]
            for i in range(8):
                if mask & (1 << i):
                    v[i] = (value >> (8 * i)) & 0xFF
            mem[off:off+8] = v

        emulator = AxiEmulator(dut.axi, None, write)

        def control():
            yield dut.control.payload.base_addr.eq(base_addr >> 7)
            yield dut.control.payload.words.eq(len(data) // 8)
            yield dut.control.payload.pattern.eq(0xCCBBAA)
            yield dut.control.valid.eq(1)
            yield
            yield dut.control.valid.eq(0)

            while (yield dut.control.ready):
                yield
            yield from wait_until(dut.control.ready, 1000)

        sim = Simulator(dut)
        emulator.add_to_sim(sim)
        sim.add_sync_process(control)
        sim.add_clock(1e-6)
        sim.run()

        def parts(x):
            return [x[8*i:8*(i+1)].hex() for i in range(3)]

        assert data == mem, ("Mismatched data. Head:\n"
                             f"Expected: {' '.join(*parts(data))}\n"
                             f"Actual:   {' '.join(*parts(mem))}")

