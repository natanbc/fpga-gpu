from amaranth import *
from typing import Optional, Tuple, TypeVar
from .zynq_ifaces import *
# noinspection PyProtectedMember
from .zynq_ifaces import _ps7_assigns as ps7_assigns


__all__ = ["PS7"]


T = TypeVar("T")


def get_or_insert(d: dict[str, T], k: str, default: T) -> T:
    if k in d:
        return d[k]
    d[k] = default
    return default


def assign_dict(prefix: str, n: int, **kwargs: dict[str, Signal]) -> dict[str, Signal]:
    res = {}
    for k, v in kwargs.items():
        if k.startswith("i_"):
            res[f"i_{prefix}{n}{k[2:]}"] = v
        elif k.startswith("o_"):
            res[f"o_{prefix}{n}{k[2:]}"] = v
        else:
            raise ValueError("Keys must start with i_/o_")
    return res


class PS7(Elaboratable):
    def __init__(self):
        self._next = {}
        self._used = {}

        self._assigns = {}
        self._clocks = {}
        self._irqs_f2p = {}

    def _port_number(self, key: str, limit: int, n: Optional[int]):
        increase = False
        if n is None:
            n = get_or_insert(self._next, key, 0)
            increase = True
        if n < 0 or n >= limit:
            raise ValueError(f"Port number {n} out of bounds")
        used = get_or_insert(self._used, key, set())
        if n in used:
            raise ValueError(f"Port {n} already used")
        used.add(n)
        if increase:
            self._next[key] = n + 1
        return n

    def axi_acp(self):
        if "axi_acp" in self._used:
            raise ValueError("ACP port already used")
        self._used["axi_acp"] = True

        port = SAxiACP.create()
        self._assigns.update(ps7_assigns(port, "saxiacp", 0))
        return port

    def axi_gp_m(self, n: Optional[int] = None):
        n = self._port_number("axi_gp_m", 2, n)
        port = MAxiGP.create()
        self._assigns.update(ps7_assigns(port, "maxigp", n))
        return port

    def axi_gp_s(self, n: Optional[int] = None):
        n = self._port_number("axi_gp_s", 2, n)
        port = SAxiGP.create()
        self._assigns.update(ps7_assigns(port, "saxigp", n))
        return port

    def axi_hp(self, n: Optional[int] = None):
        n = self._port_number("axi_hp", 4, n)
        port = SAxiHP.create()
        self._assigns.update(ps7_assigns(port, "saxihp", n))
        return port

    def fclk(self, n: int, freq: float) -> Tuple[Signal, Signal]:
        if n < 0 or n > 3:
            raise ValueError("FCLK must be in range 0-3")
        clk = Signal()
        rst = Signal()
        self._clocks[n] = clk, rst, freq
        return clk, rst

    def emio_enet(self, n: Optional[int]):
        sigs = EMIOEnet.create()
        n = self._port_number("emio_enet", 2, n)
        self._assigns.update(assign_dict(
            "EMIOENET", n,
            o_GMIITXEN=sigs.gmii_tx_en,
            o_GMIITXER=sigs.gmii_tx_er,
            o_MDIOMDC=sigs.mdio_mdc,
            o_MDIOO=sigs.mdio_o,
            o_MDIOTN=sigs.mdio_oe,
            o_GMIITXD=sigs.gmii_txd,
            i_EXTINTIN=sigs.ext_int_in,
            i_GMIICOL=sigs.gmii_col,
            i_GMIICRS=sigs.gmii_crs,
            i_GMIIRXCLK=sigs.gmii_rx_clk,
            i_GMIIRXDV=sigs.gmii_rx_dv,
            i_GMIIRXER=sigs.gmii_rx_er,
            i_GMIITXCLK=sigs.gmii_tx_clk,
            i_MDIOI=sigs.mdio_i,
            i_GMIIRXD=sigs.gmii_rxd,
        ))
        return sigs

    def irq_f2p(self, n: int):
        self._port_number("irq_f2p", 20, n)
        irq = Signal(1, name=f"irq_f2p_{n}")
        self._irqs_f2p[n] = irq
        return irq

    def elaborate(self, platform):
        m = Module()

        fclk = [Signal(1, name=f"fclk_{i}") for i in range(4)]
        freset = Signal(4)

        irq_f2p = Signal(20)
        for i, sig in self._irqs_f2p.items():
            m.d.comb += irq_f2p[i].eq(sig)

        m.submodules.ps7 = Instance(
            "PS7",
            o_FCLKCLK=Cat(fclk),
            o_FCLKRESETN=freset,
            i_IRQF2P=irq_f2p,
            **self._assigns,
        )

        for n, (clk, rst, freq) in self._clocks.items():
            m.submodules[f"clkbuf_{n}"] = Instance(
                "BUFG",
                i_I=fclk[n],
                o_O=clk,
            )
            m.d.comb += rst.eq(~freset[n])
            platform.add_clock_constraint(fclk[n], freq)

        return m
