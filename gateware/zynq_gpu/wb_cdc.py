from amaranth import *
from amaranth.lib import data
from amaranth.lib.fifo import AsyncFIFO
from amaranth_soc import wishbone


__all__ = ["WishboneCDC"]


class WishboneCDC(Elaboratable):
    def __init__(self, addr_width: int, data_width: int = 32, granularity: int = 8):
        self._req = data.StructLayout({
            "adr": unsigned(addr_width),
            "dat_w": unsigned(data_width),
            "we": unsigned(1),
            "sel": unsigned(data_width // granularity),
        })

        self._res = data.StructLayout({
            "dat_r": unsigned(data_width),
            "err": unsigned(1),
        })

        self.i_bus = wishbone.Interface(
            addr_width=addr_width,
            data_width=data_width,
            granularity=granularity,
            features={"err"},
        )
        self.t_bus = wishbone.Interface(
            addr_width=addr_width,
            data_width=data_width,
            granularity=granularity,
            features={"err"},
        )

    def elaborate(self, platform):
        m = Module()

        m.submodules.req_fifo = req_fifo = AsyncFIFO(width=self._req.size, depth=2,
                                                     r_domain="target", w_domain="initiator")
        m.submodules.res_fifo = res_fifo = AsyncFIFO(width=self._res.size, depth=2,
                                                     r_domain="initiator", w_domain="target")

        m.d.comb += res_fifo.r_en.eq(1)

        i_req = data.View(self._req, Signal(self._req))
        i_res = data.View(self._res, Signal(self._res))

        m.d.comb += [
            i_req.adr.eq(self.i_bus.adr),
            i_req.we.eq(self.i_bus.we),
            i_req.dat_w.eq(self.i_bus.dat_w),
            i_req.sel.eq(self.i_bus.sel),
            req_fifo.w_data.eq(i_req),

            i_res.eq(res_fifo.r_data),
            self.i_bus.dat_r.eq(i_res.dat_r),
            self.i_bus.err.eq(res_fifo.r_rdy & i_res.err),
            self.i_bus.ack.eq(res_fifo.r_rdy & ~i_res.err)
        ]

        m_request_in_flight = Signal()
        with m.If(self.i_bus.cyc & self.i_bus.stb):
            m.d.comb += req_fifo.w_en.eq(~m_request_in_flight)
            with m.If(req_fifo.w_en & req_fifo.w_rdy):
                m.d.initiator += m_request_in_flight.eq(1)
            with m.If(res_fifo.r_rdy):
                m.d.initiator += m_request_in_flight.eq(0)

        t_req = data.View(self._req, Signal(self._req))
        t_res = data.View(self._res, Signal(self._res))

        m.d.comb += [
            self.t_bus.adr.eq(t_req.adr),
            self.t_bus.we.eq(t_req.we),
            self.t_bus.dat_w.eq(t_req.dat_w),
            self.t_bus.sel.eq(t_req.sel),
            t_req.eq(req_fifo.r_data),

            t_res.dat_r.eq(self.t_bus.dat_r),
            t_res.err.eq(self.t_bus.err),
            res_fifo.w_data.eq(t_res),
        ]

        with m.If(req_fifo.r_rdy & res_fifo.w_rdy):
            m.d.comb += [
                self.t_bus.stb.eq(1),
                self.t_bus.cyc.eq(1),
            ]
            with m.If(self.t_bus.ack | self.t_bus.err):
                m.d.comb += [
                    req_fifo.r_en.eq(1),
                    res_fifo.w_en.eq(1),
                ]

        return m