# SK-D840N 第一版：RAM 启动验证

本 target 基于 ImmortalWrt **v25.12.2 / Linux 6.12.103**，目标是让
SK-D840N 使用原厂 U-Boot 启动现代内核，进入串口上的 ImmortalWrt 用户空间。
这是四网口有线路由器适配的第一步，**目前不支持以太网、光口和持久安装**。
异机日志已确认内嵌 initramfs 的内核 Image 和板级 DTB 生成成功，尚在排查 FIT 打包；
尚无完整固件构建成功或实机启动记录。本地检查不能替代这些验证。

实现和问题见 [研究记录](docs/RESEARCH.md)，配置种子见
[build.config](docs/build.config)。没有导入原厂二进制内核、模块、微码或本机身份数据。

## 在另一台机器编译

以下命令仅供编译机执行。本次开发没有在本地执行它们。
使用完整的 ImmortalWrt 源码及本次提交，并按项目要求安装构建依赖。

```sh
# 在已包含本次提交的源码根目录执行。
./scripts/feeds update -a
./scripts/feeds install -a
# 如已有个人 .config，请先自行备份。
cp target/linux/zx279133/docs/build.config .config
make defconfig
```

检查 `.config` 至少包含：

```text
CONFIG_TARGET_zx279133=y
CONFIG_TARGET_zx279133_generic=y
CONFIG_TARGET_zx279133_generic_DEVICE_skyworth_sk-d840n=y
CONFIG_TARGET_ROOTFS_INITRAMFS=y
CONFIG_TARGET_INITRAMFS_FORCE=y
```

菜单对应 `ZTE ZX279133 (experimental RAM bring-up)` → `Generic` →
`Skyworth SK-D840N RAM bring-up v1`。首轮保持配置精简，不添加 LuCI、无线驱动或
厂商模块。接着执行：

```sh
make download -j8
make -j"$(nproc)" V=s > build-skd840n-v1.log 2>&1
```

失败时保留完整日志，再用 `make -j1 V=s` 定位第一处错误；不要仅提供最后一行
`Error 2`。记录 `git rev-parse HEAD`、`.config` 和各 feed 的提交 ID。

### 初版编译时出现 Restart config / choice 提示

f82a9c945b、ab3db8bd97 的内核配置漏了部分 ARM64 可见选项。发行版在非交互
构建时会因这些未配置项退出；在终端中执行 `make -j1 V=s` 则可能进入问答。
这是本 target 的配置遗漏，不需要修改编译器，也不要通过删除
`FAIL_ON_UNCONFIGURED` 检查或持续输入 `yes` 绕过。

先中止正在等待回答的 make，向编译机同步包含该修复的后续提交。
不必覆盖已有顶层 `.config`，也不必清理工具链。在编译机的 Bash 中执行：

```sh
set -o pipefail
make -j1 V=s target/linux/compile 2>&1 | tee build-skd840n-kernel.log
# 仅上一条成功后继续：
make -j20
```

修改后的 target 配置会参与重新合并。若仍失败，保留完整日志，尤其是首次错误及
末尾约 100 行；配置问题解决并不代表后续驱动编译已通过。

### 时钟驱动报 devm_clk_hw_register_gate_parent_hw 未声明

该接口来自较新内核，Linux 6.12.103 未提供。后续修正通过 target 内部辅助函数，
使用 6.12 已有的 `devm_clk_hw_register_gate_parent_data()` 保留父时钟和资源管理语义。
不要关闭 `-Werror` 或给调用结果加指针强转；后面的 int→pointer 报错是同一缺失接口的连带错误。

在编译机同步修正后，用上面的单线程命令重试 `target/linux/compile`，成功后继续完整构建。
一般不需要清理工具链。若仍出现原接口名，先确认当前 Git 提交和源码中该调用已更新；
如 build_dir 仍保留旧源文件，再执行 `make target/linux/clean` 并重新构建内核。
这只清理内核构建目录，无需运行整个仓库的 `make clean`。

### FIT 打包报找不到 image-.dtb

如果日志已经生成 `image-zx279133-skyworth-sk-d840n.dtb`，但 mkits.sh 的 `-d`
参数却是 `image-.dtb`，这是旧版 `KERNEL_INITRAMFS :=` 过早展开 `DEVICE_DTS` 导致的。
后续修正改为延迟展开的 `KERNEL_INITRAMFS =`，在设备 profile 赋值后取得板级 DTB 名称。

同步修正后，在编译机重新运行 `make -j1 V=s`，无需清理已有内核或手动复制/改名 DTB。
确认新日志中的 `mkits.sh -d` 指向 `image-zx279133-skyworth-sk-d840n.dtb`，再检查最终 FIT。

预期主产物在 `bin/targets/zx279133/generic/`，文件名以
`skyworth_sk-d840n-initramfs-fit.itb` 结尾（前缀由发行版配置决定）。
没有 factory/sysupgrade 镜像；关闭 initramfs 不会得到可安装固件。

编译完成后，在编译机核对：

```sh
find bin/targets/zx279133 -name '*initramfs-fit.itb' -print
# 把以下 FIT_FILE 替换为实际路径。
staging_dir/host/bin/dumpimage -l FIT_FILE
sha256sum FIT_FILE
```

应看到 ARM64 / Linux / gzip 内核，load 与 entry 都为 `0x80000000`，
配置为 `conf@133`，引用内核 `kernel@1` 和设备树 `fdt@133`。
rootfs 已内嵌在内核里，FIT 不需要单独的 ramdisk 节点。
镜像生成时会检查 ARM64 header 的 `text_offset=0`，内核内存占用不超过
32 MiB，完整 FIT 不超过 32 MiB；这不是对所有 U-Boot 限制的验证。

## 原厂 U-Boot RAM 启动

