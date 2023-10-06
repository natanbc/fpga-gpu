from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.wiring import Component, Signature, Out
from amaranth_soc import wishbone
from .zynq_ifaces import MAxiGP


__all__ = ["Axi2Wishbone"]


class AxiReadToWishbone(Component):
    ar: Out(MAxiGP.members["read_address"].signature)
    r: Out(MAxiGP.members["read"].signature)
    wishbone: Out(wishbone.Signature(addr_width=30, data_width=32, granularity=8, features={"err"}))

    def elaborate(self, platform):
        m = Module()

        rid = Signal(12)
        with m.FSM():
            with m.State("AXI_WAIT"):
                m.d.comb += self.ar.ready.eq(1)
                with m.If(self.ar.valid):
                    m.d.sync += [
                        rid.eq(self.ar.id),
                        self.wishbone.adr.eq(self.ar.addr[2:]),
                    ]
                    sel = Signal(4)
                    unaligned = Signal()
                    with m.Switch(self.ar.size):
                        with m.Case(0b00):
                            m.d.comb += sel.eq(1 << self.ar.addr[:2])
                        with m.Case(0b01):
                            m.d.comb += sel.eq(0b11 << self.ar.addr[:2]), unaligned.eq(self.ar.addr[0])
                        with m.Case(0b10):
                            m.d.comb += sel.eq(0b1111), unaligned.eq(self.ar.addr[:2].any())
                    m.d.sync += self.wishbone.sel.eq(sel)

                    with m.If((self.ar.len != 0) | unaligned):
                        m.d.sync += self.r.resp.eq(0b10)
                        m.next = "AXI_RESPONSE"
                    with m.Else():
                        m.next = "WISHBONE"
            with m.State("WISHBONE"):
                m.d.comb += [
                    self.wishbone.cyc.eq(1),
                    self.wishbone.stb.eq(1),
                ]
                with m.If(self.wishbone.err | self.wishbone.ack):
                    m.d.sync += [
                        self.r.data.eq(self.wishbone.dat_r),
                        self.r.resp.eq(Mux(self.wishbone.err, 0b10, 0b00)),
                    ]
                    m.next = "AXI_RESPONSE"
            with m.State("AXI_RESPONSE"):
                m.d.comb += self.r.valid.eq(1)
                with m.If(self.r.ready):
                    m.next = "AXI_WAIT"

        m.d.comb += [
            self.r.last.eq(1),
            self.r.id.eq(rid),
        ]

        return m


class AxiWriteToWishbone(Component):
    aw: Out(MAxiGP.members["write_address"].signature)
    w: Out(MAxiGP.members["write_data"].signature)
    b: Out(MAxiGP.members["write_response"].signature)
    wishbone: Out(wishbone.Signature(addr_width=30, data_width=32, granularity=8, features={"err"}))

    def elaborate(self, platform):
        m = Module()

        m.d.comb += self.wishbone.we.eq(1)

        wid = Signal(12)
        burst_len = Signal(4)
        with m.FSM():
            with m.State("AXI_WAIT_ADDR"):
                m.d.comb += self.aw.ready.eq(1)
                with m.If(self.aw.valid):
                    m.d.sync += [
                        wid.eq(self.aw.id),
                        burst_len.eq(self.aw.len),
                        self.wishbone.adr.eq(self.aw.addr[2:]),
                    ]

                    unaligned = Signal()
                    with m.Switch(self.aw.size):
                        with m.Case(0b01):
                            m.d.comb += unaligned.eq(self.aw.addr[0])
                        with m.Case(0b10):
                            m.d.comb += unaligned.eq(self.aw.addr[:2].any())

                    with m.If((self.aw.len != 0) | unaligned):
                        m.d.sync += self.b.resp.eq(0b10)
                        m.next = "DUMP_FAILURE"
                    with m.Else():
                        m.next = "AXI_WAIT_DATA"
            with m.State("DUMP_FAILURE"):
                m.d.comb += self.w.ready.eq(1)
                with m.If(self.w.valid):
                    m.d.sync += burst_len.eq(burst_len - 1)
                    with m.If(burst_len == 0):
                        m.next = "AXI_RESPONSE"
            with m.State("AXI_WAIT_DATA"):
                m.d.comb += self.w.ready.eq(1)
                with m.If(self.w.valid):
                    m.d.sync += [
                        self.wishbone.dat_w.eq(self.w.data),
                        self.wishbone.sel.eq(self.w.strb),
                    ]
                    m.next = "WISHBONE"
            with m.State("WISHBONE"):
                m.d.comb += [
                    self.wishbone.cyc.eq(1),
                    self.wishbone.stb.eq(1),
                ]
                with m.If(self.wishbone.err | self.wishbone.ack):
                    m.d.sync += [
                        self.b.resp.eq(Mux(self.wishbone.err, 0b10, 0b00)),
                    ]
                    m.next = "AXI_RESPONSE"
            with m.State("AXI_RESPONSE"):
                m.d.comb += self.b.valid.eq(1)
                with m.If(self.b.ready):
                    m.next = "AXI_WAIT_ADDR"

        m.d.comb += [
            self.b.id.eq(wid),
        ]

        return m


class Axi2Wishbone(Component):
    axi: Out(MAxiGP)
    wishbone: Out(wishbone.Signature(addr_width=30, data_width=32, granularity=8, features={"err"}))

    def elaborate(self, platform):
        m = Module()

        m.d.comb += self.axi.aclk.eq(ClockSignal())
        m.submodules.arbiter = arbiter = wishbone.Arbiter(
            addr_width=30,
            data_width=32,
            granularity=8,
            features={"err"},
        )

        m.submodules.read2wb = read2wb = AxiReadToWishbone()
        m.submodules.write2wb = write2wb = AxiWriteToWishbone()

        arbiter.add(read2wb.wishbone)
        arbiter.add(write2wb.wishbone)

        wiring.connect(m, read2wb.ar, wiring.flipped(self.axi.read_address))
        wiring.connect(m, read2wb.r, wiring.flipped(self.axi.read))

        wiring.connect(m, write2wb.aw, wiring.flipped(self.axi.write_address))
        wiring.connect(m, write2wb.w, wiring.flipped(self.axi.write_data))
        wiring.connect(m, write2wb.b, wiring.flipped(self.axi.write_response))

        wiring.connect(m, arbiter.bus, wiring.flipped(self.wishbone))

        return m
