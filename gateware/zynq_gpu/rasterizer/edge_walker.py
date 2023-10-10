from amaranth import *
from amaranth.lib.data import StructLayout
from amaranth.lib.wiring import Component, Signature, In, Out
from ..utils import Divider


__all__ = ["Point", "TriangleStream", "PointStream", "EdgeWalker"]


Point = StructLayout({
    "x": unsigned(11),
    "y": unsigned(11),
})


TriangleStream = Signature({
    "valid": Out(1),
    "ready": In(1),
    "payload": Out(StructLayout({
        "v0": Point,
        "v1": Point,
        "v2": Point,
    })),
})


PointStream = Signature({
    "valid": Out(1),
    "ready": In(1),
    "payload": Out(StructLayout({
        "p": Point,
        # UQ(0.24) (which can be converted to Q(1.24) with a 0 in the sign bit for DSP48E1 usage)
        "w0": unsigned(24),
        "w1": unsigned(24),
        "w2": unsigned(24),
    })),
})


def min3(a, b, c):
    min12 = Mux(a < b, a, b)
    return Mux(min12 < c, min12, c)


def max3(a, b, c):
    max12 = Mux(a > b, a, b)
    return Mux(max12 > c, max12, c)


def orient2d(a, b, c):
    return (b.x-a.x)*(c.y-a.y) - (b.y-a.y)*(c.x-a.x)


class EdgeWalker(Component):
    triangle: In(TriangleStream)
    idle: Out(1)
    points: Out(PointStream)

    # Whether interpolation weights should be scaled by 1/area
    def __init__(self, scale_recip: bool = True):
        self._scale_recip = scale_recip
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        m.submodules.divider = divider = Divider()

        _a01 = self.triangle.payload.v0.y - self.triangle.payload.v1.y
        _a12 = self.triangle.payload.v1.y - self.triangle.payload.v2.y
        _a20 = self.triangle.payload.v2.y - self.triangle.payload.v0.y
        _b01 = self.triangle.payload.v1.x - self.triangle.payload.v0.x
        _b12 = self.triangle.payload.v2.x - self.triangle.payload.v1.x
        _b20 = self.triangle.payload.v0.x - self.triangle.payload.v2.x

        _min_x = min3(self.triangle.payload.v0.x, self.triangle.payload.v1.x, self.triangle.payload.v2.x)
        _min_y = min3(self.triangle.payload.v0.y, self.triangle.payload.v1.y, self.triangle.payload.v2.y)
        _max_x = max3(self.triangle.payload.v0.x, self.triangle.payload.v1.x, self.triangle.payload.v2.x)
        _max_y = max3(self.triangle.payload.v0.y, self.triangle.payload.v1.y, self.triangle.payload.v2.y)

        _p = Point(Signal(Point))
        m.d.comb += _p.x.eq(_min_x), _p.y.eq(_min_y)

        a01 = Signal.like(_a01)
        a12 = Signal.like(_a12)
        a20 = Signal.like(_a20)
        b01 = Signal.like(_b01)
        b12 = Signal.like(_b12)
        b20 = Signal.like(_b20)

        min_x = Signal.like(_min_x)
        max_x = Signal.like(_max_x)
        max_y = Signal.like(_max_y)

        p = Signal.like(_p)
        _w0_row = orient2d(self.triangle.payload.v1, self.triangle.payload.v2, p)
        _w1_row = orient2d(self.triangle.payload.v2, self.triangle.payload.v0, p)
        _w2_row = orient2d(self.triangle.payload.v0, self.triangle.payload.v1, p)

        _area = orient2d(self.triangle.payload.v0, self.triangle.payload.v1, self.triangle.payload.v2)

        area_recip = Signal(24)

        w0_row = Signal.like(_w0_row)
        w1_row = Signal.like(_w1_row)
        w2_row = Signal.like(_w2_row)

        w0 = Signal.like(w0_row)
        w1 = Signal.like(w1_row)
        w2 = Signal.like(w2_row)

        m.d.comb += [
            self.points.payload.p.eq(p),
            divider.n.eq(0xFFFFFF),
        ]
        if self._scale_recip:
            m.d.comb += [
                self.points.payload.w0.eq(w0 * area_recip),
                self.points.payload.w1.eq(w1 * area_recip),
                self.points.payload.w2.eq(w2 * area_recip),
            ]
        else:
            m.d.comb += [
                self.points.payload.w0.eq(w0),
                self.points.payload.w1.eq(w1),
                self.points.payload.w2.eq(w2),
            ]

        with m.FSM():
            with m.State("IDLE"):
                m.d.comb += self.idle.eq(1)
                with m.If(self.triangle.valid):
                    m.d.sync += [
                        a01.eq(_a01),
                        a12.eq(_a12),
                        a20.eq(_a20),
                        b01.eq(_b01),
                        b12.eq(_b12),
                        b20.eq(_b20),
                        p.eq(_p),
                        min_x.eq(_min_x),
                        max_x.eq(_max_x),
                        max_y.eq(_max_y),
                        divider.d.eq(_area),
                    ]
                    m.next = "CALC_ORIENT"
            with m.State("CALC_ORIENT"):
                m.d.comb += divider.trigger.eq(1)
                m.d.comb += self.triangle.ready.eq(1)
                m.d.sync += [
                    w0_row.eq(_w0_row),
                    w1_row.eq(_w1_row),
                    w2_row.eq(_w2_row),
                ]
                if self._scale_recip:
                    with m.If(divider.done):
                        m.d.sync += area_recip.eq(divider.o)
                        m.next = "LOOP_Y"
                    with m.Else():
                        m.next = "WAIT_DIV"
                else:
                    m.next = "LOOP_Y"
            if self._scale_recip:
                with m.State("WAIT_DIV"):
                    with m.If(divider.done):
                        m.d.sync += area_recip.eq(divider.o)
                        m.next = "LOOP_Y"
            with m.State("LOOP_Y"):
                with m.If(p.y > max_y):
                    m.next = "IDLE"
                with m.Else():
                    m.d.sync += [
                        p.x.eq(min_x),
                        w0.eq(w0_row),
                        w1.eq(w1_row),
                        w2.eq(w2_row),
                    ]
                    m.next = "LOOP_X"
            with m.State("LOOP_X"):
                with m.If(p.x > max_x):
                    m.d.sync += [
                        w0_row.eq(w0_row + b12),
                        w1_row.eq(w1_row + b20),
                        w2_row.eq(w2_row + b01),
                        p.y.eq(p.y + 1),
                    ]
                    m.next = "LOOP_Y"
                with m.Else():
                    m.d.comb += self.points.valid.eq((w0 | w1 | w2) >= 0)
                    with m.If(~self.points.valid | self.points.ready):
                        m.d.sync += [
                            w0.eq(w0 + a12),
                            w1.eq(w1 + a20),
                            w2.eq(w2 + a01),
                            p.x.eq(p.x + 1),
                        ]

        return m
