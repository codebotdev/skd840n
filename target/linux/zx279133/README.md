# SK-D840N：RAM 启动与只读外设适配

基于 ImmortalWrt **v25.12.2 / Linux 6.12.103**。

2026-09-18 用户日志已确认此前基础 FIT 可启动并进入串口 shell。
本分支提供独立的 MDIO-only 和只读 I/O 测试 profile。最新异机日志已生成 MDIO/SFC
驱动目标文件，但在内核链接时遇到 MTD 块转换注册符号缺失；本轮补丁修正这一配置组合。
**修复后的完整内核/FIT 尚未编译通过，新外设尚未实机验证；四口收发与持久安装仍未实现。**

| profile | 内容 |
|---|---|
| `skyworth_sk-d840n` | 原基础 DTS，串口/RAM 启动 |
| `skyworth_sk-d840n-mdio` | 优先测试：仅 MDIO PHY-ID 诊断，删除 SFC/NAND 节点 |
| `skyworth_sk-d840n-io` | 独立后续测试：MDIO 诊断、受限只读 SPI-NAND |

三者共用内核配置和 cacheinfo 修复；重新构建的基础镜像也需重新验证。
请保留目前能启动的旧 `.itb`，不要覆盖唯一可用副本。

## 文档入口

[MDIO-only 构建、RAM 启动和验收](docs/MDIO-BRINGUP.md) 是当前优先测试入口。
[只读 I/O 测试](docs/IO-BRINGUP.md) 保留为之后的 NAND 独立验证步骤。
[研究记录](docs/RESEARCH.md) 保留全部历史反馈、根因与每轮验证限制。
[闪存证据](docs/FLASH-EVIDENCE.md) 只包含可公开的硬件字段和校验值。
配置种子为 [基础](docs/build.config)、[MDIO-only](docs/build-mdio.config)
和 [I/O](docs/build-io.config)；均为顶层 `.config` 的起点，不是内核配置。

## MTD 链接错误：register_mtd_blktrans_devs

2026-09-18 的 `build-skd840n-mdio-kernel-retry.log` 已越过配置问答和驱动 C 编译。
`undefined reference to register_mtd_blktrans_devs` 来自 generic 402 补丁无条件调用
未编入的块转换层函数；紧随其后的 `R_AARCH64_CALL26` 是对同一未定义符号的诊断，
不需要改变 Image 加载地址、放宽 32 MiB 上限或修改工具链。

新增 target 补丁 140：当 `CONFIG_MTD_BLKDEVS` 关闭时提供空 inline hook；
启用时保留原外部声明。不打开 MTD_BLOCK/MTD_BLOCK_RO，不改 NAND 命令白名单。
详细来源、已验证阶段及限制见 [研究记录](docs/RESEARCH.md) 末节。

在编译机同步当前分支后，保留已有 `.config` 和 feeds，在 Bash 中重试：

```bash
(
    set -e
    set -o pipefail
    make target/linux/clean
    make -j1 V=s target/linux/compile 2>&1 | tee build-skd840n-mdio-linkfix-kernel.log
    make -j"$(nproc)" V=s 2>&1 | tee build-skd840n-mdio-linkfix.log
)
```

新增补丁需要重新准备内核；不清理工具链、不重新复制配置种子。
只有内核成功才继续完整构建；启动方法仍按 MDIO-BRINGUP.md，不先进行刷写。
本轮本地仅运行 `tests/test_mtd_blktrans.py` 的 9 项静态测试和补丁应用检查，
未运行编译器、C 预处理器、make、dtc 或固件测试。

## 基础 profile 的编译与启动

以下仅供用户的另一台编译机执行。本轮未在本地执行 make、defconfig 或编译器。
使用完整源码；新工作区按 ImmortalWrt 要求安装依赖并初始化 feeds。
已有工作区保留 feed 提交，先备份个人 `.config`：

```sh
[ ! -f .config ] || cp .config .config.before-skd840n
cp target/linux/zx279133/docs/build.config .config
make defconfig
make target/linux/clean
```

在 Bash 中构建，上一条成功后才继续下一条：

```sh
set -o pipefail
make -j1 V=s target/linux/compile 2>&1 | tee build-skd840n-kernel.log
make -j"$(nproc)" V=s 2>&1 | tee build-skd840n.log
```

记录 Git、feeds 提交、`.config`、完整日志和产物 SHA256。
出现 Restart config 不要自动回答或删除 FAIL_ON_UNCONFIGURED；
时钟 API 错误不通过强转或关闭 Werror 绕过。
之前 image-.dtb 问题已用延迟展开 KERNEL_INITRAMFS 修复，不需要伪造同名 DTB。
这些历史问题的详细原因和验证限制仍在 RESEARCH.md 中。

