# SPDX-License-Identifier: GPL-2.0-only

platform_check_image() {
	echo 'SK-D840N v1 only supports RAM boot; persistent upgrade is unavailable.' >&2
	return 1
}

# sysupgrade -F can skip platform_check_image(); reject the write path too.
platform_do_upgrade() {
	echo 'Refusing to write flash on the SK-D840N RAM bring-up target.' >&2
	exit 1
}
