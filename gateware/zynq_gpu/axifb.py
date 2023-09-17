from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out, Signature
from .dma import DMA, ControlRegisters, DataStream
from .zynq_ifaces import SAxiHP


__all__ = ["ControlRegisters", "PixelStream", "AxiFramebuffer", "PixelAdapter"]


PixelStream = Signature({
    "valid": Out(1),
    "ready": In(1),
    # TODO: lib.data
    "pixel": Out(24),
})


# 64 bit -> 24 bit
class PixelAdapter(Elaboratable):
    signature = Signature({
        "memory_stream": In(DataStream),
        "pixel_stream": Out(PixelStream),
    })

    def __init__(self):
        self.memory_stream = DataStream.flip().create()
        self.pixel_stream = PixelStream.create()

    def elaborate(self, platform):
        m = Module()

        ctr = Signal(range(4))
        remain = Signal(16)

        m.d.comb += [
            self.pixel_stream.valid.eq(self.memory_stream.valid),
        ]
        with m.FSM():
            # memory word = AAABBBCC, remain = XXXXXXXX
            with m.State("A"):
                m.d.comb += self.pixel_stream.pixel.eq(self.memory_stream.data.word_select(ctr, 24))
                with m.If(self.memory_stream.valid & self.pixel_stream.ready):
                    m.d.sync += ctr.eq(ctr + 1)
                    with m.If(ctr == 1):
                        m.next = "B"
                        m.d.comb += self.memory_stream.ready.eq(1)
                        m.d.sync += ctr.eq(0), remain.eq(self.memory_stream.data[48:])
            # memory word = CDDDEEEF, remain = XXXXXXCC
            with m.State("B"):
                with m.Switch(ctr):
                    with m.Case(0):
                        m.d.comb += self.pixel_stream.pixel.eq(Cat(remain, self.memory_stream.data[:8]))
                    with m.Case(1, 2):
                        m.d.comb += self.pixel_stream.pixel.eq(
                            self.memory_stream.data[8:].word_select((ctr-1).as_unsigned(), 24)
                        )
                with m.If(self.memory_stream.valid & self.pixel_stream.ready):
                    m.d.sync += ctr.eq(ctr + 1)
                    with m.If(ctr == 2):
                        m.next = "C"
                        m.d.comb += self.memory_stream.ready.eq(1)
                        m.d.sync += ctr.eq(0), remain.eq(self.memory_stream.data[56:])
            # memory word = FFGGGHHH, remain = XXXXXXXF
            with m.State("C"):
                with m.Switch(ctr):
                    with m.Case(0):
                        m.d.comb += self.pixel_stream.pixel.eq(Cat(remain[:8], self.memory_stream.data[:16]))
                    with m.Case(1, 2):
                        m.d.comb += self.pixel_stream.pixel.eq(
                            self.memory_stream.data[16:].word_select((ctr-1).as_unsigned(), 24)
                        )
                with m.If(self.memory_stream.valid & self.pixel_stream.ready):
                    m.d.sync += ctr.eq(ctr + 1)
                    with m.If(ctr == 2):
                        m.next = "A"
                        m.d.comb += self.memory_stream.ready.eq(1)
                        m.d.sync += ctr.eq(0), remain.eq(0)

        return m


class AxiFramebuffer(Elaboratable):
    signature = Signature({
        "axi": Out(SAxiHP),
        "control": In(ControlRegisters),
        "pixel_stream": Out(PixelStream),
    })

    def __init__(self):
        self.axi = SAxiHP.create()
        self.control = ControlRegisters.create()
        self.pixel_stream = PixelStream.create()

    def elaborate(self, platform):
        m = Module()

        m.submodules.pixel_adapter = pixel_adapter = PixelAdapter()
        m.submodules.dma = dma = DMA()

        wiring.connect(m, dma.axi, wiring.flipped(self.axi))
        wiring.connect(m, self.control, wiring.flipped(dma.control))
        wiring.connect(m, dma.data_stream, pixel_adapter.memory_stream)
        wiring.connect(m, pixel_adapter.pixel_stream, wiring.flipped(self.pixel_stream))

        return m
