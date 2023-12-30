from ..hal.alloc import Alloc
from ..hal.mmio import u32
from ..hal.rasterizer import Rasterizer
from .common import ScreenVertex


__all__ = ["CommandBuffer"]


BUFFER_SIZE_WORDS = 8192
BUFFER_COUNT = 2


class CommandBuffer:
    def __init__(self, rasterizer: Rasterizer, alloc: Alloc):
        self._rasterizer = rasterizer
        self._buffers = list(Buffer(alloc) for _ in range(BUFFER_COUNT))
        self._current_buffer = 0
        self._current_pos = 0

        self._buffers[self._current_buffer].reset()

    async def draw_triangle(self, texture: int | None, v0: ScreenVertex, v1: ScreenVertex, v2: ScreenVertex):
        await self.write_raw(
            0x01 |
            ((1 if texture is not None else 0) << 6) |
            ((texture if texture is not None else 0) << 7)
        )
        for v in [v0, v1, v2]:
            bits = v.pack()
            await self.write_raw(bits & 0xFFFF_FFFF)
            await self.write_raw(bits >> 32)

    async def load_texture(self, buffer: int, start_s: int, end_s: int, start_t: int, end_t: int, data: bytearray):
        assert 0 <= buffer < 4

        assert 0 <= start_s < 128
        assert 0 <= end_s < 128
        assert start_s <= end_s

        assert 0 <= start_t < 128
        assert 0 <= end_t < 128
        assert start_t % 2 == 0
        assert end_t % 2 == 1

        s_high = start_s >> 6
        assert s_high == (end_s >> 6)

        start_t_half = start_t // 2
        end_t_half = end_t // 2
        assert start_t_half < end_t_half
        t_high = start_t_half >> 5
        assert t_high == (end_t_half >> 5)

        expected_len = (end_s - start_s + 1) * ((end_t_half - start_t_half + 1) * 2) * 3
        assert len(data) == expected_len

        await self.write_raw(
                0x02 |
                (buffer << 6) |
                (s_high << 8) |
                ((start_s & 0b111_111) << 9) |
                ((end_s & 0b111_111) << 15) |
                (t_high << 21) |
                ((start_t_half & 0b11_111) << 22) |
                ((end_t_half & 0b11_111) << 27)
        )
        await self.write_slice(data)

    async def wait_idle(self):
        await self.write_raw(0x03)

    async def clear_buffer(self, addr: int, words: int, pattern: int):
        assert addr & 0x7f == 0

        await self.write_raw(0x04 | (pattern << 8))
        await self.write_raw(addr >> 7)
        await self.write_raw(words)

    async def wait_clear_idle(self):
        await self.write_raw(0x05)

    async def write_raw(self, word: int):
        await self._maybe_flip()
        self._buf().write(word)

    async def write_slice(self, data: bytearray):
        assert len(data) % 4 == 0
        while len(data) > 0:
            await self._maybe_flip()
            written = self._buf().write_bytes(data)
            data = data[written:]

    async def flush(self):
        buf = self._buf()
        if buf.empty():
            return

        await self._rasterizer.wait_cmd_dma()
        self._rasterizer.submit_command(buf.phys_addr, buf.finish())

        self._current_buffer = (self._current_buffer + 1) % BUFFER_COUNT
        self._buf().reset()

    async def _maybe_flip(self):
        if self._buf().full():
            await self.flush()

    def _buf(self):
        return self._buffers[self._current_buffer]


class Buffer:
    def __init__(self, alloc: Alloc):
        self.dma_buf, self.phys_addr = alloc.alloc(4 * BUFFER_SIZE_WORDS)
        self._map = self.dma_buf.map()
        self._pos = 0

    def empty(self):
        return self._pos == 0

    def full(self):
        return self._pos == BUFFER_SIZE_WORDS

    def reset(self):
        self.dma_buf.sync_start()
        self._pos = 0

    def finish(self):
        self.dma_buf.sync_end()
        return self._pos

    def write(self, val: int):
        assert not self.full()

        byte_pos = self._pos * 4
        self._map[byte_pos:byte_pos+4] = u32(val)
        self._pos += 1

    def write_bytes(self, vals: bytearray) -> int:
        assert len(vals) % 4 == 0
        assert not self.full()

        write = min(BUFFER_SIZE_WORDS - self._pos, len(vals) // 4)

        byte_pos = self._pos * 4
        self._map[byte_pos:byte_pos+write*4] = vals[:write*4]
        self._pos += write

        return write * 4