产物位于 `bin/targets/zx279133/generic/`；基础文件名以
`skyworth_sk-d840n-initramfs-fit.itb` 结尾。
用 `staging_dir/host/bin/dumpimage -l FIT_FILE` 核对 ARM64/Linux/gzip、
load/entry `0x80000000`、`conf@133` 引用 `kernel@1`/`fdt@133`。
内嵌 initramfs，不需要独立 ramdisk；不生成 factory/sysupgrade 镜像。

尽早中断原厂自动启动，确认 512 MiB RAM 与重定位/栈/预留无冲突。
TFTP 电脑为 `192.168.1.101/24`，文件名按实际基础产物替换：

```text
setenv ipaddr 192.168.1.1
setenv serverip 192.168.1.101
tftpboot 0x88000000 immortalwrt-zx279133-generic-skyworth_sk-d840n-initramfs-fit.itb
setenv bootm_low 0x80000000
setenv bootm_size 0x10000000
setenv fdt_high 0x8fffffff
setenv bootargs 'console=ttyAMA0,115200n8 earlycon=zteuart,0x10d0d000 rdinit=/init maxcpus=1 clk_ignore_unused loglevel=8'
bootm 0x88000000#conf@133
```

传输成功且字节数正确才执行 bootm。可用性已由 help 确认的 loady/YMODEM
是串口传输备选，不代表新内核网卡可用。纯 FIT 不加 `0x1e0`。
不执行 saveenv、不调用可能保存环境的 zxboot，不刷写 NAND 或替换 U-Boot。
这并不保证原厂更早的启动阶段绝未写过闪存。

Image 含 BSS 占用和 FIT 文件仍各限 32 MiB，避开 `0x82b00000` 的原厂对象；
保留 WOE `[0x91000000,0x93000000)` 和原动态 PON 预留。
不用光口不等于固件 DMA 已停止；不随意回收内存或关闭遗留时钟。
Linux watchdog 驱动未启用也不证明固件 watchdog 已关闭。

## 串口检查

```sh
uname -a
cat /proc/cmdline
cat /proc/cpuinfo
cat /proc/iomem
cat /proc/interrupts
ip link show
dmesg
grep -qs ' /sys/kernel/debug debugfs ' /proc/mounts || mount -t debugfs debugfs /sys/kernel/debug
cat /sys/kernel/debug/clk/clk_summary
```

该 BusyBox 不支持 `ip -br`。debugfs 已挂载时无需重复 mount。
旧日志没有 `/proc/mtd` 是旧版关闭 MTD 的结果；现在启用核心，但基础 DTS 无 NAND 节点。
目前仍没有 Ethernet 数据面，只有 lo 不等于硬件网口损坏。
新镜像提供 `skd840n-diag`，默认只采集被动信息；先分开运行 `--mdio-c22`
和 `--mdio-c45`，兼容的 `--mdio` 选项同时读取两种 ID。
缺少显式请求的接口、读取失败或结果含 `error=` 时退出 1，不再静默视为成功。
本分支增加 cacheinfo 首核回退修复，不伪造缓存大小；详见 MDIO-BRINGUP.md。

确认单核稳定后，再单独用基础镜像去掉临时 `maxcpus=1` 验证双核。
当前日志没有证明双核故障，也没有证明长期稳定。
保留从复位到 shell 的完整日志；异常时重新上电并使用保留的旧 FIT 对照，
不通过刷写或修改 bootloader 绕过问题。

## 本地检查

```sh
PYTHONDONTWRITEBYTECODE=1 python3 target/linux/zx279133/tests/test_mtd_blktrans.py
PYTHONDONTWRITEBYTECODE=1 python3 target/linux/zx279133/tests/test_handoff.py
PYTHONDONTWRITEBYTECODE=1 python3 target/linux/zx279133/tests/test_readonly_io.py
sh -n target/linux/zx279133/base-files/usr/sbin/skd840n-diag
git diff --check
```

历史 Image 检查仍可运行
`python3 target/linux/zx279133/image/test_check_image.py`，但本轮没有重跑。
后续目标是按真实 PHY/SerDes/微码 ABI 实现单口数据收发，再扩至四口与 1 WAN + 3 LAN。
光口不在本次目标内。当前所有配置仅在 RAM，升级检查和写入入口仍拒绝 sysupgrade。
