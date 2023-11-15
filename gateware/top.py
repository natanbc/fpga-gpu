from amaranth import *
from amaranth.lib import wiring
from amaranth_soc import wishbone

from board import ebaz4205
from zynq_gpu import axi_to_wishbone
from zynq_gpu.hdmi.tx import HDMITx
from zynq_gpu.ps7 import PS7
from zynq_gpu.soc import Framebuffer, Raster
from zynq_gpu.wb_cdc import WishboneCDC
from zynq_gpu.zynq_ifaces import MAxiGP


# Clock settings for 1080p30 + 159.5MHz (2 * pixel clock) rasterizer
divclk_divide, clkfbout_mult_f, clkout0_divide_f, clkout1_divide, clkout2_divide, \
    mmcm_in_domain, clkin1_period = \
    (5, 39.875, 10.0, 2, 5, "clk100", 10.0)


class Peripherals(Elaboratable):
    def __init__(self):
        self.axi = MAxiGP.create()

        self.video = Framebuffer()
        self.rasterizer = Raster(1920)

    def elaborate(self, platform):
        m = Module()

        m.submodules.video = video = self.video
        m.submodules.rasterizer = rasterizer = DomainRenamer("raster")(self.rasterizer)

        m.submodules.axi2wb = axi2wb = axi_to_wishbone.Axi2Wishbone()
        wiring.connect(m, axi2wb.axi, wiring.flipped(self.axi))
        m.submodules.decoder = decoder = wishbone.Decoder(
            addr_width=30,
            data_width=32,
            granularity=8,
            features={"err"},
        )

        m.submodules.cdc_raster = cdc_raster = DomainRenamer({
            "initiator": "sync",
            "target": "raster",
        })(WishboneCDC(rasterizer.bus.memory_map.addr_width - 2))
        wiring.connect(m, cdc_raster.t_bus, wiring.flipped(rasterizer.bus))
        cdc_raster.i_bus.memory_map = rasterizer.bus.memory_map

        decoder.add(video.bus, addr=0x4000_0000)
        decoder.add(cdc_raster.i_bus, addr=0x4000_1000)

        wiring.connect(m, axi2wb.wishbone, wiring.flipped(decoder.bus))

        for resource in decoder.bus.memory_map.all_resources():
            name = "_".join([i for i in resource.name])
            address = resource.start
            print(f"{name} @ 0x{address:08x}-0x{resource.end:08x}")

        return m


class ClockGen(Elaboratable):
    def elaborate(self, platform):
        m = Module()

        pix = Signal()
        pix_5x = Signal()
        raster = Signal()
        locked_int = Signal()

        def add_clock(signal, name):
            m.domains += ClockDomain(name)
            seq_reg = Signal(8, name="seq_reg_" + name, reset_less=True)
            m.domains += ClockDomain(name + "_waitlock")

            m.submodules[f"bufgce_{name}"] = Instance(
                "BUFGCE",
                i_I=signal,
                i_CE=seq_reg[-1],
                o_O=ClockSignal(name),
            )
            m.submodules[f"bufh_{name}"] = Instance(
                "BUFH",
                i_I=signal,
                o_O=ClockSignal(name + "_waitlock"),
            )
            # platform.add_clock_constraint(signal, freq)
            m.d.comb += ResetSignal(name).eq(ResetSignal(mmcm_in_domain) | ~seq_reg[-1])
            m.d.comb += ResetSignal(name + "_waitlock").eq(ResetSignal(mmcm_in_domain))

            m.d[name + "_waitlock"] += [
                seq_reg.eq(Cat(locked_int, seq_reg)),
            ]

        mmcm_clkin1 = Signal()
        mmcm_clkfbout = Signal()
        mmcm_clkfbout_buf = Signal()

        m.d.comb += mmcm_clkin1.eq(ClockSignal(mmcm_in_domain))

        m.submodules.mmcm_bufg = Instance(
            "BUFG",
            # "BUFH",
            i_I=mmcm_clkfbout,
            o_O=mmcm_clkfbout_buf,
        )

        startup_rst = Signal()
        # Half a second
        startup_wait_cycles = int(500_000_000 / clkin1_period)
        startup_ctr = Signal(range(startup_wait_cycles + 1), reset=startup_wait_cycles)
        m.d.comb += startup_rst.eq(startup_ctr != 0)
        with m.If(startup_ctr != 0):
            m.d[mmcm_in_domain] += startup_ctr.eq(startup_ctr - 1)

        m.submodules.mmcm = Instance(
            "MMCME2_ADV",
            p_BANDWIDTH="OPTIMIZED",
            p_CLKOUT4_CASCADE="FALSE",
            p_COMPENSATION="ZHOLD",
            p_STARTUP_WAIT="FALSE",
            p_DIVCLK_DIVIDE=divclk_divide,
            p_CLKFBOUT_MULT_F=clkfbout_mult_f,
            p_CLKFBOUT_PHASE=0.0,
            p_CLKFBOUT_USE_FINE_PS="FALSE",

            p_CLKOUT0_DIVIDE_F=clkout0_divide_f,
            p_CLKOUT0_PHASE=0.0,
            p_CLKOUT0_DUTY_CYCLE=0.5,
            p_CLKOUT0_USE_FINE_PS="FALSE",

            p_CLKOUT1_DIVIDE=clkout1_divide,
            p_CLKOUT1_PHASE=0.0,
            p_CLKOUT1_DUTY_CYCLE=0.5,
            p_CLKOUT1_USE_FINE_PS="FALSE",

            p_CLKOUT2_DIVIDE=clkout2_divide,
            p_CLKOUT2_PHASE=0.0,
            p_CLKOUT2_DUTY_CYCLE=0.5,
            p_CLKOUT2_USE_FINE_PS="FALSE",

            p_CLKIN1_PERIOD=clkin1_period,

            o_CLKFBOUT=mmcm_clkfbout,
            o_CLKOUT0=pix,
            o_CLKOUT1=pix_5x,
            o_CLKOUT2=raster,

            i_CLKFBIN=mmcm_clkfbout_buf,
            i_CLKIN1=mmcm_clkin1,
            i_CLKIN2=Signal(),

            i_CLKINSEL=Signal(name="clkin_sel", reset=1),

            o_LOCKED=locked_int,

            i_DWE=Signal(),
            i_PSEN=Signal(),

            i_RST=ResetSignal(mmcm_in_domain) | startup_rst,
        )

        add_clock(pix, "pix")
        add_clock(pix_5x, "pix_5x")
        add_clock(raster, "raster")

        return m


