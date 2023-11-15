from amaranth import *
from amaranth.lib import wiring
from ..hdmi import HDMIFramebuffer, VideoMode
from ..zynq_ifaces import SAxiHP
from .peripheral import Peripheral


__all__ = ["Framebuffer"]


class Framebuffer(Peripheral):
    def __init__(self, mode: VideoMode, *, name=None, src_loc_at=1):
        super().__init__(name=name, src_loc_at=src_loc_at)

        self._mode = mode

        self.axi = SAxiHP.create()
        self.data_enable = Signal(1)
        self.hsync = Signal(1)
        self.vsync = Signal(1)
        self.r = Signal(8)
        self.g = Signal(8)
        self.b = Signal(8)

        self._width = self.csr(11, "r")
        self._height = self.csr(11, "r")
        self._addr = self.csr(20, "rw")
        self._words = self.csr(20, "rw")
        self._en = self.csr(1, "rw")

        self._fetch_start = self.irq()
        self._fetch_end = self.irq()
        self._underrun = self.irq()

        self._bridge = self.bridge()
        self.bus = self._bridge.bus
        self.irq = self._bridge.irq

    def elaborate(self, platform):
        m = Module()

        m.submodules.bridge = self._bridge
        m.submodules.framebuffer = framebuffer = HDMIFramebuffer(self._mode)
        wiring.connect(m, framebuffer.axi, wiring.flipped(self.axi))

        m.d.comb += [
            self._width.r_data.eq(framebuffer.width),
            self._height.r_data.eq(framebuffer.height),

            self.data_enable.eq(framebuffer.data_enable),
            self.hsync.eq(framebuffer.hsync),
            self.vsync.eq(framebuffer.vsync),
            self.r.eq(framebuffer.r),
            self.g.eq(framebuffer.g),
            self.b.eq(framebuffer.b),
        ]
        for reg, field in zip(
            [self._addr, self._words, self._en],
            [framebuffer.page_addr, framebuffer.words, framebuffer.en],
        ):
            m.d.comb += reg.r_data.eq(field)
            with m.If(reg.w_stb):
                m.d.sync += field.eq(reg.w_data)

        for a, b in zip(
            [self._fetch_start, self._fetch_end, self._underrun],
            [framebuffer.fetch_start_irq, framebuffer.fetch_end_irq, framebuffer.underrun_irq],
        ):
            m.d.comb += a.eq(b)

        return m
