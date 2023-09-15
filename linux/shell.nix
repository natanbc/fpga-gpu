{ pkgs ? import (builtins.fetchTarball {
  name = "nixos-23.05-2023-09-10";
  url = "https://github.com/nixos/nixpkgs/archive/4c8cf44c5b9481a4f093f1df3b8b7ba997a7c760.tar.gz";
  # Hash obtained using `nix-prefetch-url --unpack <url>`
  sha256 = "1fq4k8b4jxzwsydwg0sf4yg00qfdqaf1fgv3ya9l5923hwv2klp6";
}) {} }:

let
    rustVersion = "nightly-2023-09-10";
    llvm = pkgs.llvmPackages_15;
in
pkgs.mkShell {
    nativeBuildInputs = with pkgs; [
        bc
        bison
        cacert
        dtc
        flex
        gnumake
        llvm.clang-unwrapped
        llvm.bintools-unwrapped
        ncurses
        rustup
        ubootTools
    ];

    shellHook = ''
        rustup toolchain install ${rustVersion} --component cargo --component rustc --component rust-src
        export TOOLCHAIN=${rustVersion}

        build_kernel() {
            # Usage: build_kernel [--config <kernel config>] [make args]
            cfg_arg=""
            if [ "$1" = "--config" ]; then
                cfg_arg="KCONFIG_CONFIG=$2"
                shift 2
            fi
            make ARCH=arm "$cfg_arg"                                        \
                CC="clang -target arm-linux-gnueabihf" LD=ld.lld AR=llvm-ar \
                NM=llvm-nm OBJCOPY=llvm-objcopy OBJDUMP=llvm-objdump        \
                READELF=llvm-readelf STRIP=llvm-strip -j$(nproc) "$@"       \
                && \
                mkimage -A arm -O linux -T kernel -C none -a 008000         \
                -e 008000 -n Linux -d arch/arm/boot/zImage kernel.img
        }

        build_module() {
            # Usage: build_module [--config <kernel config>] [--module <module path>] --kernel <kernel src> [make args]
            cfg_arg=""
            module="$(pwd)"
            kernel=""
            while true; do
                case "$1" in
                    --config)
                        cfg_arg="KCONFIG_CONFIG=$2"
                        shift 2
                        ;;
                    --module)
                        module="$2"
                        shift 2
                        ;;
                    --kernel)
                        kernel="$2"
                        shift 2
                        ;;
                    *)
                        break
                        ;;
                esac
            done
            if [ ! -d "$kernel" ]; then
                echo "Kernel source tree not specified"
                return 1
            fi
            make ARCH=arm "$cfg_arg"                                        \
                CC="clang -target arm-linux-gnueabihf" LD=ld.lld AR=llvm-ar \
                NM=llvm-nm OBJCOPY=llvm-objcopy OBJDUMP=llvm-objdump        \
                READELF=llvm-readelf STRIP=llvm-strip -j$(nproc)            \
                -C "$kernel" M="$module" "$@"
        }
    '';
}
