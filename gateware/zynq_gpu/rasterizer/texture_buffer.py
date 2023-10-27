from amaranth import *
from amaranth.lib.wiring import Component, In
from .types import TextureBufferRead, TextureBufferWrite


__all__ = ["TextureBuffer"]


class TextureBuffer(Component):
    write: In(TextureBufferWrite)
    read: In(TextureBufferRead)

    def __init__(self):
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        side = 128
        n_pixels = side * side

        pixel_idx = Signal(14)
        read_buffer_0 = Signal(2)
        read_buffer_1 = Signal(2)
        sel_0 = Signal()
        sel_1 = Signal()
        m.d.comb += [
            pixel_idx.eq(Cat(self.read.t, self.read.s)),
        ]
        m.d.sync += [
            sel_0.eq(pixel_idx[0]),
            sel_1.eq(sel_0),

            read_buffer_0.eq(self.read.buffer),
            read_buffer_1.eq(read_buffer_0),
        ]

        for i in range(4):
            mem = Memory(width=48, depth=n_pixels // 2, name=f"texture_{i}", attrs={"RAM_STYLE": "BLOCK"})

            m.submodules[f"rp_{i}"] = rp = mem.read_port(transparent=False)
            m.submodules[f"wp_{i}"] = wp = mem.write_port()

            pipeline_reg = Signal.like(rp.data, name=f"pipeline_reg_{i}")

            m.d.comb += [
                wp.addr.eq(self.write.addr),
                wp.data.eq(self.write.data),
                wp.en.eq(self.write.en & (self.write.buffer == i)),

                rp.addr.eq(pixel_idx[1:]),
                rp.en.eq(self.read.en & (self.read.buffer == i)),
            ]
            m.d.sync += pipeline_reg.eq(rp.data)

            with m.If(read_buffer_1 == i):
                m.d.comb += self.read.color.eq(pipeline_reg.word_select(sel_1, 24)),

        return m
