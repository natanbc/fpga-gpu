from amaranth.sim import *
from zynq_gpu.wb_cdc import WishboneCDC
import unittest
from .utils import wait_until


class WishboneCDCTests(unittest.TestCase):
    @staticmethod
    def _do_test(target_clock: float, target_halt: bool = False):
        dut = WishboneCDC(addr_width=32, features={"err"})

        def initiator():
            for i in range(20):
                yield dut.i_bus.adr.eq(i)
                yield dut.i_bus.stb.eq(1)
                yield dut.i_bus.cyc.eq(1)
                yield
                yield from wait_until(dut.i_bus.ack | dut.i_bus.err)
                if (yield dut.i_halt):
                    assert (yield dut.i_bus.ack) == 0
                    assert (yield dut.i_bus.err) == 1
                elif i & 1:
                    assert (yield dut.i_bus.ack) == 0
                    assert (yield dut.i_bus.err) == 1
                    assert (yield dut.i_bus.dat_r) == 0
                else:
                    assert (yield dut.i_bus.ack) == 1
                    assert (yield dut.i_bus.err) == 0
                    assert (yield dut.i_bus.dat_r) == i
                yield dut.i_bus.stb.eq(0)
                yield dut.i_bus.cyc.eq(0)
                if target_halt:
                    if i == 5:
                        yield dut.i_halt.eq(1)
                    if i == 15:
                        yield dut.i_halt.eq(0)
                yield

        def target():
            yield Passive()
            while True:
                # Do nothing while halted
                if (yield dut.i_halt):
                    yield
                    continue
                yield dut.t_bus.dat_r.eq(0)
                yield dut.t_bus.ack.eq(0)
                yield dut.t_bus.err.eq(0)

                if (yield dut.t_bus.cyc) & (yield dut.t_bus.stb):
                    if (yield dut.t_bus.adr) & 1:
                        yield dut.t_bus.err.eq(1)
                    else:
                        yield dut.t_bus.ack.eq(1)
                        yield dut.t_bus.dat_r.eq((yield dut.t_bus.adr))
                yield

        sim = Simulator(dut)
        sim.add_sync_process(initiator, domain="initiator")
        sim.add_sync_process(target, domain="target")
        sim.add_clock(1e-6, domain="initiator")
        sim.add_clock(target_clock, domain="target")
        sim.run()

    def test_same_freq(self):
        self._do_test(1e-6)

    def test_slower(self):
        self._do_test(1e-5)

    def test_faster(self):
        self._do_test(1e-7)

    def test_same_freq_halt(self):
        self._do_test(1e-6, True)

    def test_slower_halt(self):
        self._do_test(1e-5, True)

    def test_faster_halt(self):
        self._do_test(1e-7, True)
