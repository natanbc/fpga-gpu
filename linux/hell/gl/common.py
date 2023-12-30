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


CLIP_NEG_X = 0x01
CLIP_POS_X = 0x02
CLIP_NEG_Y = 0x04
CLIP_POS_Y = 0x08
CLIP_NEG_Z = 0x10
CLIP_POS_Z = 0x20


@dataclass(slots=True)
class ClipVertex:
    x: float
    y: float
    z: float
    w: float
    r_s: float
    g_t: float
    b: float

    def pos(self, idx: int) -> float:
        match idx:
            case 0: return self.x
            case 1: return self.y
            case 2: return self.z
            case 3: return self.w
            case _: raise IndexError()

    def classify(self) -> int:
        code = 0
        if self.x < -self.w:
            code |= CLIP_NEG_X
        if self.x > self.w:
            code |= CLIP_POS_X
        if self.y < -self.w:
            code |= CLIP_NEG_Y
        if self.y > self.w:
            code |= CLIP_POS_Y
        if self.z < -self.w:
            code |= CLIP_NEG_Z
        if self.z > self.w:
            code |= CLIP_POS_Z
        return code


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


def _clip_against_plane(vertices: list[ClipVertex], plane: int) -> list[ClipVertex]:
    sign, index = {
        CLIP_NEG_X: (-1.0, 0),
        CLIP_POS_X: (1.0, 0),
        CLIP_NEG_Y: (-1.0, 1),
        CLIP_POS_Y: (1.0, 1),
        CLIP_NEG_Z: (-1.0, 2),
        CLIP_POS_Z: (1.0, 2),
    }[plane]

    res = []
    for i, vertex in enumerate(vertices):
        v0 = vertices[-1] if i == 0 else vertices[i - 1]
        v1 = vertex

        p0 = v0.pos(index) * sign
        w0 = v0.w
        p1 = v1.pos(index) * sign
        w1 = v1.w

        if p0 < w0:
            res.append(v0)

        if (p0 < w0 and p1 >= w1) or (p0 >= w0 and p1 < w1):
            denom = -p0 + p1 + w0 - w1
            if abs(denom) > 0.001:
                t = (-p0 + w0) / denom
                res.append(ClipVertex(
                    v0.x * (1.0 - t) + v1.x * t,
                    v0.y * (1.0 - t) + v1.y * t,
                    v0.z * (1.0 - t) + v1.z * t,
                    v0.w * (1.0 - t) + v1.w * t,

                    v0.r_s * (1.0 - t) + v1.r_s * t,
                    v0.g_t * (1.0 - t) + v1.g_t * t,
                    v0.b * (1.0 - t) + v1.b * t,
                ))

    return res


def _clip(t: (ClipVertex, ClipVertex, ClipVertex)) -> Iterable[Tuple[ClipVertex, ClipVertex, ClipVertex]]:
    c0 = t[0].classify()
    c1 = t[1].classify()
    c2 = t[2].classify()
    if (c0 & c1 & c2) != 0:
        return

    vertices = list(t)
    if (c0 | c1 | c2) != 0:
        for plane in [CLIP_NEG_X, CLIP_POS_X, CLIP_NEG_Y, CLIP_POS_Y, CLIP_NEG_Z, CLIP_POS_Z]:
            vertices = _clip_against_plane(vertices, plane)

        if len(vertices) > 0 and ((vertices[0].classify() & vertices[1].classify() & vertices[2].classify()) != 0):
            return

    for i in range(2, len(vertices) + 1):
        yield vertices[0], vertices[i - 2], vertices[i - 1]


def _to_screen(
        v: ClipVertex,
        scale_device,
        screen_attr_map: Callable[[float, float, float], Tuple[int, int, int]],
) -> ScreenVertex:
    r_s, g_t, b = screen_attr_map(v.r_s, v.g_t, v.b)
    rv = ScreenVertex(
        int(((v.x / v.w) * 0.5 + 0.5) * scale_device[0]),
        int(((v.y / v.w) * -0.5 + 0.5) * scale_device[1]),
        int(((v.z / v.w) * -0.5 + 0.5) * scale_device[2]),
        r_s,
        g_t,
        b,
    )

    # assert 0 <= rv.x <= scale_device[0], f"{v.x/v.w}, {v}"
    # assert 0 <= rv.y <= scale_device[1], f"{v.y/v.w}, {v}"
    # assert 0 <= rv.z <= scale_device[2], f"{v.z/v.w}, {v}"
    # assert 0 <= rv.r_s <= 255, f"{rv}"
    # assert 0 <= rv.g_t <= 255, f"{v}"
    # assert 0 <= rv.b <= 255, f"{v}"

    return rv


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

    def _transform_texture(
            self,
            vertex_buffer: Mapping[int, TextureVertex] | list[TextureVertex],
            index_buffer: Iterable[int],
    ) -> Iterable[ScreenVertex]:
        yield from self._transform(
            vertex_buffer,
            index_buffer,
            lambda v: (v.s, v.t, 0.0),
            lambda s, t, _: (int((1.0 - s) * 255), int(t * 255), 0),
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
