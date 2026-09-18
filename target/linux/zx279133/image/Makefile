# SPDX-License-Identifier: GPL-2.0-only

include $(TOPDIR)/rules.mk
include $(INCLUDE_DIR)/image.mk

define Build/zx279133-check-image
	python3 $(CURDIR)/check-image.py image $@
endef

define Build/zx279133-check-fit
	python3 $(CURDIR)/check-image.py fit $@
endef

define Device/Default
  PROFILES := Default
  KERNEL_NAME := Image
  KERNEL_LOADADDR := 0x80000000
  KERNEL_ENTRY := 0x80000000
  KERNEL_DEPENDS := $(CURDIR)/check-image.py
  DEVICE_DTS_DIR := ../dts
  DEVICE_DTS_CONFIG := conf@133
  DEVICE_DTS_DELIMITER := @
  DEVICE_FDT_NUM := 133
  KERNEL_INITRAMFS_SUFFIX := -fit.itb
  # DEVICE_DTS is assigned by the device profile after Device/Default.
  KERNEL_INITRAMFS = kernel-bin | zx279133-check-image | gzip | fit gzip $$(KDIR)/image-$$(DEVICE_DTS).dtb | zx279133-check-fit
  IMAGES :=
endef

define Device/skyworth_sk-d840n
  DEVICE_VENDOR := Skyworth
  DEVICE_MODEL := SK-D840N
  DEVICE_VARIANT := RAM bring-up v1
  DEVICE_DTS := zx279133-skyworth-sk-d840n
endef
TARGET_DEVICES += skyworth_sk-d840n

# Same RAM/FIT limits, separate DTS: the existing profile stays minimal.
define Device/skyworth_sk-d840n-io
  DEVICE_VENDOR := Skyworth
  DEVICE_MODEL := SK-D840N
  DEVICE_VARIANT := Read-only I/O bring-up (experimental)
  DEVICE_DTS := zx279133-skyworth-sk-d840n-io
endef
TARGET_DEVICES += skyworth_sk-d840n-io

# PHY identification without probing the experimental SPI-NAND controller.
define Device/skyworth_sk-d840n-mdio
  DEVICE_VENDOR := Skyworth
  DEVICE_MODEL := SK-D840N
  DEVICE_VARIANT := MDIO-only RAM bring-up (experimental)
  DEVICE_DTS := zx279133-skyworth-sk-d840n-mdio
endef
TARGET_DEVICES += skyworth_sk-d840n-mdio

$(eval $(call BuildImage))