class Top(Elaboratable):
    def elaborate(self, platform):
        m = Module()

        m.submodules.ps7 = ps7 = PS7()
        m.submodules.clock_gen = ClockGen()
        m.submodules.hdmi_tx = hdmi_tx = HDMITx()

        m.domains += ClockDomain("clk100")
        clk, rst = ps7.fclk(0, 100e6)
        m.d.comb += [
            ClockSignal("clk100").eq(clk),
            ResetSignal("clk100").eq(rst),
        ]

        peripherals = Peripherals()
        m.submodules.peripherals = DomainRenamer("pix")(peripherals)
        wiring.connect(m, peripherals.axi, ps7.axi_gp_m(0))

        wiring.connect(m, peripherals.video.axi, ps7.axi_hp(0))

        wiring.connect(m, peripherals.rasterizer.axi1, ps7.axi_hp(1))
        wiring.connect(m, peripherals.rasterizer.axi2, ps7.axi_hp(3))
        wiring.connect(m, peripherals.rasterizer.axi_cmd, ps7.axi_gp_s(0))

        hdmi_port = platform.request("hdmi_tx", 0)
        m.d.comb += [
            hdmi_tx.data_enable.eq(peripherals.video.data_enable),
            hdmi_tx.hsync.eq(peripherals.video.hsync),
            hdmi_tx.vsync.eq(peripherals.video.vsync),
            hdmi_tx.r.eq(peripherals.video.r),
            hdmi_tx.g.eq(peripherals.video.g),
            hdmi_tx.b.eq(peripherals.video.b),

            hdmi_port.clk.o.eq(hdmi_tx.tmds_clk),
            hdmi_port.d.o.eq(Cat(hdmi_tx.tmds_d0, hdmi_tx.tmds_d1, hdmi_tx.tmds_d2)),
        ]

        m.d.comb += [
            ps7.irq_f2p(0).eq(peripherals.video.irq),
            ps7.irq_f2p(2).eq(peripherals.rasterizer.irq),
        ]

        enet_pins = platform.request("enet", 0)
        enet = ps7.emio_enet(0)
        m.d.comb += [
            enet_pins.tx_data.o.eq(enet.gmii_txd),
            enet.gmii_rxd.eq(enet_pins.rx_data.i),
            enet_pins.tx_en.o.eq(enet.gmii_tx_en),
            enet.gmii_rx_dv.eq(enet_pins.rx_dv.i),
            enet.gmii_tx_clk.eq(enet_pins.tx_clk.i),
            enet.gmii_rx_clk.eq(enet_pins.rx_clk.i),
            enet_pins.mdc.o.eq(enet.mdio_mdc),
            enet_pins.mdio.o.eq(enet.mdio_o),
            enet.mdio_i.eq(enet_pins.mdio.i),
            enet_pins.mdio.oe.eq(enet.mdio_oe),
        ]

        return m


plat = ebaz4205.EBAZ4205Platform().with_extension_board(True)
plat.build(Top(), do_build=True, do_program=True, add_constraints="""
set_property BITSTREAM.GENERAL.COMPRESS true [current_design]
# set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets {pin_enet_0__tx_clk/enet_0__tx_clk__i}]
set_property CLOCK_DEDICATED_ROUTE FALSE [get_nets {pin_enet_0__tx_clk/pin_enet_0__tx_clk_enet_0__tx_clk__i}]

set_false_path -to [get_cells csrs/cdc_raster/req_fifo/_0__reg[*]]
set_false_path -to [get_cells csrs/cdc_raster/res_fifo/_0__reg[*]]
""", script_after_read="""
set_param general.maxThreads 12
auto_detect_xpm
""")
