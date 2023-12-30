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
        assert texture is None
        # No texture bits to set yet
        await self.write_raw(0x01)
        for v in [v0, v1, v2]:
            bits = v.pack()
            await self.write_raw(bits & 0xFFFF_FFFF)
            await self.write_raw(bits >> 32)

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
