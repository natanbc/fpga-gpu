from amaranth import *
from amaranth.lib.wiring import Component, In, Out
from ..zynq_ifaces import SAxiHP


__all__ = ["PixelWriter"]


class PixelWriter(Component):
    pixel_valid: In(1)
    pixel_ready: Out(1)
    pixel_addr: In(32)
    pixel_data: In(24)

    axi_addr: Out(SAxiHP.members["write_address"].signature)
    axi_data: Out(SAxiHP.members["write_data"].signature)
    axi_resp: Out(SAxiHP.members["write_response"].signature)

    def elaborate(self, platform):
        m = Module()

        split_write = Signal()
        # `pixel_addr[:3] in [7, 8]` => multiple writes
        m.d.comb += split_write.eq(self.pixel_addr[1:3].all())

        split_page = Signal()
        # `pixel_addr[:12] in [0xFFE, 0xFFF]` => write crosses 4KiB boundary
        m.d.comb += split_page.eq(self.pixel_addr[1:12].all())

        cyc1_write_data = Signal(64)
        cyc1_write_mask = Signal(8)

        cyc2_write_data = Signal(16)
        cyc2_write_mask = Signal(2)

        axi_send_addr = Signal()
        axi_send_data = Signal()
        axi_data_done = Signal()

        m.d.comb += [
            self.axi_addr.burst.eq(0b01),  # INCR
            self.axi_addr.size.eq(0b11),   # 8 bytes/beat
            self.axi_resp.ready.eq(1),     # Don't care about responses
        ]

        with m.If(axi_send_addr):
            m.d.comb += self.axi_addr.valid.eq(1)
            with m.If(self.axi_addr.ready):
                m.d.sync += axi_send_addr.eq(0)

        with m.If(axi_send_data):
            m.d.comb += [
                self.axi_data.valid.eq(1),
                axi_data_done.eq(self.axi_data.ready),
            ]

        with m.FSM() as fsm:
            with m.State("IDLE"):
                m.d.comb += self.pixel_ready.eq(1)
                with m.If(self.pixel_valid):
                    m.d.sync += [
                        cyc1_write_data.eq(self.pixel_data << (8 * self.pixel_addr[:3])),
                        cyc1_write_mask.eq(0b111 << self.pixel_addr[:3])
                    ]

                    m.d.sync += [
                        axi_send_addr.eq(1),
                        self.axi_addr.addr.eq(Cat(C(0, 3), self.pixel_addr[3:])),
                        self.axi_addr.len.eq(split_write & ~split_page),
                    ]

                    with m.If(split_write):
                        m.d.sync += [
                            cyc2_write_data.eq(Mux(
                                self.pixel_addr[0],
                                self.pixel_data[8:],
                                self.pixel_data[16:],
                            )),
                            cyc2_write_mask.eq(Mux(
                                self.pixel_addr[0],
                                0b11,
                                0b01,
                            ))
                        ]
                        with m.If(split_page):
                            m.next = "WRITE_SPLIT_PAGE_1"
                        with m.Else():
                            m.next = "WRITE_MULTI_1"
                    with m.Else():
                        m.next = "WRITE_SINGLE"
            with m.State("WRITE_SINGLE"):
                with m.If(axi_data_done):
                    m.next = "IDLE"
            with m.State("WRITE_MULTI_1"):
                with m.If(axi_data_done):
                    m.next = "WRITE_MULTI_2"
            with m.State("WRITE_MULTI_2"):
                with m.If(axi_data_done):
                    m.next = "IDLE"
            with m.State("WRITE_SPLIT_PAGE_1"):
                with m.If(axi_data_done):
                    m.d.sync += [
                        self.axi_addr.addr.eq(self.axi_addr.addr + 8),
                        axi_send_addr.eq(1),
                    ]
                    m.next = "WRITE_SPLIT_PAGE_2"
            with m.State("WRITE_SPLIT_PAGE_2"):
                with m.If(axi_data_done):
                    m.next = "IDLE"

        m.d.comb += self.axi_data.last.eq(
            fsm.ongoing("WRITE_SINGLE") | fsm.ongoing("WRITE_MULTI_2") |
            fsm.ongoing("WRITE_SPLIT_PAGE_1") | fsm.ongoing("WRITE_SPLIT_PAGE_2")
        )

        with m.If(fsm.ongoing("WRITE_SINGLE") | fsm.ongoing("WRITE_MULTI_1") | fsm.ongoing("WRITE_SPLIT_PAGE_1")):
            m.d.comb += [
                axi_send_data.eq(1),
                self.axi_data.data.eq(cyc1_write_data),
                self.axi_data.strb.eq(cyc1_write_mask),
            ]
        with m.If(fsm.ongoing("WRITE_MULTI_2") | fsm.ongoing("WRITE_SPLIT_PAGE_2")):
            m.d.comb += [
                axi_send_data.eq(1),
                self.axi_data.data.eq(cyc2_write_data),
                self.axi_data.strb.eq(cyc2_write_mask),
            ]

        return m
