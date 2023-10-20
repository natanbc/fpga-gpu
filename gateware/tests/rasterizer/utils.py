from dataclasses import dataclass
from typing import Iterable


__all__ = ["points", "points_recip", "Vertex", "BarycentricCoordinates"]


class Point2D:
    x: int
    y: int


@dataclass
class Vertex:
    x: int
    y: int
    z: int
    r: int
    g: int
    b: int
    

@dataclass
class BarycentricCoordinates:
    x: int
    y: int
    w0: int
    w1: int
    w2: int


def orient2d(a, b, c):
    return (b.x-a.x)*(c.y-a.y) - (b.y-a.y)*(c.x-a.x)


def points(v0: Vertex, v1: Vertex, v2: Vertex) -> Iterable[BarycentricCoordinates]:
    assert all(isinstance(v.x, int) and isinstance(v.y, int) for v in [v0, v1, v2])

    min_x = min(v.x for v in [v0, v1, v2])
    max_x = max(v.x for v in [v0, v1, v2])
    min_y = min(v.y for v in [v0, v1, v2])
    max_y = max(v.y for v in [v0, v1, v2])

    a01 = v0.y - v1.y
    a12 = v1.y - v2.y
    a20 = v2.y - v0.y
    b01 = v1.x - v0.x
    b12 = v2.x - v1.x
    b20 = v0.x - v2.x

    p = Point2D()
    p.x = min_x
    p.y = min_y
    w0_row = orient2d(v1, v2, p)
    w1_row = orient2d(v2, v0, p)
    w2_row = orient2d(v0, v1, p)

    if orient2d(v0, v1, v2) == 0:
        return

    while p.y <= max_y:
        p.x = min_x

        w0 = w0_row
        w1 = w1_row
        w2 = w2_row
        while p.x <= max_x:
            if w0 >= 0 and w1 >= 0 and w2 >= 0:
                yield BarycentricCoordinates(p.x, p.y, w0, w1, w2)
            w0 += a12
            w1 += a20
            w2 += a01
            p.x += 1

        w0_row += b12
        w1_row += b20
        w2_row += b01
        p.y += 1


def points_recip(v0: Vertex, v1: Vertex, v2: Vertex) -> Iterable[BarycentricCoordinates]:
    area = orient2d(v0, v1, v2)
    area_recip = 0xFFFFFF // area
    for c in points(v0, v1, v2):
        yield BarycentricCoordinates(c.x, c.y, c.w0*area_recip, c.w1*area_recip, c.w2*area_recip)
