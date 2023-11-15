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
    WAIT_IDLE = 0x03
    CLEAR_BUFFER = 0x04
    WAIT_CLEAR_IDLE = 0x05


class CommandProcessor(Component):
    idle: Out(1)

    axi: Out(SAxiGP)
    control: In(ControlRegisters)

    rasterizer_idle: In(1)
    clearer_idle: In(1)

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

        texture_s = Signal(7)
        # Half of the t coordinate, 2 pixels are written at once
        texture_t_half = Signal(6)

        texture_s_end = Signal(7, reset=0x7F)
        texture_t_start = Signal(6, reset=0)
        texture_t_end = Signal(6, reset=0x3F)

        advance_texture = Signal()
        with m.If(advance_texture):
            m.d.sync += [
                texture_t_half.eq(Mux(texture_t_half == texture_t_end, texture_t_start, texture_t_half + 1)),
                texture_s.eq(texture_s + (texture_t_half == texture_t_end)),
            ]

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
                    m.d.comb += advance_texture.eq(1)
                    m.d.sync += [
                        self.texture_writes.en.eq(texture_en),
                        texture_fsm_state.eq(2),
                        self.texture_writes.addr.eq(Cat(texture_t_half, texture_s)),
                    ]
            with m.Case(2):
                m.d.sync += self.texture_writes.data.eq(Cat(texture_remain, dma.data_stream.data))
                with m.If(dma.data_stream.ready & dma.data_stream.valid):
                    m.d.comb += advance_texture.eq(1)
                    m.d.sync += [
                        self.texture_writes.en.eq(texture_en),
                        texture_fsm_state.eq(0),
                        self.texture_writes.addr.eq(Cat(texture_t_half, texture_s)),
                    ]

        buffer_clear_word = Signal(1)

        with m.FSM():
            with m.State("READ_CMD"):
                m.d.comb += [
                    self.idle.eq(~dma.data_stream.valid & dma.control.idle),
                    dma.data_stream.ready.eq(1),
                ]
                with m.If(dma.data_stream.valid):
                    with m.Switch(dma.data_stream.data[:6]):
                        with m.Case(Command.DRAW_TRIANGLE):
                            m.d.sync += vertex_ctr.eq(0), vertex_half.eq(0)
                            m.d.sync += self.triangles.payload.texture_enable.eq(dma.data_stream.data[6])
                            m.d.sync += self.triangles.payload.texture_buffer.eq(dma.data_stream.data[7:9])
                            m.next = "READ_VERTEXES"
                        with m.Case(Command.READ_TEXTURE):
                            s_start = Signal(7)
                            s_end = Signal(7)
                            t_half_start = Signal(6)
                            t_half_end = Signal(6)

                            s_high = dma.data_stream.data[8]
                            m.d.comb += [
                                s_start.eq(Cat(dma.data_stream.data[9:15], s_high)),
                                s_end.eq(Cat(dma.data_stream.data[15:21], s_high)),
                            ]
                            t_high = dma.data_stream.data[21]
                            m.d.comb += [
                                t_half_start.eq(Cat(dma.data_stream.data[22:27], t_high)),
                                t_half_end.eq(Cat(dma.data_stream.data[27:32], t_high)),
                            ]

                            m.d.sync += [
                                self.texture_writes.buffer.eq(dma.data_stream.data[6:8]),
                                texture_s.eq(s_start),
                                texture_s_end.eq(s_end),

                                texture_t_start.eq(t_half_start),
                                texture_t_half.eq(t_half_start),
                                texture_t_end.eq(t_half_end),

                                texture_fsm_state.eq(0),
                            ]
                            m.next = "READ_TEXTURE"
                        with m.Case(Command.WAIT_IDLE):
                            m.next = "WAIT_IDLE"
                        with m.Case(Command.CLEAR_BUFFER):
                            m.d.sync += [
                                self.buffer_clears.payload.pattern.eq(dma.data_stream.data[8:]),
                            ]
                            m.next = "READ_BUFFER_CLEAR"
                        with m.Case(Command.WAIT_CLEAR_IDLE):
                            m.next = "WAIT_CLEAR_IDLE"
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
                    with m.If((texture_s == texture_s_end) & (texture_t_half == texture_t_end)):
                        m.next = "READ_CMD"
            with m.State("WAIT_IDLE"):
                with m.If(self.rasterizer_idle):
                    m.next = "READ_CMD"
            with m.State("READ_BUFFER_CLEAR"):
                m.d.comb += dma.data_stream.ready.eq(1)
                with m.Switch(buffer_clear_word):
                    with m.Case(0):
                        m.d.sync += self.buffer_clears.payload.base_addr.eq(dma.data_stream.data)
                    with m.Case(1):
                        m.d.sync += self.buffer_clears.payload.words.eq(dma.data_stream.data)
                with m.If(dma.data_stream.valid):
                    m.d.sync += buffer_clear_word.eq(buffer_clear_word + 1)
                    with m.If(buffer_clear_word):
                        m.next = "CLEAR_BUFFER"
            with m.State("CLEAR_BUFFER"):
                m.d.comb += self.buffer_clears.valid.eq(1)
                with m.If(self.buffer_clears.ready):
                    m.next = "READ_CMD"
            with m.State("WAIT_CLEAR_IDLE"):
                with m.If(self.clearer_idle):
                    m.next = "READ_CMD"

        return m
