# SK-D840N 只读 I/O 测试

## 状态与保护边界

基础 FIT 已由用户日志证明可以进入串口 shell。本轮新驱动、完整配置和 -io 镜像
**未经过编译或实机验证**，不是四口路由器或可刷写固件。

新增 profile 为 `skyworth_sk-d840n-io`；原基础 DTS 保留。
两种 profile 共用新内核配置，因此重新编译的基础镜像也需验证。
请保留此前能启动的 `.itb`，不要覆盖唯一可用副本。

MDIO 位于 `0x14f01000`、`0x14f02000`，使用已有 LSP1 时钟/reset 实现。
只读 sysfs `phy_ids` 查询 Clause 22 ID 寄存器 2/3；
`phy_ids_c45` 查询 Clause 45 MMD 1/3 的 ID 寄存器 2/3。
仅查询原厂候选地址十进制 `2,5,10,11,12,13,28`，输出地址列为十六进制。
原厂两条总线均列出候选表，不代表每个地址有 PHY，也不是物理网口映射。
probe 不发起 PHY 事务；显式读取 sysfs 才执行。没有 PHY 数据写、外部 PHY reset、
phylib 自动枚举或 Ethernet netdev；每次事务有 10 ms 超时，事务由 mutex 串行化。
Clause 45 地址周期只选择寄存器，不写其数据。

SFC 位于 `0x10d0f000`，SPI 33，映射 4 KiB，避免原厂模板 256 KiB 范围覆盖 GPIO。
使用单线、最高 25 MHz、PIO，唯一分区 `stock-readonly` 覆盖 256 MiB 且标记只读。
控制器 exec_op 另有不可配置的白名单，拒绝 WREN、program、erase、OTP 和坏块 LUT 写入。
关闭 spidev、MTD block、命令行分区及 UBI；无 factory/sysupgrade 安装入口。
即使 U-Boot 修改分区属性，实际命令白名单仍存在。

识别前只允许受约束的 RESET/READ ID/GET FEATURE；必须实际读到 `ef aa 22`，
才允许 W25N02KV 的 page-to-cache/cache-read 和必要的易失特征配置：
A0 只能写 0；B0 只能包含 ECC/buffered-read 位 `0x18`。
这不等于完全不写寄存器：控制器、时钟和 NAND 易失配置会改变，
但不允许擦除或编程 NAND 阵列。其他芯片或未允许的特征值保守失败。
RESET 保留确认过的不可变芯片身份，兼容 resume 不再 READ ID 的配置路径。
Linux SPI-NAND 即使只读也会选择写缓存模板，所以 supports_op 仅描述传输能力，
实际写入在 exec_op 拒绝，而不是让整个芯片探测失败。

两驱动依赖原厂 U-Boot 留下的 pinmux，MDIO 还依赖分频设置，不是完整冷启动初始化。
没有实现 Ethernet MAC/NP/FPP/IDM DMA、USB、GPIO/LED/按键、温控或 watchdog 接管。
不用光口，但未证明 PON/WOE DMA 停机前不能回收其内存预留。

## 编译机操作

以下命令仅供用户的编译机执行。本轮开发没有运行 make、defconfig、dtc 或编译器。
在完整源码中同步本分支；已存在本地分支时直接 git switch，不重复 --track：

```sh
git fetch origin skd840n/readonly-io-20260918
git switch --track origin/skd840n/readonly-io-20260918
[ ! -f .config ] || cp .config .config.before-readonly-io
cp target/linux/zx279133/docs/build-io.config .config
make defconfig
make target/linux/clean
```

保留既有 feeds 提交；新工作区按项目说明初始化依赖和 feeds。
新增 overlay/补丁需要重新准备内核，不必清理整个工具链。
在 Bash 中，上一条成功后才运行下一条：

```sh
set -o pipefail
make -j1 V=s target/linux/compile 2>&1 | tee build-skd840n-io-kernel.log
make -j"$(nproc)" V=s 2>&1 | tee build-skd840n-io.log
```

保存 Git commit、feeds 提交、`.config` 和完整日志。出现配置问答或 C 错误时
保留第一处诊断，不自动回答、不删除 FAIL_ON_UNCONFIGURED、不关闭 Werror。

预期产物在 `bin/targets/zx279133/generic/`：
`immortalwrt-zx279133-generic-skyworth_sk-d840n-io-initramfs-fit.itb`。
实际前缀以发行版配置为准。用 `staging_dir/host/bin/dumpimage -l FIT_FILE`
检查 ARM64/Linux/gzip、load/entry `0x80000000`、`conf@133` 和 `fdt@133`；
再记录 SHA256。Image 含 BSS 占用和整个 FIT 仍各限 32 MiB，不放宽限制。

