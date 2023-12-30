import enum
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping, Optional, Tuple, TypeVar
from ..hal import Alloc, DisplayController, Uio
import glm

__all__ = ["GouraudVertex", "TextureVertex", "CullMode", "FrontFace", "ScreenVertex", "GlCommon"]


@dataclass(slots=True)
class GouraudVertex:
    x: float
    y: float
    z: float
    r: float
    g: float
    b: float


@dataclass(slots=True)
class TextureVertex:
    x: float
    y: float
    z: float
    s: float
    t: float


class CullMode(enum.Enum):
    BACK_FACE = enum.auto()
    FRONT_FACE = enum.auto()
    DISABLED = enum.auto()


class FrontFace(enum.Enum):
    CLOCKWISE = enum.auto()
    COUNTER_CLOCKWISE = enum.auto()


@dataclass(slots=True)
class ClipVertex:
    x: float
    y: float
    z: float
    w: float
    r_s: float
    g_t: float
    b: float


@dataclass(slots=True)
class ScreenVertex:
    x: int
    y: int
    z: int
    r_s: int
    g_t: int
    b: int

    def __post_init__(self):
        assert 0 <= self.x < (1 << 11)
        assert 0 <= self.y < (1 << 11)
        assert 0 <= self.z < (1 << 16)
        assert 0 <= self.r_s < (1 << 8)
        assert 0 <= self.g_t < (1 << 8)
        assert 0 <= self.b < (1 << 8)

    def pack(self):
        return self.x | (self.y << 11) | (self.z << 22) | (self.r_s << 38) | (self.g_t << 46) | (self.b << 54)


_V = TypeVar("_V")


def _iter_triangles(
        vertex_buffer: Mapping[int, _V] | list[_V],
        index_buffer: Iterable[int],
) -> Iterable[Tuple[_V, _V, _V]]:
    it = iter(index_buffer)
    while True:
        try:
            yield vertex_buffer[next(it)], vertex_buffer[next(it)], vertex_buffer[next(it)]
        except StopIteration:
            return


def _to_clip(
        v: _V,
        pvm,
        clip_attr_map: Callable[[_V], Tuple[float, float, float]],
) -> ClipVertex:
    # pos = pvm @ np.array([v.x, v.y, v.z, 1.0])
    pos = pvm * glm.vec4(v.x, v.y, v.z, 1.0)
    r_s, g_t, b = clip_attr_map(v)
    return ClipVertex(
        pos.x,
        pos.y,
        pos.z,
        pos.w,
        r_s,
        g_t,
        b,
    )


def _orientation(t: (ClipVertex, ClipVertex, ClipVertex)):
    ax, ay = t[0].x / t[0].w, t[0].y / t[0].w
    bx, by = t[1].x / t[1].w, t[1].y / t[1].w
    cx, cy = t[2].x / t[2].w, t[2].y / t[2].w
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def _clip(t: (ClipVertex, ClipVertex, ClipVertex)) -> Iterable[Tuple[ClipVertex, ClipVertex, ClipVertex]]:
    # TODO
    yield t


def _to_screen(
        v: ClipVertex,
        scale_device,
        screen_attr_map: Callable[[float, float, float], Tuple[int, int, int]],
) -> ScreenVertex:
    assert -1.0 <= v.x <= 1.0
    assert -1.0 <= v.y <= 1.0
    assert -1.0 <= v.z <= 1.0
    assert 0.0 <= v.r_s <= 1.0
    assert 0.0 <= v.g_t <= 1.0
    assert 0.0 <= v.b <= 1.0

    r_s, g_t, b = screen_attr_map(v.r_s, v.g_t, v.b)
    v = ScreenVertex(
        int(((v.x / v.w) * 0.5 + 0.5) * scale_device[0]),
        int(((v.y / v.w) * -0.5 + 0.5) * scale_device[1]),
        int(((v.z / v.w) * -0.5 + 0.5) * scale_device[2]),
        r_s,
        g_t,
        b,
    )

    return v


