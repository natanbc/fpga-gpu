from amaranth import *
from amaranth.lib import wiring
from amaranth.lib.enum import Enum
from amaranth.lib.wiring import Component, In, Out
from ..dma import ControlRegisters, DMA
from ..zynq_ifaces import SAxiGP
from .types import TriangleStream, BufferClearStream, TextureBufferWrite


__all__ = ["Command", "CommandProcessor"]


class Command(Enum):
    # TODO: flags (no z buffering, etc)?
    DRAW_TRIANGLE = 0x01
    READ_TEXTURE = 0x02


class CommandProcessor(Component):
    idle: Out(1)

    axi: Out(SAxiGP)
    control: In(ControlRegisters)

    triangles: Out(TriangleStream)
    buffer_clears: Out(BufferClearStream)
    texture_writes: Out(TextureBufferWrite)

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

        texture_addr_next = Signal.like(self.texture_writes.addr)
        texture_en = Signal()
        texture_fsm_state = Signal(range(3))
        texture_remain = Signal(16)

        m.d.sync += self.texture_writes.en.eq(0)
        with m.Switch(texture_fsm_state):
            with m.Case(0):
                m.d.sync += self.texture_writes.data[:32].eq(dma.data_stream.data)
                with m.If(dma.data_stream.ready & dma.data_stream.valid):
                    m.d.sync += texture_fsm_state.eq(1)
            with m.Case(1):
                m.d.sync += Cat(self.texture_writes.data[32:], texture_remain).eq(dma.data_stream.data)
                with m.If(dma.data_stream.ready & dma.data_stream.valid):
                    m.d.sync += [
                        self.texture_writes.en.eq(texture_en),
                        texture_fsm_state.eq(2),
                        self.texture_writes.addr.eq(texture_addr_next),
                        texture_addr_next.eq(texture_addr_next + 1),
                    ]
            with m.Case(2):
                m.d.sync += self.texture_writes.data.eq(Cat(texture_remain, dma.data_stream.data))
                with m.If(dma.data_stream.ready & dma.data_stream.valid):
                    m.d.sync += [
                        self.texture_writes.en.eq(texture_en),
                        texture_fsm_state.eq(0),
                        self.texture_writes.addr.eq(texture_addr_next),
                        texture_addr_next.eq(texture_addr_next + 1),
                    ]

        with m.FSM():
            with m.State("READ_CMD"):
                m.d.comb += [
                    self.idle.eq(~dma.data_stream.valid & dma.control.idle),
                    dma.data_stream.ready.eq(1),
                ]
                with m.If(dma.data_stream.valid):
                    with m.Switch(dma.data_stream.data[:8]):
                        with m.Case(Command.DRAW_TRIANGLE):
                            m.d.sync += vertex_ctr.eq(0), vertex_half.eq(0)
                            m.d.sync += self.triangles.payload.texture_enable.eq(dma.data_stream.data[8])
                            m.d.sync += self.triangles.payload.texture_buffer.eq(dma.data_stream.data[9:11])
                            m.next = "READ_VERTEXES"
                        with m.Case(Command.READ_TEXTURE):
                            m.d.sync += [
                                self.texture_writes.buffer.eq(dma.data_stream.data[8:10]),
                                texture_addr_next.eq(0),
                                texture_fsm_state.eq(0),
                            ]
                            m.next = "READ_TEXTURE"
            with m.State("READ_VERTEXES"):
                m.d.comb += dma.data_stream.ready.eq(1)
                with m.If((vertex_ctr == 2) & vertex_half):
                    m.next = "SUBMIT_TRIANGLE"
            with m.State("SUBMIT_TRIANGLE"):
                m.d.comb += self.triangles.valid.eq(1)
                with m.If(self.triangles.ready):
                    m.next = "READ_CMD"
            with m.State("READ_TEXTURE"):
                m.d.comb += texture_en.eq(1), dma.data_stream.ready.eq(1)
                with m.If(dma.data_stream.ready & dma.data_stream.valid):
                    with m.If(texture_addr_next == (128 * 128 // 2) - 1):
                        m.next = "READ_CMD"

        return m