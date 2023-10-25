from amaranth import *
from amaranth.lib import wiring
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

        # Cycle 2

        bx_ax_cy_ay_2 = Signal.like(bx_ax_cy_ay)
        by_ay_cx_ax_2 = Signal.like(by_ay_cx_ax)
        m.d.sync += [
            bx_ax_cy_ay_2.eq(bx_ax_cy_ay),
            by_ay_cx_ax_2.eq(by_ay_cx_ax),
        ]

        # Cycle 3

        m.d.sync += self.res.eq(bx_ax_cy_ay_2 - by_ay_cx_ax_2)

        return m


class PassthroughScaler(Component):
    area: In(24)
    area_trigger: In(1)

    points: In(PointStream)
    points_scaled: Out(PointStream)

    def elaborate(self, platform):
        m = Module()

        wiring.connect(m, wiring.flipped(self.points), wiring.flipped(self.points_scaled))

        return m


class Scaler(Component):
    area: In(24)
    area_trigger: In(1)

    points: In(PointStream)
    points_scaled: Out(PointStream)

    def __init__(self, div_unroll: int):
        self._div_unroll = div_unroll
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        m.submodules.divider = divider = Divider(24, self._div_unroll)
        m.d.comb += [
            divider.n.eq(0xFFFFFF),
            divider.d.eq(self.area),
            divider.trigger.eq(self.area_trigger),
        ]

        area_recip = Signal(24)
        div_done = Signal()
        with m.If(divider.done):
            m.d.sync += [
                area_recip.eq(divider.o),
                div_done.eq(1),
            ]
        with m.If(self.area_trigger):
            m.d.sync += div_done.eq(0)

        stall = Signal()

        p = Signal(Point)
        w0 = Signal(24)
        w1 = Signal(24)
        w2 = Signal(24)
        valid = Signal()

        with m.If(~stall):
            m.d.sync += [
                p.eq(self.points.payload.p),
                w0.eq(self.points.payload.w0 * area_recip),
                w1.eq(self.points.payload.w1 * area_recip),
                w2.eq(self.points.payload.w2 * area_recip),
                valid.eq(self.points.valid & div_done),
            ]

        m.d.comb += [
            self.points.ready.eq(~stall & div_done),
            self.points_scaled.valid.eq(valid),

            stall.eq(valid & ~self.points_scaled.ready),

            self.points_scaled.payload.p.eq(p),
            self.points_scaled.payload.w0.eq(w0),
            self.points_scaled.payload.w1.eq(w1),
            self.points_scaled.payload.w2.eq(w2),
        ]

        return m


class FIFOScaler(Component):
    area: In(24)
    area_trigger: In(1)

    points: In(PointStream)
    points_scaled: Out(PointStream)

    def __init__(self, div_unroll: int):
        self._div_unroll = div_unroll
        super().__init__()

    def elaborate(self, platform):
        # TODO: figure out how to avoid mixing area reciprocals for different triangles
        raise Exception("unimplemented")


class EdgeWalker(Component):
    triangle: In(TriangleStream)
    idle: Out(1)
    points: Out(PointStream)

    # Whether interpolation weights should be scaled by 1/area
    def __init__(self, scale_recip: bool = True, *, div_unroll: int = 1, use_fifo: bool = False):
        self._scale_recip = scale_recip
        self._div_unroll = div_unroll
        self._use_fifo = use_fifo
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        if self._scale_recip:
            if self._use_fifo:
                scaler = FIFOScaler(self._div_unroll)
            else:
                scaler = Scaler(self._div_unroll)
        else:
            scaler = PassthroughScaler()
        m.submodules.scaler = scaler

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

        w0_row = Signal.like(w0_orient2d.res)
        w1_row = Signal.like(w1_orient2d.res)
        w2_row = Signal.like(w2_orient2d.res)

        w0 = Signal.like(w0_row)
        w1 = Signal.like(w1_row)
        w2 = Signal.like(w2_row)

        m.d.comb += [
            scaler.points.payload.p.eq(p),
            scaler.points.payload.w0.eq(w0),
            scaler.points.payload.w1.eq(w1),
            scaler.points.payload.w2.eq(w2),
            scaler.area.eq(area_orient2d.res)
        ]
        wiring.connect(m, wiring.flipped(self.points), scaler.points_scaled)

        with m.FSM():
            with m.State("IDLE"):
                m.d.comb += self.idle.eq(~self.triangle.valid)
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
                    ]
                    m.next = "ORIENT2D_DELAY1"
            with m.State("ORIENT2D_DELAY1"):  # area cycle 1, w0/w1/w2 cycle 0
                # safe to change, values already in the pipeline
                m.d.comb += self.triangle.ready.eq(1)
                m.next = "ORIENT2D_DELAY2"
            with m.State("ORIENT2D_DELAY2"):  # area cycle 2, w0/w1/w2 cycle 1
                m.next = "ORIENT2D_DELAY3"
            with m.State("ORIENT2D_DELAY3"):  # area cycle 3, w0/w1/w2 cycle 2
                m.next = "ORIENT2D_DELAY4"
            with m.State("ORIENT2D_DELAY4"):  # area done, w0/w1/w2 cycle 3
                with m.If(area_orient2d.res <= 0):
                    m.next = "IDLE"
                with m.Else():
                    m.d.comb += scaler.area_trigger.eq(1)
                    m.next = "ORIENT2D_DELAY5"
            with m.State("ORIENT2D_DELAY5"):  # w0/w1/w2 done
                m.d.sync += [
                    w0_row.eq(w0_orient2d.res),
                    w1_row.eq(w1_orient2d.res),
                    w2_row.eq(w2_orient2d.res),
                    w0.eq(w0_orient2d.res),
                    w1.eq(w1_orient2d.res),
                    w2.eq(w2_orient2d.res),
                    p.x.eq(min_x),
                ]
                m.next = "WALK"
            with m.State("WALK"):
                with m.If(p.y > max_y):
                    m.next = "IDLE"
                with m.Elif(p.x > max_x):
                    m.d.sync += [
                        w0_row.eq(w0_row + b12),
                        w1_row.eq(w1_row + b20),
                        w2_row.eq(w2_row + b01),
                        w0.eq(w0_row + b12),
                        w1.eq(w1_row + b20),
                        w2.eq(w2_row + b01),
                        p.x.eq(min_x),
                        p.y.eq(p.y + 1),
                    ]
                with m.Else():
                    m.d.comb += scaler.points.valid.eq((w0 | w1 | w2) >= 0)
                    with m.If(~scaler.points.valid | scaler.points.ready):
                        m.d.sync += [
                            w0.eq(w0 + a12),
                            w1.eq(w1 + a20),
                            w2.eq(w2 + a01),
                            p.x.eq(p.x + 1),
                        ]

        return m
