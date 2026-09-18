# SK-D840N：先验证 MDIO，不耦合 NAND

## 当前状态

用户提供的基础 FIT 启动日志已证明串口 shell 可用。这里的新镜像、cacheinfo 修复、
MDIO 与只读 SFC 驱动仍未编译或实机验证；15 项新增静态/解释式测试不能替代这些验证。
没有 Ethernet 数据收发驱动，不能把 PHY ID 查询称为四口路由功能。

新增 `skyworth_sk-d840n-mdio` profile，复用现有 I/O DTS 的 MDIO 布线，
但使用 `/delete-node/ &spifc` 删除整个 SPI-NAND 控制器子树，并删除 `spi0` 别名。
这不是仅将 `status` 改成 disabled。保留基础和 `-io` profile 供分别对照。
三者共用内核配置及 cacheinfo 补丁；原厂 U-Boot 可能修正 DT，仍应核对实机传入的树。
不能由此推断更早固件阶段没有访问或写过 NAND。

两条 MDIO 总线、候选地址、只读边界与寄存器协议来源沿用
[IO-BRINGUP.md](IO-BRINGUP.md) 和 [FLASH-EVIDENCE.md](FLASH-EVIDENCE.md)。
probe 仍需开时钟并复位 MDIO 控制器；“只读 PHY ID”不等于不写任何控制寄存器。
不写 PHY 数据，不复位外部 PHY，不自动枚举，不加入光口功能。
本轮没有改动 MDIO/SFC 驱动、原内核配置、内存预留、升级拒绝路径或 FIT 大小限制。

## 在编译机同步和构建

保留此前能启动的 `.itb`。在工作区无未处理冲突的前提下：

```sh
git fetch origin skd840n/readonly-io-20260918
# 首次创建本地分支：
git switch --track -c skd840n/readonly-io-20260918 origin/skd840n/readonly-io-20260918
```

本地已存在此分支时，不重复创建，改为：

```sh
git switch skd840n/readonly-io-20260918
git merge --ff-only origin/skd840n/readonly-io-20260918
```

保留当前 feeds 提交。以下在编译机的 Bash 子 shell 中执行，任一步失败即停止；
没有运行整个项目的 make clean，不清理工具链：

```bash
(
    set -e
    set -o pipefail
    [ ! -f .config ] || cp .config ".config.before-mdio-$(date +%Y%m%d-%H%M%S)"
    cp target/linux/zx279133/docs/build-mdio.config .config
    make defconfig
    make target/linux/clean
    make -j1 V=s target/linux/compile 2>&1 | tee build-skd840n-mdio-kernel.log
    make -j"$(nproc)" V=s 2>&1 | tee build-skd840n-mdio.log
)
```

不要自动回答配置问答、删除 FAIL_ON_UNCONFIGURED 或关闭 Werror。
保留第一处构建错误、完整日志、Git/feed 提交及 `.config`。
本轮没有进行完整配置求值，旧 I/O 配置若有新缺项，仍需依据实际日志补齐。

产物预期为：

```text
bin/targets/zx279133/generic/immortalwrt-zx279133-generic-skyworth_sk-d840n-mdio-initramfs-fit.itb
```

前缀以实际发行版配置为准。用 `staging_dir/host/bin/dumpimage -l FIT_FILE`
核对 ARM64/Linux/gzip、load/entry `0x80000000`、`conf@133` 引用
`kernel@1`/`fdt@133`，记录 SHA256。Image 含 BSS 和 FIT 均维持 32 MiB 上限。
在编译机检查生成的 DTB 不包含 `spi@10d0f000`、`flash@0` 或悬空的 `spi0` 别名；
本轮只做了 DTS 源码约束检查，未运行 dtc。

## RAM 启动

尽早中断自动启动，不进入可能保存环境的 zxboot。
确认 RAM/重定位布局仍符合 README；TFTP 成功且长度正确后才执行 bootm：

```text
setenv ipaddr 192.168.1.1
setenv serverip 192.168.1.101
tftpboot 0x88000000 immortalwrt-zx279133-generic-skyworth_sk-d840n-mdio-initramfs-fit.itb
setenv bootm_low 0x80000000
setenv bootm_size 0x10000000
setenv fdt_high 0x8fffffff
setenv bootargs 'console=ttyAMA0,115200n8 earlycon=zteuart,0x10d0d000 rdinit=/init maxcpus=1 clk_ignore_unused loglevel=8'
bootm 0x88000000#conf@133
```

