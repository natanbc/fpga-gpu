from amaranth.sim import *
from zynq_gpu.dma import DMAFifo, DMAControl, DMA
from zynq_gpu.zynq_ifaces import SAxiHP, SAxiGP, SAxiACP
import random
import struct
import unittest
from .utils import wait_until, AxiEmulator


def _get_width_bytes(sig):
    if sig in [SAxiHP, SAxiACP]:
        return 8
    if sig == SAxiGP:
        return 4
    assert False, f"Unsupported interface type {sig}"


class DMAFifoTests(unittest.TestCase):
    @staticmethod
    def _do_test(axi_iface_sig, slow_axi: bool, slow_read: bool):
        dut = DMAFifo(axi_iface_sig=axi_iface_sig, depth=512)

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

        word_size = _get_width_bytes(axi_iface_sig)
        unpack_type = "Q" if word_size == 8 else "I"

        def axi_feed():
            yield dut.axi_read.valid.eq(1)
            last = (len(data) // word_size) - 1
            for i, word in enumerate(struct.unpack("<{}{}".format(len(data) // word_size, unpack_type), data)):
                axi_last = i % 16 == 15 or i == last
                yield dut.axi_read.data.eq(word)
                yield dut.axi_read.last.eq(axi_last)
                yield from wait_until(dut.axi_read.ready)
                assert (yield dut.burst_end) == axi_last
                if slow_axi:
                    yield dut.axi_read.valid.eq(0)
                    for _ in range(10):
                        yield
                    yield dut.axi_read.valid.eq(1)
            yield dut.axi_read.valid.eq(0)

        def pixel_read():
            yield dut.data_stream.ready.eq(1)
            for word in struct.unpack("<{}{}".format(len(data) // word_size, unpack_type), data):
                yield from wait_until(dut.data_stream.valid)
                actual = yield dut.data_stream.data
                assert actual == word, f"Mismatched word, expected {hex(word)}, got {hex(actual)}"
                if slow_read:
                    yield dut.data_stream.ready.eq(0)
                    for _ in range(10):
                        yield
                    yield dut.data_stream.ready.eq(1)

        sim = Simulator(dut)
        sim.add_sync_process(axi_feed)
        sim.add_sync_process(pixel_read)
        sim.add_clock(1e-6)
        sim.run()

    def test_basic_hp(self):
        self._do_test(SAxiHP, False, False)

    def test_slow_reads_hp(self):
        self._do_test(SAxiHP, False, True)

    def test_slow_axi_hp(self):
        self._do_test(SAxiHP, True, False)

    def test_slow_axi_slow_reads_hp(self):
        self._do_test(SAxiHP, True, True)

    def test_basic_gp(self):
        self._do_test(SAxiGP, False, False)

    def test_slow_reads_gp(self):
        self._do_test(SAxiGP, False, True)

    def test_slow_axi_gp(self):
        self._do_test(SAxiGP, True, False)

    def test_slow_axi_slow_reads_gp(self):
        self._do_test(SAxiGP, True, True)


class DMAControlTests(unittest.TestCase):
    @staticmethod
    def _do_test(axi_iface_sig, count: int, slow_axi: bool = False):
        dut = DMAControl(axi_iface_sig=axi_iface_sig, max_pending_bursts=64)

        base_addr = 0x4000_0000
        word_size = _get_width_bytes(axi_iface_sig)

        def control():
            yield dut.control.base_addr.eq(base_addr >> 6)
            yield dut.control.words.eq(count)
            yield dut.control.trigger.eq(1)
            yield
            yield dut.control.trigger.eq(0)

        pending_reads = 0

        def axi_read():
            nonlocal pending_reads

            yield dut.axi_address.ready.eq(1)
            done = 0
            while done < count:
                yield from wait_until(dut.axi_address.valid)
                expected_addr = base_addr + done * word_size
                actual_addr = (yield dut.axi_address.addr)
                assert expected_addr == actual_addr, \
                    f"Wrong address, expected {hex(expected_addr)}, got {hex(actual_addr)}"
                expected_burst_len = 16 if (count - done) >= 16 else (count - done)
                actual_burst_len = (yield dut.axi_address.len) + 1
                assert expected_burst_len == actual_burst_len, \
                    f"Wrong burst length, expected {expected_burst_len}, got {actual_burst_len}"
                done += actual_burst_len
                pending_reads += 1
                if slow_axi:
                    yield dut.axi_address.ready.eq(0)
                    for _ in range(10):
                        yield
                    yield dut.axi_address.ready.eq(1)
            assert done == count, f"Wrong total read words, expected {count}, got {done}"

        def burst_ends():
            nonlocal pending_reads

            yield Passive()
            while True:
                if pending_reads > 0:
                    for _ in range(random.randint(0, 50)):
                        yield
                    yield dut.burst_end.eq(1)
                    yield
                    yield dut.burst_end.eq(0)
                yield

        sim = Simulator(dut)
        sim.add_sync_process(control)
        sim.add_sync_process(axi_read)
        sim.add_sync_process(burst_ends)
        sim.add_clock(1e-6)
        sim.run()

    def test_burst_len_multiple_hp(self):
        self._do_test(SAxiHP, 128)

    def test_burst_len_not_multiple_hp(self):
        self._do_test(SAxiHP, 69)

    def test_slow_axi_hp(self):
        self._do_test(SAxiHP, 1337, True)

    def test_burst_len_multiple_gp(self):
        self._do_test(SAxiGP, 128)

    def test_burst_len_not_multiple_gp(self):
        self._do_test(SAxiGP, 69)

    def test_slow_axi_gp(self):
        self._do_test(SAxiGP, 1337, True)


class DMATest(unittest.TestCase):
    @staticmethod
    def _do_test(axi_iface_sig, slow_read: bool):
        dut = DMA(axi_iface_sig=axi_iface_sig)

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
        word_size = _get_width_bytes(axi_iface_sig)
        unpack_type = "Q" if word_size == 8 else "I"

        def read(addr, bytes_per_beat):
            offset = addr - base_addr
            return struct.unpack(f"<{unpack_type}", data[offset:offset+word_size])[0]

        emulator = AxiEmulator(dut.axi, read, None)

        def control():
            yield dut.control.base_addr.eq(base_addr >> 6)
            yield dut.control.words.eq(len(data) // word_size)
            yield dut.control.trigger.eq(1)
            yield
            yield dut.control.trigger.eq(0)

        def pixel_read():
            yield dut.data_stream.ready.eq(1)
            for word in struct.unpack("<{}{}".format(len(data) // word_size, unpack_type), data):
                yield from wait_until(dut.data_stream.valid)
                actual = yield dut.data_stream.data
                assert actual == word, f"Mismatched word, expected {hex(word)}, got {hex(actual)}"
                if slow_read:
                    yield dut.data_stream.ready.eq(0)
                    for _ in range(10):
                        yield
                    yield dut.data_stream.ready.eq(1)

        sim = Simulator(dut)
        emulator.add_to_sim(sim)
        sim.add_sync_process(control)
        sim.add_sync_process(pixel_read)
        sim.add_clock(1e-6)
        sim.run()

    def test_normal_hp(self):
        return self._do_test(SAxiHP, False)

    def test_slow_read_hp(self):
        return self._do_test(SAxiHP, True)

    def test_normal_gp(self):
        return self._do_test(SAxiGP, False)

    def test_slow_read_gp(self):
        return self._do_test(SAxiGP, True)

    # Should be identical to SAxiHP for the purposes of the DMA engine
    def test_normal_acp(self):
        return self._do_test(SAxiACP, False)

    def test_slow_read_acp(self):
        return self._do_test(SAxiACP, True)
