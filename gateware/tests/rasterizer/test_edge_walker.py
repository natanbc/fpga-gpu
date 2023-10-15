from amaranth.sim import *
from zynq_gpu.rasterizer import EdgeWalker
import unittest
from ..utils import wait_until
from .utils import points, points_recip


def submit_triangle(s, v0, v1, v2):
    yield s.payload.v0.x.eq(v0[0])
    yield s.payload.v0.y.eq(v0[1])
    yield s.payload.v1.x.eq(v1[0])
    yield s.payload.v1.y.eq(v1[1])
    yield s.payload.v2.x.eq(v2[0])
    yield s.payload.v2.y.eq(v2[1])
    yield s.valid.eq(1)
    yield from wait_until(s.ready)
    yield s.valid.eq(0)
    yield


class EdgeWalkerTest(unittest.TestCase):
    @staticmethod
    def _test_coordinates(triangles, count):
        dut = EdgeWalker()

        submit_done = False

        def trig_feed():
            nonlocal submit_done

            yield Passive()
            for v0, v1, v2 in triangles:
                yield from submit_triangle(dut.triangle, v0, v1, v2)
            submit_done = True

        def point_read():
            st = [[False]*10 for _ in range(10)]
            exp_st = [[False]*10 for _ in range(10)]

            for v0, v1, v2 in triangles:
                for x, y, _, _, _ in points(v0, v1, v2):
                    exp_st[y][x] = True

            def print_state(name, s):
                print(f"{name}:")
                print("  | 0 1 2 3 4 5 6 7 8 9")
                print("----------------------")
                for i, row in enumerate(s):
                    print(i, "|", end="")
                    for p in row:
                        print("# " if p else "  ", end="")
                    print()

            for _ in range(5):
                yield

            yield dut.points.ready.eq(1)
            while True:
                yield from wait_until(dut.points.valid | dut.idle)
                if (yield dut.idle):
                    if submit_done:
                        break
                    continue
                x, y = (yield dut.points.payload.p.x), (yield dut.points.payload.p.y)
                st[y][x] = True

            for row1, row2 in zip(st, exp_st):
                for val1, val2 in zip(row1, row2):
                    if val1 != val2:
                        print_state("Expected state", exp_st)
                        print_state("Actual state", st)
                        raise Exception("Outputs differ between model and gateware")

            if sum(sum(r) for r in exp_st) != count:
                print_state("Result", exp_st)
                raise Exception("Wrong number of points in the output")

        sim = Simulator(dut)
        sim.add_sync_process(trig_feed)
        sim.add_sync_process(point_read)
        sim.add_clock(1e-6)
        sim.run()

    @staticmethod
    def _test_interpolation(triangles, recip: bool):
        dut = EdgeWalker(recip)

        submit_done = False

        def trig_feed():
            nonlocal submit_done
            yield Passive()
            for v0, v1, v2 in triangles:
                yield from submit_triangle(dut.triangle, v0, v1, v2)
            submit_done = True

        def point_read():
            st = {}
            exp_st = {}

            for v0, v1, v2 in triangles:
                for x, y, w0, w1, w2 in (points_recip if recip else points)(v0, v1, v2):
                    exp_st[(x, y)] = (w0, w1, w2)

            for _ in range(5):
                yield

            yield dut.points.ready.eq(1)
            while True:
                yield from wait_until(dut.points.valid | dut.idle)
                if (yield dut.idle):
                    if submit_done:
                        break
                    continue
                x, y = (yield dut.points.payload.p.x), (yield dut.points.payload.p.y)
                w0, w1, w2 = (yield dut.points.payload.w0), (yield dut.points.payload.w1), (yield dut.points.payload.w2)
                st[(x, y)] = (w0, w1, w2)

            for k, exp_v in exp_st.items():
                v = st[k]
                assert exp_v == v, f"[{k}]: expected {exp_v}, got {v}"

        sim = Simulator(dut)
        sim.add_sync_process(trig_feed)
        sim.add_sync_process(point_read)
        sim.add_clock(1e-6)
        sim.run()

    @staticmethod
    def _test_raw_interpolation_factors(v0, v1, v2):
        EdgeWalkerTest._test_interpolation([(v0, v1, v2)], False)

    @staticmethod
    def _test_interpolation_factors(v0, v1, v2):
        EdgeWalkerTest._test_interpolation([(v0, v1, v2)], True)

    def test_empty(self):
        self._test_coordinates([((2, 2), (2, 2), (2, 2))], 0)

    def test_triangle(self):
        self._test_coordinates([((2, 2), (8, 2), (2, 8))], 7 + 6 + 5 + 4 + 3 + 2 + 1)

    def test_wrong_vertex_order(self):
        self._test_coordinates([((2, 2), (2, 8), (8, 2))], 0)

    def test_raw_interp(self):
        self._test_raw_interpolation_factors((2, 2), (8, 2), (2, 8))

    def test_interp(self):
        self._test_interpolation_factors((2, 2), (8, 2), (2, 8))

    def test_square(self):
        self._test_coordinates([((2, 2), (8, 2), (2, 8)), ((8, 2), (8, 8), (2, 8))], 7 * 7)

    def test_square_raw_interp(self):
        self._test_interpolation([((2, 2), (8, 2), (2, 8)), ((8, 2), (8, 8), (2, 8))], False)

    def test_square_interp(self):
        self._test_interpolation([((2, 2), (8, 2), (2, 8)), ((8, 2), (8, 8), (2, 8))], True)
