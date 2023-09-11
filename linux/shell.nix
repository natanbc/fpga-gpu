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
        cacert
        dtc
        rustup
    ];

    shellHook = ''
        rustup toolchain install ${rustVersion} --component cargo --component rustc --component rust-src
        export TOOLCHAIN=${rustVersion}
    '';
}
