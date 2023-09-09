from amaranth import *
from amaranth.lib.wiring import Signature, In, Out


class TMDSSerializer(Elaboratable):
    signature = Signature({
        "data": In(10),
        "o": Out(1),
    })

    def __init__(self, pix_domain: str = "pix", pix_5x_domain: str = "pix_5x"):
        self._pix_domain = pix_domain
        self._pix_5x_domain = pix_5x_domain

        self.data = Signal(10)
        self.o = Signal()

    def elaborate(self, platform):
        m = Module()

        ce = Signal()
        # m.d.comb += ce.eq(~ResetSignal(self._pix_domain))
        m.d.comb += ce.eq(1)
        shift = Signal(2)

        m.submodules.serializer1 = Instance(
            "OSERDESE2",
            p_DATA_WIDTH=10,
            p_TRISTATE_WIDTH=1,
            p_DATA_RATE_OQ="DDR",
            p_DATA_RATE_TQ="SDR",
            p_SERDES_MODE="MASTER",
            p_TBYTE_CTL="FALSE",
            p_TBYTE_SRC="FALSE",

            o_OQ=self.o,
            i_OCE=ce,
            i_TCE=0,
            i_RST=ResetSignal(self._pix_domain),
            i_CLK=ClockSignal(self._pix_5x_domain),
            i_CLKDIV=ClockSignal(self._pix_domain),

            i_D1=self.data[0],
            i_D2=self.data[1],
            i_D3=self.data[2],
            i_D4=self.data[3],

            i_D5=self.data[4],
            i_D6=self.data[5],
            i_D7=self.data[6],
            i_D8=self.data[7],

            i_SHIFTIN1=shift[0],
            i_SHIFTIN2=shift[1],
        )
        m.submodules.serializer2 = Instance(
            "OSERDESE2",
            p_DATA_WIDTH=10,
            p_TRISTATE_WIDTH=1,
            p_DATA_RATE_OQ="DDR",
            p_DATA_RATE_TQ="SDR",
            p_SERDES_MODE="SLAVE",
            p_TBYTE_CTL="FALSE",
            p_TBYTE_SRC="FALSE",

            i_OCE=ce,
            i_TCE=0,
            i_RST=ResetSignal(self._pix_domain),
            i_CLK=ClockSignal(self._pix_5x_domain),
            i_CLKDIV=ClockSignal(self._pix_domain),

            i_D1=0,
            i_D2=0,
            i_D3=self.data[8],
            i_D4=self.data[9],

            i_D5=0,
            i_D6=0,
            i_D7=0,
            i_D8=0,

            i_SHIFTIN1=0,
            i_SHIFTIN2=0,
            o_SHIFTOUT1=shift[0],
            o_SHIFTOUT2=shift[1],
        )

        return m
