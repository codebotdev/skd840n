# SK-D840N：RAM 启动与只读外设适配

基于 ImmortalWrt **v25.12.2 / Linux 6.12.103**。

2026-09-18 用户日志已确认此前基础 FIT 可启动并进入串口 shell。
本轮新增独立的只读 I/O 测试 profile；**新增驱动仅通过静态/解释式检查，
尚未编译、尚未实机验证。Ethernet 四口收发及持久安装仍未实现。**

| profile | 内容 |
|---|---|
| `skyworth_sk-d840n` | 原基础 DTS，串口/RAM 启动 |
| `skyworth_sk-d840n-io` | 新增 MDIO PHY-ID 诊断、受限只读 SPI-NAND |

两者共用新内核配置；重新构建的基础镜像也需重新验证。
请保留目前能启动的旧 `.itb`，不要覆盖唯一可用副本。

## 文档入口

[只读 I/O 构建、RAM 启动和验收](docs/IO-BRINGUP.md) 是本轮测试入口。
[研究记录](docs/RESEARCH.md) 保留全部历史反馈、根因与每轮验证限制。
[闪存证据](docs/FLASH-EVIDENCE.md) 只包含可公开的硬件字段和校验值。
配置种子为 [基础 profile](docs/build.config) 和 [I/O profile](docs/build-io.config)；
两者是顶层 `.config` 的起点，不是内核配置。

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
新镜像提供 `skd840n-diag`，`--mdio` 才触发候选 PHY ID 读取；
MDIO 与 NAND 的具体验收、只读边界和限制见 IO-BRINGUP.md。

确认单核稳定后，再单独用基础镜像去掉临时 `maxcpus=1` 验证双核。
当前日志没有证明双核故障，也没有证明长期稳定。
保留从复位到 shell 的完整日志；异常时重新上电并使用保留的旧 FIT 对照，
不通过刷写或修改 bootloader 绕过问题。

## 本地检查

```sh
PYTHONDONTWRITEBYTECODE=1 python3 target/linux/zx279133/tests/test_readonly_io.py
sh -n target/linux/zx279133/base-files/usr/sbin/skd840n-diag
git diff --check
```

历史 Image 检查仍可运行
`python3 target/linux/zx279133/image/test_check_image.py`，但本轮没有重跑。
后续目标是按真实 PHY/SerDes/微码 ABI 实现单口数据收发，再扩至四口与 1 WAN + 3 LAN。
光口不在本次目标内。当前所有配置仅在 RAM，升级检查和写入入口仍拒绝 sysupgrade。
