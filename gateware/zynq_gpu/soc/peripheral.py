from amaranth import *
from amaranth import tracer
from amaranth_soc import csr
from amaranth_soc.csr import wishbone


__all__ = ["Peripheral"]


class Peripheral(Elaboratable):
    def __init__(self, name=None, src_loc_at=1):
        if name is not None and not isinstance(name, str):
            raise TypeError("Name must be a string, not {!r}".format(name))
        self._name = name or tracer.get_var_name(depth=2 + src_loc_at).lstrip("_")

        self._csrs = []
        self._irqs = []

    def csr(self, width, access, *, name=None, src_loc_at=0):
        if name is not None and not isinstance(name, str):
            raise TypeError("Name must be a string, not {!r}".format(name))
        name = name or tracer.get_var_name(depth=2 + src_loc_at).lstrip("_")
        reg = csr.Element(width, access, path=(self._name, name))
        self._csrs.append((reg, name))
        return reg

    def irq(self, *, name=None, src_loc_at=0):
        if name is not None and not isinstance(name, str):
            raise TypeError("Name must be a string, not {!r}".format(name))
        name = name or tracer.get_var_name(depth=2 + src_loc_at).lstrip("_")
        sig = Signal(name=f"{name}_trigger")
        self._irqs.append(sig)
        return sig

    def bridge(self):
        return Bridge(self._csrs, self._irqs, self._name)


class Bridge(Elaboratable):
    def __init__(self, registers, irqs, base_name):
        self._csr_mux = csr_mux = csr.Multiplexer(addr_width=1, data_width=8)

        self._int = None
        if len(irqs) > 0:
            self._int = InterruptSource(irqs, base_name)
            self.irq = Signal()
            self._csr_mux.add(self._int.status, name=f"{base_name}_irq_status", extend=True, alignment=2)
            self._csr_mux.add(self._int.mask, name=f"{base_name}_irq_mask", extend=True, alignment=2)

        for register, name in registers:
            csr_mux.add(register, name=f"{base_name}_{name}", extend=True, alignment=2)

        self._csr_wb = wishbone.WishboneCSRBridge(csr_mux.bus, data_width=32)

        self.bus = self._csr_wb.wb_bus

    def elaborate(self, platform):
        m = Module()

        m.submodules.csr_mux = self._csr_mux
        m.submodules.wb_bridge = self._csr_wb
        if self._int is not None:
            m.submodules.int = self._int
            m.d.comb += self.irq.eq(self._int.irq)

        return m


class InterruptSource(Elaboratable):
    def __init__(self, events, path):
        self.irq = Signal()

        w = len(events)
        self.status = csr.Element(w, "rw", path=(path, "irq_status"))
        self.mask = csr.Element(w, "rw", path=(path, "irq_mask"))

        self._ev = events

    def elaborate(self, platform):
        m = Module()

        irq_status = Signal(len(self._ev))
        irq_mask = Signal(len(self._ev))

        m.d.comb += self.irq.eq((irq_status & irq_mask).any())

        m.d.comb += self.mask.r_data.eq(irq_mask)
        with m.If(self.mask.w_stb):
            m.d.sync += irq_mask.eq(self.mask.w_data)

        m.d.comb += self.status.r_data.eq(irq_status)
        irq_set = Cat(self._ev)
        irq_clr = Signal(len(irq_status))
        with m.If(self.status.w_stb):
            m.d.comb += irq_clr.eq(self.status.w_data)

        m.d.sync += irq_status.eq((irq_status & ~irq_clr) | irq_set)

        return m
