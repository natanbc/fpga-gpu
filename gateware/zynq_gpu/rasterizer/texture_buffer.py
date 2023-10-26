from amaranth import *
from amaranth.lib.wiring import Component, In
from .types import TextureBufferRead, TextureBufferWrite


__all__ = ["TextureBuffer"]


class TextureBuffer(Component):
    write: In(TextureBufferWrite)
    read: In(TextureBufferRead)

    def elaborate(self, platform):
        m = Module()

        n_pixels = 128 * 128

        pixel_idx = Signal(14)
        read_buffer = Signal(2)
        sel = Signal()
        m.d.comb += [
            pixel_idx.eq(self.read.s * 128 + self.read.t),

        ]
        m.d.sync += sel.eq(pixel_idx[0]), read_buffer.eq(self.read.buffer)

        for i in range(4):
            mem = Memory(width=48, depth=n_pixels // 2, name=f"texture_{i}")

            m.submodules[f"rp_{i}"] = rp = mem.read_port()
            m.submodules[f"wp_{i}"] = wp = mem.write_port()

            assert len(wp.addr) == 13

            m.d.comb += [
                wp.addr.eq(self.write.addr),
                wp.data.eq(self.write.data),
                wp.en.eq(self.write.en & (self.write.buffer == i)),

                rp.addr.eq(pixel_idx[1:]),
                rp.en.eq(self.read.en & (self.read.buffer == i)),
            ]

            with m.If(read_buffer == i):
                m.d.comb += self.read.color.eq(rp.data.word_select(24, sel)),

        return m
