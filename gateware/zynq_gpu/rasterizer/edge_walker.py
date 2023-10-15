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


class Orient2D(Component):
    a: In(Point)
    b: In(Point)
    c: In(Point)

    res: Out(signed(25))

    def elaborate(self, platform):
        m = Module()

        # Cycle 0

        _bx_ax = self.b.x - self.a.x
        _cy_ay = self.c.y - self.a.y
        _by_ay = self.b.y - self.a.y
        _cx_ax = self.c.x - self.a.x

        bx_ax = Signal.like(_bx_ax)
        cy_ay = Signal.like(_cy_ay)
        by_ay = Signal.like(_by_ay)
        cx_ax = Signal.like(_cx_ax)
        m.d.sync += [
            bx_ax.eq(_bx_ax),
            cy_ay.eq(_cy_ay),
            by_ay.eq(_by_ay),
            cx_ax.eq(_cx_ax),
        ]

        # Cycle 1

        _bx_ax_cy_ay = bx_ax * cy_ay
        _by_ay_cx_ax = by_ay * cx_ax

        bx_ax_cy_ay = Signal.like(_bx_ax_cy_ay)
        by_ay_cx_ax = Signal.like(_by_ay_cx_ax)
        m.d.sync += [
            bx_ax_cy_ay.eq(_bx_ax_cy_ay),
            by_ay_cx_ax.eq(_by_ay_cx_ax),
        ]

        m.d.comb += self.res.eq(bx_ax_cy_ay - by_ay_cx_ax)

        return m


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

        m.submodules.divider = divider = Divider(24, 3)

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

        m.submodules.area_orient2d = area_orient2d = Orient2D()
        m.d.comb += [
            area_orient2d.a.eq(self.triangle.payload.v0),
            area_orient2d.b.eq(self.triangle.payload.v1),
            area_orient2d.c.eq(self.triangle.payload.v2),
        ]

        m.submodules.w0_orient2d = w0_orient2d = Orient2D()
        m.d.comb += [
            w0_orient2d.a.eq(self.triangle.payload.v1),
            w0_orient2d.b.eq(self.triangle.payload.v2),
            w0_orient2d.c.eq(p),
        ]

        m.submodules.w1_orient2d = w1_orient2d = Orient2D()
        m.d.comb += [
            w1_orient2d.a.eq(self.triangle.payload.v2),
            w1_orient2d.b.eq(self.triangle.payload.v0),
            w1_orient2d.c.eq(p),
        ]

        m.submodules.w2_orient2d = w2_orient2d = Orient2D()
        m.d.comb += [
            w2_orient2d.a.eq(self.triangle.payload.v0),
            w2_orient2d.b.eq(self.triangle.payload.v1),
            w2_orient2d.c.eq(p),
        ]

        area_recip = Signal(24)

        w0_row = Signal.like(w0_orient2d.res)
        w1_row = Signal.like(w1_orient2d.res)
        w2_row = Signal.like(w2_orient2d.res)

        w0 = Signal.like(w0_row)
        w1 = Signal.like(w1_row)
        w2 = Signal.like(w2_row)

        m.d.comb += [
            self.points.payload.p.eq(p),
            divider.n.eq(0xFFFFFF),
        ]
        if self._scale_recip:
            m.d.comb += [
                divider.d.eq(area_orient2d.res),
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

        div_done = Signal()
        with m.If(divider.done):
            m.d.sync += [
                area_recip.eq(divider.o),
                div_done.eq(1),
            ]

        with m.FSM():
            with m.State("IDLE"):
                m.d.comb += self.idle.eq(1)
                with m.If(self.triangle.valid):  # area cycle 0, w0/w1/w2 not started
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
                        div_done.eq(0),
                    ]
                    m.next = "ORIENT2D_DELAY1"
            with m.State("ORIENT2D_DELAY1"):  # area cycle 1, w0/w1/w2 cycle 0
                # safe to change, values already in the pipeline
                m.d.comb += self.triangle.ready.eq(1)
                m.next = "ORIENT2D_DELAY2"
            with m.State("ORIENT2D_DELAY2"):  # area done, w0/w1/w2 cycle 1
                with m.If(area_orient2d.res == 0):
                    m.next = "IDLE"
                with m.Else():
                    m.d.comb += divider.trigger.eq(1)
                    m.next = "ORIENT2D_DELAY3"
            with m.State("ORIENT2D_DELAY3"):  # w0/w1/w2 done
                m.d.sync += [
                    w0_row.eq(w0_orient2d.res),
                    w1_row.eq(w1_orient2d.res),
                    w2_row.eq(w2_orient2d.res),
                ]
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
                    valid_point = Signal()
                    m.d.comb += valid_point.eq((w0 | w1 | w2) >= 0)
                    if self._scale_recip:
                        m.d.comb += self.points.valid.eq(div_done & valid_point)
                    else:
                        m.d.comb += self.points.valid.eq(valid_point)
                    with m.If(~valid_point | (self.points.valid & self.points.ready)):
                        m.d.sync += [
                            w0.eq(w0 + a12),
                            w1.eq(w1 + a20),
                            w2.eq(w2 + a01),
                            p.x.eq(p.x + 1),
                        ]

        return m
