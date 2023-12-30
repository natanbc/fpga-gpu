from typing import Iterable, Mapping
from .command import CommandBuffer
from .common import GlCommon, GouraudVertex
from ..hal import Alloc, Rasterizer, Uio

__all__ = ["Gl"]


class Gl(GlCommon):
    def __init__(self):
        super().__init__()

        self._rast = Rasterizer(Uio("rasterizer"))

        alloc = Alloc()

        self._cmd = CommandBuffer(self._rast, alloc)

        z_size = self.width * self.height * 2
        z_size = (z_size + 4095) // 4096 * 4096

        self._depth_buffers = list(alloc.alloc(z_size) for _ in range(2))
        self._depth_buffer_idx = 0

    async def begin_frame(self):
        self._rast.set_buffers(
            self._frame_buffers[self._frame_buffer_idx][1],
            self._depth_buffers[self._depth_buffer_idx][1],
        )

        await self._cmd.wait_clear_idle()
        self._depth_buffer_idx = (self._depth_buffer_idx + 1) % len(self._depth_buffers)

        db, addr = self._depth_buffers[self._depth_buffer_idx]
        await self._cmd.clear_buffer(addr, db.size // 8, 0)

    async def end_frame(self, draw: bool):
        next_fb_idx = (self._frame_buffer_idx + 1) % len(self._frame_buffers)
        fb, fb_addr = self._frame_buffers[next_fb_idx]
        await self._cmd.clear_buffer(fb_addr, fb.size // 8, 0xFFFFFF)

        await self._cmd.wait_idle()
        await self._cmd.flush()
        await self._rast.wait_cmd()

        await super()._end_frame(draw)

    async def draw_gouraud(
            self,
            vertex_buffer: Mapping[int, GouraudVertex] | list[GouraudVertex],
            index_buffer: Iterable[int],
    ):
        for v0, v1, v2 in self._transform_gouraud(vertex_buffer, index_buffer):
            await self._cmd.draw_triangle(None, v0, v1, v2)
