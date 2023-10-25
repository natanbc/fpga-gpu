from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.enum import Enum
from amaranth.lib.wiring import Component, In, Out
from ..dma import ControlRegisters, DMA
from ..zynq_ifaces import SAxiGP
from .types import TriangleStream, BufferClearStream


__all__ = ["Command", "CommandProcessor"]


class Command(Enum):
    # TODO: textures? flags (no z buffering, etc)?
    TRIANGLE = 0x01


class CommandProcessor(Component):
    idle: Out(1)

    axi: Out(SAxiGP)
    control: In(ControlRegisters)

    triangles: Out(TriangleStream)
    buffer_clears: Out(BufferClearStream)

    def elaborate(self, platform):
        m = Module()

        m.submodules.dma = dma = DMA(SAxiGP)
        wiring.connect(m, dma.axi, wiring.flipped(self.axi))
        wiring.connect(m, dma.control, wiring.flipped(self.control))

        vertex_ctr = Signal(range(3))
        vertex_half = Signal()

        vertex = Array([getattr(self.triangles.payload, x) for x in ["v0", "v1", "v2"]])[vertex_ctr]
        ignore = Signal(2)
        with m.If(dma.data_stream.valid & dma.data_stream.ready):
            m.d.sync += [
                Cat(vertex, ignore).word_select(vertex_half, 32).eq(dma.data_stream.data),
                vertex_half.eq(~vertex_half),
                vertex_ctr.eq(Mux(vertex_half, Mux(vertex_ctr == 2, 0, vertex_ctr + 1), vertex_ctr)),
            ]

        with m.FSM():
            with m.State("READ_CMD"):
                m.d.comb += [
                    self.idle.eq(~dma.data_stream.valid & dma.control.idle),
                    dma.data_stream.ready.eq(1),
                ]
                with m.If(dma.data_stream.valid):
                    with m.Switch(dma.data_stream.data[:8]):
                        with m.Case(Command.TRIANGLE):
                            m.d.sync += vertex_ctr.eq(0), vertex_half.eq(0)
                            m.next = "READ_VERTEXES"
            with m.State("READ_VERTEXES"):
                m.d.comb += dma.data_stream.ready.eq(1)
                with m.If((vertex_ctr == 2) & vertex_half):
                    m.next = "SUBMIT_TRIANGLE"
            with m.State("SUBMIT_TRIANGLE"):
                m.d.comb += self.triangles.valid.eq(1)
                with m.If(self.triangles.ready):
                    m.next = "READ_CMD"

        return m
