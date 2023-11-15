from dataclasses import dataclass
from typing import ClassVar
from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.wiring import Component, In, Out
from ..axifb import AxiFramebuffer
from ..zynq_ifaces import SAxiHP


__all__ = ["HDMIFramebuffer", "Timings", "VideoMode"]


@dataclass
class Timings:
    active: int
    front_porch: int
    sync: int
    back_porch: int

    @property
    def total(self):
        return self.active + self.front_porch + self.sync + self.back_porch

    @property
    def sync_start(self):
        return self.active + self.front_porch

    @property
    def sync_end(self):
        return self.sync_start + self.sync


@dataclass
class VideoMode:
    M1080_30: ClassVar["VideoMode"]
    M720_60: ClassVar["VideoMode"]
    M480_60: ClassVar["VideoMode"]

    h_timings: Timings
    v_timings: Timings
    pixel_clock: int

    @property
    def width(self):
        return self.h_timings.active

    @property
    def height(self):
        return self.v_timings.active


# 1080x1920 @ 30 / 79.75MHz pix clk, 398.75MHz serdes clk
VideoMode.M1080_30 = VideoMode(
    Timings(1920, 56, 192, 248),
    Timings(1080, 3, 5, 14),
    79_750_000,
)
# 720x1280 @ 60  / 74.50MHz pix clk, 372.50MHz serdes clk
VideoMode.M720_60 = VideoMode(
    Timings(1280, 64, 128, 192),
    Timings(720, 3, 5, 20),
    74_500_000,
)
# 480x640 @ 60   / 23.75MHz pix clk, 118.75MHz serdes clk
VideoMode.M480_60 = VideoMode(
    Timings(640, 16, 64, 80),
    Timings(480, 3, 4, 13),
    23_750_000,
)


class TimingGen(Elaboratable):
    def __init__(self, mode: VideoMode):
        self._mode = mode

        self.x = Signal(range(mode.h_timings.total), reset=mode.width + 1)
        self.y = Signal(range(mode.v_timings.total), reset=mode.height)
        self.active = Signal()
        self.hsync = Signal()
        self.vsync = Signal()

        self.ended_frame = Signal()

    def elaborate(self, platform):
        m = Module()

        with m.If(self.x < self._mode.h_timings.total - 1):
            m.d.sync += self.x.eq(self.x + 1)
        with m.Else():
            m.d.sync += self.x.eq(0)
            with m.If(self.y < self._mode.v_timings.total - 1):
                m.d.sync += self.y.eq(self.y + 1)
            with m.Else():
                m.d.sync += self.y.eq(0)

        m.d.comb += [
            self.ended_frame.eq((self.x == self._mode.width) & (self.y == self._mode.height)),
            self.active.eq((self.x < self._mode.width) & (self.y < self._mode.height)),
            self.hsync.eq((self.x >= self._mode.h_timings.sync_start) & (self.x < self._mode.h_timings.sync_end)),
            self.vsync.eq((self.y >= self._mode.v_timings.sync_start) & (self.y < self._mode.v_timings.sync_end)),
        ]

        return m


class HDMIFramebuffer(Component):
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

    def __init__(self, mode: VideoMode):
        self._mode = mode
        super().__init__()

    @property
    def width(self):
        return self._mode.width

    @property
    def height(self):
        return self._mode.height

    def elaborate(self, platform):
        m = Module()

        reset = Signal()
        m.submodules.tgen = tgen = ResetInserter(reset)(TimingGen(self._mode))
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