**不需要先自编译或替换 U-Boot。不要将本版文件交给原厂升级界面，也不要写 NAND。**
本机日志已有 FIT/ARM64/gzip 启动证据，但尚未实际测试此新 FIT。

使用现有 TTL 串口（115200 8N1），尽早中断原厂自动启动，先记录：

```text
version
bdinfo
printenv bootcmd bootargs bootm_low bootm_size fdt_high initrd_high
help bootm
help tftpboot
help loady
```

先从 `bdinfo` 确认 RAM 仍是 `0x80000000` 起的 512 MiB，检查重定位地址、栈和
保留区域与下表无冲突。不满足时先分析日志，不套用地址。
原厂 `zxboot` 路径会自动保存环境到 NAND；应在进入该路径前中断启动。
以下命令不含 `saveenv`，也不调用 `zxboot`，但不能据此保证此前的启动阶段从未写过 NAND。

| 用途 | 本版布局 |
|---|---|
| 解压后的 Image（含 BSS 占用） | `[0x80000000, 0x82000000)`，上限 32 MiB |
| 原厂 multi_dtb_fit 指针 | `0x82b00000`，对象大小和后续使用情况待核，暂避开 |
| 完整 FIT 传输地址 | `0x88000000`，文件上限 32 MiB |
| 本次 bootm 可用区间 | `[0x80000000, 0x90000000)`，供启动分配器使用 |
| 保留原厂 WOE 内存 | `[0x91000000, 0x93000000)` |
| 原厂动态 PON 预留 | 保留大小 `0x03245000`，运行时位置待核 |

用户首次现场回报确认原厂 U-Boot 的高地址 LMB 保留区为
`[0x9f6ee080, 0xa0000000)`，当前控制 FDT 为 `0x9f6efd40`。
原提交 f82a9c945b 允许内核占用 64 MiB，可能覆盖新发现的 `multi_dtb_fit`；
现已收紧至 32 MiB。请在编译机同步后续修正提交。该限制只避免这一已知地址的
直接覆盖，不证明固件 DMA 已停机，也不证明其余 RAM 均可任意使用。

可先使用 U-Boot 的 TFTP；它使用 U-Boot 自身网卡驱动，与新内核是否支持网口无关。
本机回报的 ipaddr/serverip 已是下面地址。将 TFTP 服务器所在电脑配置为
`192.168.1.101/24`（不要与现有设备冲突），文件名按实际产物替换：

```text
setenv ipaddr 192.168.1.1
setenv serverip 192.168.1.101
tftpboot 0x88000000 sk-d840n-initramfs-fit.itb
```

只有在传输成功、字节数与编译机文件一致时继续。TFTP 不可用时，如果现场
`help loady` 确认支持，可用 `loady 0x88000000` 后通过串口工具 YMODEM 发送 FIT；
115200 下会较慢。2026-09-18 的现场 `help` 已确认 bootm 支持 FIT 配置选择、
tftpboot 支持指定加载地址、loady 支持 YMODEM；尚未获得实际传输成功的日志。

镜像就位后设置本次临时环境：

```text
setenv bootm_low 0x80000000
setenv bootm_size 0x10000000
setenv fdt_high 0x8fffffff
setenv bootargs 'console=ttyAMA0,115200n8 earlycon=zteuart,0x10d0d000 rdinit=/init maxcpus=1 clk_ignore_unused loglevel=8'
bootm 0x88000000#conf@133
```

这是从文件开头加载的纯 FIT，**不要加原厂 kernel 分区的 `0x1e0` 包装偏移**。
U-Boot 可能用环境覆盖 DT 的 bootargs，所以这里显式重设，不沿用原厂 root/mtd 参数。
`maxcpus=1` 暂时只启动 CPU0；内核仍按双核构建。`clk_ignore_unused` 暂时保留未使用时钟。

记录从复位至 shell 的完整日志。如果提示 FIT 配置找不到、解压空间不足或
`ft_board_setup` 报错，保留原始报错分析；不要用刷写或修改 bootloader 来绕过。
如果出现定时复位，优先调查原厂遗留的四个 watchdog，见研究记录。
未描述网络/PON 节点不等于其 DMA 已停止；U-Boot 的外设停机和 FDT 修正仍需核对。

## 首次成功启动后的采集

按 Enter 进入串口控制台后采集：

```sh
uname -a
cat /proc/cmdline
cat /proc/cpuinfo
cat /proc/iomem
cat /proc/interrupts
cat /proc/mtd
ip -br link
dmesg
mount -t debugfs debugfs /sys/kernel/debug
cat /sys/kernel/debug/clk/clk_summary
```

预期只有 loopback，没有四个以太网口；`/proc/mtd` 可能不存在，因为本版关闭 MTD。
检查 UART 中断是否随键盘输入增长、时钟树是否可读、空闲运行是否会复位。
不要在串口无人值守时直接打开 watchdog 或扫描未知 MDIO/GPIO 寄存器。
确认 CPU0 稳定后，后续试验可从临时 bootargs 去掉 `maxcpus=1`，验证 PSCI 启动第二核。

所有配置变更均在 RAM 中，掉电后丢失。只要原厂启动环境和 NAND 未被修改，
重新上电走原厂启动流程即可回到原系统。本版 `sysupgrade` 的检查和写入路径都会拒绝操作。

## 本地允许的检查

以下仅运行 Python 测试和 shell 语法检查，不编译内核或设备树：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 target/linux/zx279133/image/test_check_image.py
sh -n target/linux/zx279133/base-files/lib/upgrade/platform.sh
git diff --check
```

下一版优先依赖本版的异机编译日志和实机启动日志，随后实现本机 MAC/MDIO/PHY，
以“四网口、1 WAN + 3 LAN”为功能验收目标。光口继续延后。