class GlCommon:
    def __init__(self):
        self._dc = DisplayController(Uio("display_controller"))

        self._view = glm.identity(glm.fmat4x4)
        self._projection = glm.identity(glm.fmat4x4)
        self._projection_view = glm.identity(glm.fmat4x4)
        self._model = glm.identity(glm.fmat4x4)
        self._scale_device = glm.vec3(self._dc.width - 1, self._dc.height - 1, 65535.0)

        alloc = Alloc()

        fb_size = self._dc.width * self._dc.height * 3
        fb_size = (fb_size + 4095) // 4096 * 4096

        self._frame_buffers = list(alloc.alloc(fb_size) for _ in range(3))
        self._frame_buffer_idx = 0

        self.cull_mode = CullMode.DISABLED
        self.front_face = FrontFace.COUNTER_CLOCKWISE

    @property
    def width(self):
        return self._dc.width

    @property
    def height(self):
        return self._dc.height

    @property
    def view(self) -> glm.fmat4x4:
        return self._view

    @view.setter
    def view(self, value: glm.fmat4x4):
        self._projection_view = self._projection * value
        self._view = value

    @property
    def projection(self) -> glm.fmat4x4:
        return self._projection

    @projection.setter
    def projection(self, value: glm.fmat4x4):
        self._projection_view = value * self._view
        self._projection = value

    @property
    def model(self) -> glm.fmat4x4:
        return self._model

    @model.setter
    def model(self, value: glm.fmat4x4):
        self._model = value

    def _transform_gouraud(
            self,
            vertex_buffer: Mapping[int, GouraudVertex] | list[GouraudVertex],
            index_buffer: Iterable[int],
    ) -> Iterable[ScreenVertex]:
        yield from self._transform(
            vertex_buffer,
            index_buffer,
            lambda v: (v.r, v.g, v.b),
            lambda r, g, b: (int(r * 255), int(g * 255), int(b * 255)),
        )

    async def _end_frame(self, draw: bool):
        if draw:
            self._dc.draw_frame(self._frame_buffers[self._frame_buffer_idx][1])
            await self._dc.wait_end_of_frame()
        self._frame_buffer_idx = (self._frame_buffer_idx + 1) % len(self._frame_buffers)

    def _transform(
            self,
            vertex_buffer: Mapping[int, _V] | list[_V],
            index_buffer: Iterable[int],
            clip_attr_map: Callable[[_V], Tuple[float, float, float]],
            screen_attr_map: Callable[[float, float, float], Tuple[int, int, int]],
    ) -> Iterable[Tuple[ScreenVertex, ScreenVertex, ScreenVertex]]:
        pvm = self._projection_view * self._model
        for trig in _iter_triangles(vertex_buffer, index_buffer):
            trig = tuple(map(lambda v: _to_clip(v, pvm, clip_attr_map), trig))
            trig = self._cull(trig)
            if not trig:
                continue
            for final_trig in _clip(trig):
                yield tuple(map(lambda v: _to_screen(v, self._scale_device, screen_attr_map), final_trig))

    def _cull(
            self,
            triangle: (ScreenVertex, ScreenVertex, ScreenVertex)
    ) -> Optional[Tuple[ScreenVertex, ScreenVertex, ScreenVertex]]:
        if self.front_face == FrontFace.COUNTER_CLOCKWISE:
            triangle = (triangle[0], triangle[2], triangle[1])

        match self.cull_mode:
            case CullMode.BACK_FACE:
                pass
            case CullMode.FRONT_FACE:
                if _orientation(triangle) < 0.0:
                    return None
                triangle = (triangle[0], triangle[2], triangle[1])
            case CullMode.DISABLED:
                if _orientation(triangle) > 0.0:
                    triangle = (triangle[0], triangle[2], triangle[1])

        return triangle
