from typing import Iterable, Mapping
from .command import CommandBuffer
from .common import GlCommon, GouraudVertex, TextureVertex
from ..hal import Alloc, Rasterizer, Uio

__all__ = ["Gl", "TextureBuffer"]


class TextureBuffer:
    def __init__(self, *, _id: int):
        self._id = _id
        self._data = bytearray(128*128*3)
        self._dirty = True

    def load(self, data: bytearray):
        assert len(data) == len(self._data)
        for q in range(4):
            dst_base = 64 * 64 * q
            sx, sy = 64 * (q & 1), 64 * (q >> 1)
            for y in range(64):
                dst_offset = (dst_base + y * 64) * 3
                src_offset = ((sy + y) * 128 + sx) * 3
                self._data[dst_offset:dst_offset + 64*3] = data[src_offset:src_offset + 64 * 3]
        self._dirty = True


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

        self._next_texture_buffer_id = 1
        self._loaded_texture_buffers = [0, 0, 0, 0]
        self._next_buffer_replace = 0

    def create_texture_buffer(self):
        i = self._next_texture_buffer_id
        self._next_texture_buffer_id += 1
        return TextureBuffer(_id=i)

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

    async def draw_texture(
            self,
            texture_buffer: TextureBuffer,
            vertex_buffer: Mapping[int, TextureVertex] | list[TextureVertex],
            index_buffer: Iterable[int],
    ):
        # noinspection PyProtectedMember
        buf_id = texture_buffer._id
        if buf_id in self._loaded_texture_buffers:
            hw_buf_id = self._loaded_texture_buffers.index(buf_id)
            # noinspection PyProtectedMember
            load = texture_buffer._dirty
        else:
            hw_buf_id = self._next_buffer_replace
            self._next_buffer_replace = (self._next_buffer_replace + 1) % 4
            load = True
        if load:
            qs = 64 * 64 * 3
            # noinspection PyProtectedMember
            buf = texture_buffer._data
            await self._cmd.load_texture(hw_buf_id, 0, 63, 0, 63, buf[:qs])
            await self._cmd.load_texture(hw_buf_id, 0, 63, 64, 127, buf[qs:2*qs])
            await self._cmd.load_texture(hw_buf_id, 64, 127, 0, 63, buf[2*qs:3*qs])
            await self._cmd.load_texture(hw_buf_id, 64, 127, 64, 127, buf[3*qs:])
            texture_buffer._dirty = False

        for v0, v1, v2 in self._transform_texture(vertex_buffer, index_buffer):
            await self._cmd.draw_triangle(hw_buf_id, v0, v1, v2)
