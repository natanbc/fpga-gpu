from amaranth import *
from amaranth.lib.wiring import Component, Signature, In, Out
from .phy import TMDSSerializer
from .tmds_encoder import TMDSEncoder


__all__ = ["HDMITx"]


class HDMITx(Component):
    r: In(8)
    g: In(8)
    b: In(8)
    data_enable: In(1)
    hsync: In(1)
    vsync: In(1)

    tmds_clk: Out(1)
    tmds_d0: Out(1)
    tmds_d1: Out(1)
    tmds_d2: Out(1)

    def elaborate(self, platform):
        m = Module()

        renamer = DomainRenamer("pix")
        m.submodules.encoder_r = encoder_r = renamer(TMDSEncoder())
        m.submodules.encoder_g = encoder_g = renamer(TMDSEncoder())
        m.submodules.encoder_b = encoder_b = renamer(TMDSEncoder())

        control = Signal(2)

        m.d.comb += [
            control[0].eq(self.hsync),
            control[1].eq(self.vsync),

            encoder_r.data.eq(self.r),
            encoder_g.data.eq(self.g),
            encoder_b.data.eq(self.b),

            encoder_r.data_enable.eq(self.data_enable),
            encoder_g.data_enable.eq(self.data_enable),
            encoder_b.data_enable.eq(self.data_enable),

            encoder_b.control.eq(control),
        ]

        m.submodules.serializer_clk = serializer_clk = TMDSSerializer()
        m.submodules.serializer_r = serializer_r = TMDSSerializer()
        m.submodules.serializer_g = serializer_g = TMDSSerializer()
        m.submodules.serializer_b = serializer_b = TMDSSerializer()

        m.d.comb += [
            # serializer_clk.data.eq(0b1111100000),
            serializer_clk.data.eq(0b0000011111),
            serializer_r.data.eq(encoder_r.output),
            serializer_g.data.eq(encoder_g.output),
            serializer_b.data.eq(encoder_b.output),
        ]

        m.d.comb += [
            self.tmds_clk.eq(serializer_clk.o),
            self.tmds_d0.eq(serializer_b.o),
            self.tmds_d1.eq(serializer_g.o),
            self.tmds_d2.eq(serializer_r.o),
        ]

        return m