不执行 saveenv、不写 NAND、不替换 U-Boot；纯 FIT 不加 `0x1e0`。
仍保留 PON/WOE 预留，暂不取消 clk_ignore_unused，不猜测 watchdog/SerDes 寄存器。

## 一次只做一种探测

进入 shell 后逐条执行，上一项正常完成且未异常复位再继续：

```sh
skd840n-diag
skd840n-diag --mdio-c22
skd840n-diag --mdio-c45
```

默认不发起 MDIO 事务。C22 仅查寄存器 2/3，C45 仅查 MMD 1/3 的寄存器 2/3；
候选地址列是十六进制。`candidate-id` 还不是物理端口归属或链路收发证明。
`--mdio` 保留为同时查询两种 ID 的兼容选项，不建议首轮将两步混在一起。

缺少显式请求的 sysfs 接口、cat 失败或返回表含 `error=` 时，工具完成其余报告后
退出 1。`no-id` 不等于端口坏了；退出 0 也不意味着存在可用 PHY 或 Ethernet 已工作。
默认采集中的缺失可选项（例如未挂载 debugfs、未暴露缓存 size）不会被伪造或自动挂载。
分享前检查 dmesg、cmdline 中的本机识别字段，不需要回传原厂配置或闪存内容。

优先回传完整 bootm 到 shell 日志、上述三次输出、固件 SHA256 与源码提交号。
若发生超时或异常复位，先保留对应日志并用原基础 FIT 对照，不通过刷写排查。
MDIO-only 镜像仍只应有 lo，没有 MTD 分区是预期；不要因此切换到写入操作。
只有此步骤明确后，再按 IO-BRINGUP.md 单独测试 `-io` 镜像的 NAND 读取。

## 本轮处理的日志问题

`cacheinfo: Unable to detect cache hierarchy for CPU 0` 对应 6.12.103
`cache_setup_properties()` 的回退返回路径：已经设置 use_arch_info，却继续返回
DT/ACPI 的错误。补丁 130 只在原本允许架构回退时清零错误，其他错误仍保留。
ARM64 的层级/类型来自 CLIDR_EL1；不在 DT 中编造缓存容量或路数，也不承诺所有
size/line-size 字段存在。诊断工具新增已暴露缓存 level/type/shared_cpu_list 等采集。
此修改仍需实机确认警告消失和缓存目录生成，并非通过静态测试证明硬件缓存正常。

`/soc/bdinfo:vid_list` 的 U-Boot 修正失败没有阻止这次启动。本轮不制造未知格式
的厂商属性来消除提示。`ip -br` 改用 BusyBox 支持的命令；已挂载 debugfs 不重复挂载。
仅 CPU0 在线与 maxcpus=1 相符，原厂的 SLAVECPU 消息不能替代 Linux CPU1 在线证据。

双核验证单独使用基础镜像，仅从临时 bootargs 去掉 maxcpus=1，保留其他参数。
核对 cpu/online 为 0-1、timer/interrupts 出现两列、串口交互与持续运行；
没有实机结果之前不把默认首启改成双核，也不同时增加 NAND 或调整时钟。

## 可复现的本地检查

```sh
PYTHONDONTWRITEBYTECODE=1 python3 target/linux/zx279133/tests/test_handoff.py
sh -n target/linux/zx279133/base-files/usr/sbin/skd840n-diag
git diff --check
```

15 项新增测试通过：5 项对补丁后的实际 C 函数作有限 AST 解释，2 项检查源码约束，
8 项在隔离假 sysroot 下测试 shell。cacheinfo hunk 在从 6.12.103 逐字复制的函数
fixture 上零 fuzz、零 offset 应用；没有应用完整发行版补丁链。
5 项 C 解释测试需要 pycparser 和 patch，缺少依赖会明确 skipped，不能算全套通过。
本轮未重新运行旧 9 项 I/O 策略测试或旧 Image 边界测试；MDIO/SFC 驱动未修改。
未执行 make、C 编译器/预处理器、dtc、mkimage 或新固件启动。
