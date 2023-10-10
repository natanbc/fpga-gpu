from typing import Tuple, Iterable


__all__ = ["points", "points_recip"]


def orient2d(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def points(v0: Tuple[int, int], v1: Tuple[int, int], v2: Tuple[int, int]) -> Iterable[Tuple[int, int, int, int, int]]:
    assert all(isinstance(v[0], int) and isinstance(v[1], int) for v in [v0, v1, v2])

    min_x = min(v[0] for v in [v0, v1, v2])
    max_x = max(v[0] for v in [v0, v1, v2])
    min_y = min(v[1] for v in [v0, v1, v2])
    max_y = max(v[1] for v in [v0, v1, v2])

    a01 = v0[1] - v1[1]
    a12 = v1[1] - v2[1]
    a20 = v2[1] - v0[1]
    b01 = v1[0] - v0[0]
    b12 = v2[0] - v1[0]
    b20 = v0[0] - v2[0]

    p = [min_x, min_y]
    w0_row = orient2d(v1, v2, p)
    w1_row = orient2d(v2, v0, p)
    w2_row = orient2d(v0, v1, p)

    while p[1] <= max_y:
        p[0] = min_x

        w0 = w0_row
        w1 = w1_row
        w2 = w2_row
        while p[0] <= max_x:
            if w0 >= 0 and w1 >= 0 and w2 >= 0:
                yield p[0], p[1], w0, w1, w2
            w0 += a12
            w1 += a20
            w2 += a01
            p[0] += 1

        w0_row += b12
        w1_row += b20
        w2_row += b01
        p[1] += 1


def points_recip(v0: Tuple[int, int], v1: Tuple[int, int], v2: Tuple[int, int]) -> Iterable[Tuple[int, int, int, int, int]]:
    area = orient2d(v0, v1, v2)
    area_recip = 0xFFFFFF // area
    for x, y, w0, w1, w2 in points(v0, v1, v2):
        yield x, y, w0*area_recip, w1*area_recip, w2*area_recip

