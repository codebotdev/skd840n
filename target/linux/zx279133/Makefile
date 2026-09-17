# SPDX-License-Identifier: GPL-2.0-only

include $(TOPDIR)/rules.mk

ARCH:=aarch64
BOARD:=zx279133
BOARDNAME:=ZTE ZX279133 (experimental RAM bring-up)
FEATURES:=ramdisk fpu
CPU_TYPE:=cortex-a53
SUBTARGETS:=generic
KERNELNAME:=Image
KERNEL_PATCHVER:=6.12
DEVICE_TYPE:=basic

include $(INCLUDE_DIR)/target.mk

# Serial-only bring-up: omit persistent-storage tools and router/UI defaults.
DEFAULT_PACKAGES := $(filter-out mtd block-mount default-settings-chn kmod-nf-nathelper luci,$(DEFAULT_PACKAGES))

define Target/Description
	Experimental ZX279133 support. SK-D840N initramfs boots from RAM using
	the stock U-Boot. Ethernet and persistent installation are not supported.
endef

$(eval $(call BuildTarget))
