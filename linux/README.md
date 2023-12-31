# Setup

Follow the steps [here](https://github.com/xjtuecho/EBAZ4205#reset-the-root-password-of-built-in-linux)
to change the root password.

1) Copy the command line from the device
```
root@zedboard-zynq7:~# cat /proc/cmdline
console=ttyPS0,115200 root=/dev/mtdblock6 rootfstype=jffs2 noinitrd rw rootwait
```
2) Update `gpu_device_tree.dts` to use that command line, with `uio_pdrv_genirq.of_id=generic-uio` appended:
```
chosen {
    bootargs = "console=ttyPS0,115200 root=/dev/mtdblock6 rootfstype=jffs2 noinitrd rw rootwait uio_pdrv_genirq.of_id=generic-uio";
};
```
3) Compile the modified DTB
```
you@your-machine:~$ make gpu_device_tree.dtb
```
4) Compile the newer kernel
```
you@your-machine:~$ git clone --depth=1 --branch xilinx-v2023.1 https://github.com/Xilinx/linux-xlnx
                    # Assuming this is ran in this folder
you@your-machine:~$ nix-shell --run 'cd linux-xlnx; build_kernel --config ../kernel_config' 
```
5) Copy `gpu_device_tree.dtb` and `linux-xlnx/kernel.img` to the device
6) Check which NAND partition has the device tree and kernel:
```
root@zedboard-zynq7:~# cat /proc/mtd
dev:    size   erasesize  name
mtd0: 00300000 00020000 "nand-fsbl-uboot"     <========= UBOOT
mtd1: 00500000 00020000 "nand-linux"          <========= KERNEL
mtd2: 00020000 00020000 "nand-device-tree"    <========= DEVICE TREE
mtd3: 00a00000 00020000 "nand-rootfs"
mtd4: 01000000 00020000 "nand-jffs2"
mtd5: 00800000 00020000 "nand-bitstream"
mtd6: 04000000 00020000 "nand-allrootfs"
mtd7: 013e0000 00020000 "nand-release"
mtd8: 00200000 00020000 "nand-reserve"
```
7) Overwrite them in NAND (after running all 4 commands you can reboot to test):
```
root@zedboard-zynq7:~# flash_erase /dev/mtdDEVICETREE 0 0
root@zedboard-zynq7:~# nandwrite -p /dev/mtdDEVICETREE gpu_device_tree.dtb
root@zedboard-zynq7:~# flash_erase /dev/mtdKERNEL 0 0
root@zedboard-zynq7:~# nandwrite -p /dev/mtdKERNEL kernel.img
```
8) Now, U-Boot needs to be patched to *not* pass a command line to the kernel, so it picks up the new one from the DTB
9) Read the existing U-Boot:
```
root@zedboard-zynq7:~# nanddump /dev/mtdUBOOT -f uboot-image 
```
10) Open it in a hex editor, search for "nandroot" and replace `bootargs` with `bootargz`
```
< 0026c6f0: 3d73 6574 656e 7620 626f 6f74 6172 6773  =setenv bootargs
---
> 0026c6f0: 3d73 6574 656e 7620 626f 6f74 6172 677a  =setenv bootargz
```
11) Overwrite U-Boot in NAND (if you fuck this up the board will be left unbootable, you'll need to change the resistor to boot via SD card to recover it).
Do **NOT** reboot the device between these steps.
```
root@zedboard-zynq7:~# flash_erase /dev/mtdUBOOT 0 0
root@zedboard-zynq7:~# nandwrite -p /dev/mtdUBOOT modified-uboot
```
12) Reboot, and once you're back `/sys/class/uio/uio0` should exist.

# Driver

The `driver` folder includes a small kernel module that allows userspace to allocate contiguous `dma-buf`s,
and also provides their physical address back. To build this driver, simply run
```
you@your-machine:~$ nix-shell --run 'cd driver; build_module --config ../kernel_config --kernel ../linux-xlnx'
```
The module can then be loaded with `insmod` on the device.
