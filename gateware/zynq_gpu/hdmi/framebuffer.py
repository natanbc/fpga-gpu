from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.wiring import Component, In, Out
from ..axifb import AxiFramebuffer
from ..zynq_ifaces import SAxiHP
from .tx import HDMITx


__all__ = ["HDMIFramebuffer"]


# 1080x1920 @ 30 / 79.75MHz pix clk, 398.75MHz serdes clk
width, height, hscan, vscan = 1920, 1080, 2416, 1102
hsync_start, hsync_end = 1920 + 56, 1920 + 56 + 192
vsync_start, vsync_end = 1080 + 3, 1080 + 3 + 5


class TimingGen(Elaboratable):
    def __init__(self):
        self.x = Signal(range(hscan), reset=width + 1)
        self.y = Signal(range(vscan), reset=height)
        self.active = Signal()
        self.hsync = Signal()
        self.vsync = Signal()

        self.ended_frame = Signal()

    def elaborate(self, platform):
        m = Module()

        with m.If(self.x < hscan - 1):
            m.d.sync += self.x.eq(self.x + 1)
        with m.Else():
            m.d.sync += self.x.eq(0)
            with m.If(self.y < vscan - 1):
                m.d.sync += self.y.eq(self.y + 1)
            with m.Else():
                m.d.sync += self.y.eq(0)

        m.d.comb += [
            self.ended_frame.eq((self.x == width) & (self.y == height)),
            self.active.eq((self.x < width) & (self.y < height)),
            self.hsync.eq((self.x >= hsync_start) & (self.x < hsync_end)),
            self.vsync.eq((self.y >= vsync_start) & (self.y < vsync_end)),
        ]

        return m


class HDMIFramebuffer(Component):
    width = width
    height = height

    axi: Out(SAxiHP)

    data_enable: Out(1)
    hsync: Out(1)
    vsync: Out(1)
    r: Out(8)
    g: Out(8)
    b: Out(8)

    page_addr: In(20)
    words: In(20)
    en: In(1)

    fetch_start_irq: Out(1)
    fetch_end_irq: Out(1)
    underrun_irq: Out(1)

    def elaborate(self, platform):
        m = Module()

        reset = Signal()
        m.submodules.tgen = tgen = ResetInserter(reset)(TimingGen())
        m.submodules.fb = fb = ResetInserter(reset)(AxiFramebuffer())

        en = Signal()
        m.d.pix += en.eq(self.en)
        # Reset just before enabling
        m.d.comb += reset.eq(~en & self.en)

        started = Signal()
        with m.If(~en):
            m.d.pix += started.eq(0)
        with m.Elif(fb.control.trigger):
            m.d.pix += started.eq(1)

        idle_prev = Signal()
        m.d.pix += idle_prev.eq(fb.control.idle)
        vsync_prev = Signal()
        m.d.pix += vsync_prev.eq(tgen.vsync)
        m.d.comb += [
            fb.control.base_addr.eq(Cat(C(0, 6), self.page_addr)),
            fb.control.words.eq(self.words),
            fb.control.trigger.eq(en & vsync_prev & ~tgen.vsync),
            fb.control.qos.eq(0b1111),

            self.fetch_start_irq.eq(fb.control.trigger),
            self.fetch_end_irq.eq(~idle_prev & fb.control.idle),
        ]

        wiring.connect(m, fb.axi, wiring.flipped(self.axi))

        m.d.comb += [
            fb.pixel_stream.ready.eq(~en | tgen.active),
            self.underrun_irq.eq(en & started & tgen.active & ~fb.pixel_stream.valid),

            self.data_enable.eq(tgen.active),
            self.hsync.eq(tgen.hsync),
            self.vsync.eq(tgen.vsync),

            Cat(self.b, self.g, self.r).eq(Mux(fb.pixel_stream.valid, fb.pixel_stream.pixel, 0x800080)),
        ]

        return m
