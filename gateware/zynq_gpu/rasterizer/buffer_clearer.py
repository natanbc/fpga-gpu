from amaranth import *
from amaranth.lib.wiring import Component, In, Out
from .types import BufferClearStream
from ..zynq_ifaces import SAxiHP


class BufferClearer(Component):
    axi: Out(SAxiHP)
    control: In(BufferClearStream)
    idle: Out(1)

    def elaborate(self, platform):
        m = Module()

        m.d.comb += self.axi.aclk.eq(ClockSignal())
        m.d.comb += self.axi.write_response.ready.eq(1)

        addr_128 = Signal.like(self.control.payload.base_addr)
        addr_ctr = Signal.like(self.control.payload.words)
        data_ctr = Signal.like(self.control.payload.words)
        pattern = Signal.like(self.control.payload.pattern)
        pattern_ctr = Signal(range(3))
        burst_ctr = Signal(range(16))

        pending_bursts = Signal(range(64))
        sent_burst = Signal()

        with m.If(sent_burst & ~self.axi.write_response.valid):
            m.d.sync += pending_bursts.eq(pending_bursts + 1)
        with m.Elif(~sent_burst & self.axi.write_response.valid):
            m.d.sync += pending_bursts.eq(pending_bursts - 1)

        with m.If(data_ctr == 0):
            m.d.comb += [
                self.control.ready.eq(1),
                self.idle.eq(1),
            ]
            with m.If(self.control.valid):
                m.d.sync += [
                    addr_128.eq(self.control.payload.base_addr),
                    addr_ctr.eq(self.control.payload.words),
                    data_ctr.eq(self.control.payload.words),
                    pattern.eq(self.control.payload.pattern),
                    self.axi.write_address.qos.eq(self.control.payload.qos),
                    pattern_ctr.eq(0),
                    burst_ctr.eq(0),
                ]
        with m.Else():
            m.d.comb += self.axi.write_address.valid.eq(addr_ctr.any() & ~(pending_bursts.all()))

        burst_len = Signal(4)
        m.d.comb += [
            burst_len.eq(Mux(addr_ctr[4:].any(), 0b1111, addr_ctr - 1)),
            self.axi.write_address.burst.eq(0b01),  # INCR
            self.axi.write_address.size.eq(0b11),   # 8 bytes/beat
            self.axi.write_address.addr.eq(Cat(C(0, 7), addr_128)),
            self.axi.write_address.len.eq(burst_len),

            sent_burst.eq(self.axi.write_address.ready & self.axi.write_address.valid),
        ]
        with m.If(sent_burst):
            m.d.sync += [
                # If burst length is less than 16 words, it's the last burst so counter
                # would get zeroed.
                addr_ctr.eq(Mux(burst_len == 0b1111, addr_ctr - 16, 0)),
                # Doesn't matter if it's left wrong after the last access
                addr_128.eq(Mux(burst_len == 0b1111, addr_128 + 1, addr_128)),
            ]

        # ==========================================

        write_data = Signal(64)

        m.d.comb += [
            self.axi.write_data.valid.eq(data_ctr.any()),
            self.axi.write_data.strb.eq(0b11111111),
            self.axi.write_data.last.eq(burst_ctr.all() | (data_ctr == 1)),
            self.axi.write_data.data.eq(write_data),
        ]

        with m.Switch(pattern_ctr):
            with m.Case(0):
                m.d.comb += write_data.eq(Cat(pattern, pattern, pattern[:16]))
            with m.Case(1):
                m.d.comb += write_data.eq(Cat(pattern[16:], pattern, pattern, pattern[:8]))
            with m.Case(2):
                m.d.comb += write_data.eq(Cat(pattern[8:], pattern, pattern))

        with m.If(self.axi.write_data.ready & self.axi.write_data.valid):
            m.d.sync += [
                data_ctr.eq(data_ctr - 1),
                burst_ctr.eq(burst_ctr + 1),
                pattern_ctr.eq(Mux(pattern_ctr == 2, 0, pattern_ctr + 1)),
            ]

        return m