## RAM 启动

尽早中断原厂自动启动，不进入可能保存环境的 zxboot。确认 TFTP 电脑为
`192.168.1.101/24`、传输字节数与编译机 FIT 一致，再继续 bootm。
仅更换文件名，保持用户已验证的地址、配置名和参数：

```text
setenv ipaddr 192.168.1.1
setenv serverip 192.168.1.101
tftpboot 0x88000000 immortalwrt-zx279133-generic-skyworth_sk-d840n-io-initramfs-fit.itb
setenv bootm_low 0x80000000
setenv bootm_size 0x10000000
setenv fdt_high 0x8fffffff
setenv bootargs 'console=ttyAMA0,115200n8 earlycon=zteuart,0x10d0d000 rdinit=/init maxcpus=1 clk_ignore_unused loglevel=8'
bootm 0x88000000#conf@133
```

纯 FIT 不加 `0x1e0`。不执行 saveenv、不写 NAND、不替换 U-Boot、不用原厂升级界面。
这不保证原厂更早阶段绝未写过 NAND。保留所有 PON/WOE 预留和未知外设时钟；
不凭猜测关闭 watchdog 或写 MAC/SerDes/GPIO 寄存器。

启动失败或异常复位时，保留完整串口日志，重新上电后使用原基础 `.itb` 对照；
不通过刷写或修改永久环境绕过错误。

## 验收与回传

进入 shell 后先执行默认被动采集；正常且无异常复位后才显式读 PHY ID：

```sh
skd840n-diag
skd840n-diag --mdio
```

默认工具不读闪存内容/配置文件、不挂载 debugfs、不扫描任意 MMIO。
dmesg/cmdline 仍可能含识别信息，分享前检查。

检查两个 MDIO probe 结果、C22/C45 ID/超时，以及 NAND 型号、几何、flags、ECC 统计。
预期 W25N02KV 为 256 MiB、128 KiB eraseblock、2048 字节页、128 字节 OOB。
出现 MTD 分区不等于读取数据正确，更不等于可以安装。
目前无 Ethernet 数据面，ip link 只有 lo 不是 MDIO 诊断失败。

仅在标签/几何正确、无超时/ECC 错误后，可额外只读第一页到 RAM：

```sh
dev="$(awk '$4 == "\"stock-readonly\"" { sub(/:$/, "", $1); print $1; exit }' /proc/mtd)"
if [ -n "$dev" ]; then
    dd if="/dev/$dev" of=/tmp/nand-first-page.bin bs=2048 count=1 &&
        sha256sum /tmp/nand-first-page.bin
fi
```

提供的主数据备份中偏移 0 起 2048 字节 SHA256：
`27c5cd604fff0ed138c98407a3a169ef83384da553bfef930a6f1ac92d59c0a1`。
匹配只是初步证据；不匹配先分析读取错误、ECC/OOB、备份格式或数据变化，
不尝试写回。无需公开页内容/整片备份。不要实际擦除或写入来测试保护。

双核试验与新外设分开：先用基础镜像只去掉 maxcpus=1，
核对 cpu/online、timer/IRQ 两列、串口与稳定性。当前日志没有证明双核故障。
暂不同时去掉 clk_ignore_unused。

## 本地测试与下一阶段

```sh
PYTHONDONTWRITEBYTECODE=1 python3 target/linux/zx279133/tests/test_readonly_io.py
sh -n target/linux/zx279133/base-files/usr/sbin/skd840n-diag
git diff --check
(cd target/linux/zx279133 && sha256sum -c docs/SOURCES-io.sha256)
```

9 项测试通过。5 项依赖 pycparser；缺少时明确 skipped，不能算全套通过。
解释的是实际 C 白名单函数的有限 AST，覆盖 opcode 拒绝集合和 65536 组特征地址/值，
不模拟完整 C 类型/溢出、SPI 核心、MMIO、中断、时序或硬件。
补丁四个 hunk 对已取得的 Linux 6.12.103 源码片段零 fuzz 应用，
不是完整 ImmortalWrt 补丁链验证；完整配置求值、编译、新 FIT 和实机均待验证。

下一步用真实 PHY ID 确认型号/总线，再核对 MAC/SerDes/物理网口和微码 ABI，
实现单口 MAC/NP/FPP/IDM DMA 收发后扩至四口。原厂/社区微码 CRC 不同只是差异证据，
不能证明绝对不兼容，也不能据此直接互换。本轮未导入微码或旧模块。
光口明确不在目标内；其他 GPIO/USB 功能待单独核对接线及初始化。
