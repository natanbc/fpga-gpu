from amaranth.lib.wiring import In, Out, Signature
from typing import Optional


__all__ = ["SAxiGP", "MAxiGP", "SAxiHP", "SAxiACP", "EMIOEnet"]


_overrides = {
    "read_address": {
        "count": "RACOUNT",
    },
    "write_address": {
        "count": "WACOUNT",
    },
    "read": {
        "issuecap1en": "RDISSUECAP1EN"
    },
    "write_data": {
        "issuecap1en": "WRISSUECAP1EN",
    },
}


def _map_axi_names(port: str, iface) -> dict[str, object]:
    assigns = {
        f"o_{port}ARESETN": iface.reset_n,
        f"i_{port}ACLK": iface.aclk,
    }
    name_mapping = {
        "read_address": "AR",
        "read": "R",
        "write_address": "AW",
        "write_data": "W",
        "write_response": "B",
    }
    for chan, prefix in name_mapping.items():
        c = getattr(iface, chan)
        signature: Signature = getattr(c, "signature")
        members = signature.members
        overrides = _overrides.get(chan) or {}
        for signal_name in members:
            flow = members[signal_name].flow
            # Flipped, "in" from the component side is an output on the PS7 instance and vice versa
            if flow == In:
                direction = "o"
            elif flow == Out:
                direction = "i"
            else:
                assert False
            name = f"{prefix}{signal_name.upper()}"
            if overrides.get(signal_name) is not None:
                name = overrides.get(signal_name)
            assigns[f"{direction}_{port}{name}"] = getattr(c, signal_name)

    return assigns


def _axi_sig(is_m: bool, data_width: int, id_width: int, extras: Optional[dict[str, dict[str, int]]] = None):
    sig_ar = Signature({
        "ready": In(1),
        "valid": Out(1),
        "burst": Out(2),
        "lock": Out(2),
        "size": Out(2),
        "prot": Out(3),
        "addr": Out(32),
        "cache": Out(4),
        "len": Out(4),
        "qos": Out(4),
        "id": Out(id_width),
    })

    sig_r = Signature({
        "last": In(1),
        "valid": In(1),
        "resp": In(2),
        "data": In(data_width),
        "id": In(id_width),
        "ready": Out(1),
    })

    sig_aw = Signature({
        "ready": In(1),
        "valid": Out(1),
        "burst": Out(2),
        "lock": Out(2),
        "size": Out(2),
        "prot": Out(3),
        "addr": Out(32),
        "cache": Out(4),
        "len": Out(4),
        "qos": Out(4),
        "id": Out(id_width),
    })

    sig_w = Signature({
        "ready": In(1),
        "last": Out(1),
        "valid": Out(1),
        "data": Out(data_width),
        "strb": Out(data_width // 8),
        "id": Out(id_width),
    })

    sig_b = Signature({
        "valid": In(1),
        "resp": In(2),
        "id": In(id_width),
        "ready": Out(1),
    })

    if extras:
        channels = {
            "ar": sig_ar,
            "r": sig_r,
            "aw": sig_aw,
            "w": sig_w,
            "b": sig_b,
        }
        for chan, sigs in extras.items():
            m = channels[chan].members
            for name, sig in sigs.items():
                m[name] = sig

    def direction(s: Signature) -> Signature:
        return Out(s) if is_m else In(s)

    return Signature({
        "reset_n": In(1),
        "aclk": Out(1),
        "read_address": direction(sig_ar),
        "read": direction(sig_r),
        "write_address": direction(sig_aw),
        "write_data": direction(sig_w),
        "write_response": direction(sig_b),
    })


SAxiGP = _axi_sig(True, 32, 6)


MAxiGP = _axi_sig(False, 32, 12)


SAxiHP = _axi_sig(True, 64, 6, {
    "ar": {
        "count": In(3),  # "RACOUNT"
    },
    "aw": {
        "count": In(6),  # "WACOUNT"
    },
    "r": {
        "count": In(8),
        "issuecap1en": Out(1),  # "RDISSUECAP1EN"
    },
    "w": {
        "count": In(8),
        "issuecap1en": Out(1),  # "WRISSUECAP1EN"
    },
})


SAxiACP = _axi_sig(True, 64, 3, {
    "ar": {
        "user": Out(5),
    },
    "aw": {
        "user": Out(5),
    },
})


EMIOEnet = Signature({
    "gmii_tx_en": In(1),
    "gmii_tx_er": In(1),
    "mdio_mdc": In(1),
    "mdio_o": In(1),
    "mdio_oe": In(1),
    # TODO?: PTP signals
    "gmii_txd": In(8),
    "ext_int_in": Out(1),
    "gmii_col": Out(1),
    "gmii_crs": Out(1),
    "gmii_rx_clk": Out(1),
    "gmii_rx_dv": Out(1),
    "gmii_rx_er": Out(1),
    "gmii_tx_clk": Out(1),
    "mdio_i": Out(1),
    "gmii_rxd": Out(8),
})


def _ps7_assigns(interface, port_type: str, idx: int) -> dict:
    if not isinstance(port_type, str) or port_type.lower() not in ["saxigp", "maxigp", "saxihp", "saxiacp"]:
        raise ValueError("Port type must be one of SAxiGP, MAxiGP, SAxiHP or SAxiACP")
    count, sig = {
        "saxigp": (2, SAxiGP),
        "maxigp": (2, MAxiGP),
        "saxihp": (4, SAxiHP),
        "saxiacp": (1, SAxiACP),
    }[port_type.lower()]
    if not isinstance(idx, int) or idx < 0 or idx >= count:
        raise ValueError(f"Port index must be in range [0, {count})")
    idx_str = str(idx) if port_type.lower() != "saxiacp" else ""
    reasons = []
    if not sig.is_compliant(interface, reasons=reasons):
        reasons_as_string = "".join("\n- " + reason for reason in reasons)
        raise ValueError(f"{interface!r} does not match the port signature:" +
                         reasons_as_string)
    return _map_axi_names(f"{port_type.upper()}{idx_str}", interface)
