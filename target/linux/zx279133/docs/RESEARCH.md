# SK-D840N 适配研究与修改记录

## 2026-09-17：v1，建立可供异机编译的 RAM 启动实现

用户目标：先做四网口有线路由器，光口后续研究；本地不编译，只修改、检查并提交代码。
v1 的实际交付是串口/RAM bring-up，实现现代内核启动所需的基础支持。
**编译结果、能否实机启动、四网口可用性均未获得验证。**

### 基线与证据

| 来源 | 固定版本 / 内容 | 用途 |
|---|---|---|
| ImmortalWrt | v25.12.2，`4fc16f2985a358bd43bb522e43f05395fcbd6ed5` | 实际开发基线，保留其 Linux 6.12.103 |
| Linux 发布源码 | `linux-6.12.103.tar.xz`，SHA256 `f143aaade8877ba5616e788b4482576db28481bcf557ef537f4fcc3938fc3176` | 核对接口、ARM64 header，并进行补丁应用检查 |
| 本机备份 | 工作区 `skd840nbackup/`、原厂 DT、`close_uart_ttl.log` | RAM、UART、中断、PSCI、闪存和原厂启动行为 |
| 先前研究 | 工作区 `bringup-planning/`，尤其 HARDWARE.md、PLAN.md 和 evidence/stock-board.decompiled.dts | 硬件和镜像证据汇总；不要求编译机具有这些文件 |
| SK-D840N 参考项目 | `huxiangjs/SK-D840N-OpenWRT`，`011319bed96959107423a223e975b6e78d87ce68`；用户提供 firmware_v2.0.zip | 参考初始化流程与网口映射，不导入其旧内核或模块 |
| 同 SoC 社区移植 | `cnjn/linux-mainline-zte-zxslc-sr1010`，`07f8687248578d4be6931c665ff5d08bb6cc3d9d` | 时钟驱动、DT 头文件，以及 UART 补丁的来源 |

外部原始来源：

- [ImmortalWrt v25.12.2](https://github.com/immortalwrt/immortalwrt/tree/v25.12.2)
- [Linux 6.12.103](https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.103.tar.xz)
- [Linux ARM64 启动约定](https://www.kernel.org/doc/html/v6.12/arch/arm64/booting.html)
- [SK-D840N 参考工程固定版本](https://github.com/huxiangjs/SK-D840N-OpenWRT/tree/011319bed96959107423a223e975b6e78d87ce68)
- [ZX279133 社区源码固定版本](https://github.com/cnjn/linux-mainline-zte-zxslc-sr1010/tree/07f8687248578d4be6931c665ff5d08bb6cc3d9d)

社区项目名称含 mainline，不代表其 ZX279133 板级支持已经进入 Linux 主线。
这里的 UART、时钟和 DT ABI 是本 target 自带的下游支持。

### 已知硬件及不能套用的假设

- 本机为 ZX279133、2×Cortex-A53、512 MiB RAM，内存起点 `0x80000000`。
  原厂运行日志证明 PSCI SMC、GICv3 和 25 MHz architected timer 可用。
- UART0 地址 `0x10d0d000`，SPI 31，原厂 AMBA ID `0x001feffe`。
  它与标准 PL011 的寄存器偏移、访问宽度和部分状态位不同，不能只写 `arm,pl011`。
- 本机 NAND 日志为 Winbond `ef aa 22` / 256 MiB；参考照片是 GigaDevice，不能按照片写死。
- 四网口候选映射：2.5G 对应 MAC4，三个 GE 对应 MAC1/2/3，来自参考工程私有
  `/dev/ethdriver` 初始化。还不是现代 netdev/DSA 驱动的可用映射。
- 本机 PHY 软件命名 RTL8226/rlt8226b，参考照片可读 RTL8221B；准确 PHY ID 和后缀待采集。
- SR1010 的 ZX279051 WAN PHY、RTL8372N 外置交换机及 10G CPU 链路与本机不同。
  不复制其 Ethernet DTS、PHY 地址、GPIO reset 或交换机配置。
- 原厂/参考微码 `mcode_133.bin` SHA256 为
  `251acd2858aa1486dac66386842285a3e4aef55a5335891da5d595b937084db0`，
  CRC32 `c839b946`；社区网络驱动的已校准微码 CRC 为 `ef1d7647`。
  两者不一致，导入社区网络驱动之前必须核对微码 ABI、NP/FPP 寄存器和队列格式。

### 本次修改

| 文件 / 目录 | 修改与原因 |
|---|---|
| Makefile | 新建 aarch64/cortex-a53 target；仅 ramdisk/fpu 特性；保留 Linux 6.12；删除默认 mtd 和路由器/UI 扩展包 |
| generic/target.mk | 标准 generic 子目标，与设备 profile、配置种子和镜像选择规则保持一致 |
| config-6.12 | 启用 GICv3、PSCI、标准 timer、OF、时钟/reset、ZTE 适配后的 PL011；双核构建；关闭 MTD、SPI、PCI、USB、watchdog；不启用 KASLR |
| dts/zx279133.dtsi | 从本机 DT 提取 CPU/中断/串口/时钟最低集合，采用社区现代 clock/reset ABI；没有 NAND、网络和 PON 设备节点 |
| dts/zx279133-skyworth-sk-d840n.dts | 新板兼容串、512 MiB RAM、串口与保留内存；单核首启、保持未使用时钟；`rdinit=/init` |
| files-6.12/drivers/clk/clk-zx279133.c | 导入社区时钟/reset 驱动；A53 mux 强制只读，去除对未导入实验性 cpufreq 配置的依赖 |
| files-6.12/include/dt-bindings/ | 导入 TOPCRM/LSP 时钟及 LSP reset 编号，供 DTS 和驱动共同使用 |
| patches-6.12/100-* | 将时钟驱动挂接至 Kconfig/Kbuild；无需额外 RESET_SIMPLE 驱动 |
| patches-6.12/110-* | 从社区 consolidated patch 0001 提取 UART 修改并重定位到 6.12.103；加入 ZTE offsets、32-bit IO、earlycon、AMBA ID 和 shared-reset deassert |
| image/Makefile | 生成 gzip 内核 + DT 的 FIT，内嵌 initramfs；load/entry `0x80000000`，配置 `conf@133`；无 factory/sysupgrade 产物 |
| image/check-image.py | 生成镜像时检查 Image header、内存占用与 FIT 文件尺寸，防止沿用旧内核地址或超出约定 RAM 布局 |
| image/test_check_image.py | 使用合成文件验证头部、BSS 占用、旧偏移、截断和尺寸上限；不涉及编译 |
| base-files/lib/upgrade/platform.sh | 分别拒绝镜像检查和实际写入，防止 `sysupgrade -F` 绕过前者 |
| docs/build.config、README.md | 异机构建配置、临时 U-Boot 启动方法、验收和日志采集步骤 |

保留了来源文件的 SPDX 标识；UART 补丁记录原作者 cnjn。时钟驱动保留社区其他时钟域的
实现以便后续复用，但本机 DTS 只实例化 TOPCRM 与 LSP0，没有 LSP1 或网络外设消费者。
此举并不代表所有时钟域已在本机验证。来源文件与本地文件的哈希见
[SOURCES.sha256](SOURCES.sha256)。

### 研究中发现并处理的问题

1. **内核版本不同。** 发行版是 6.12.103，社区系列基于 6.18。
   不直接叠加完整系列，提取最低所需部分。对照 6.12.103 头文件确认用到的
   `devm_clk_hw_register_*` 和 `devm_reset_control_get_shared_deasserted()` 接口存在。
   这只验证接口声明，不能替代编译。
2. **加载地址不能照搬。** 原厂 4.19 Image 的 `text_offset=0x80000`，
   6.12.103 `arch/arm64/kernel/head.S` 的 header 明确为 0。
   新 FIT 改用 2 MiB 对齐的 `0x80000000`，并在镜像流水线中检查 header。
   内存占用用 header 的 image_size（含 BSS）核对，不只看 gzip 大小。
3. **不能跳过 initramfs 初始化。** 检查发行版 `target/linux/generic/other-files/init`
   后，入口采用 `/init`，由它设置 INITRAMFS、切换 tmpfs 根并执行 procd。
   不直接将 `/bin/sh` 或 `/etc/preinit` 设为正常启动入口。
4. **部分旧配置符号失效。** 对照 6.12.103 Kconfig 定义清理
   ARCH_PHYS_ADDR_T_64BIT、CLKSRC_OF、DMA_REMAP，避免沿用旧模板中的无效符号。
5. **原厂 U-Boot 会修正 DT、自动保存环境。** 磁盘 DT 不等于实际传入 Linux 的 DT；
   `zxboot` 路径会写 env。测试流程要求尽早中断自动启动，直接 RAM `bootm`，不修改持久环境。
6. **旧模块不能作为现代驱动。** 参考 release 混有 4.19 与 6.6 模块，
   无法加载到本版 6.12。没有将这些文件作为 kmod 或 firmware 包导入。

### 仍需实机或异机验证的问题

| 优先级 | 问题 | 下一步及验收 |
|---|---|---|
| P0 | 本版从未实际编译 | 在另一台机器完整构建，保存 feed ID、配置和日志；确认 FIT 元数据及体积 |
| P0 | 原厂 U-Boot 接受新 FIT 与 DT 的情况未知 | 验证 `bootm ...#conf@133`、gzip 解压、ft_board_setup、传给内核的 bootargs 和内存预留 |
| P0 | PON/WOE DMA 停机与内存交接不明确 | 检查 U-Boot 停机路径与实际 /proc/iomem；保留内存不等于 DMA 已安全停机，不凭猜测写寄存器 |
| P0 | 四 watchdog 的 handoff 未知 | 原厂 Linux 会显式启动四个实例，但不能据此断言 U-Boot 已启用；记录复位时间和原因，核对 0x14f09000–0x14f0c000 的状态与 reset 路径 |
| P0 | UART 时钟和 console 切换 | 确认 earlycon 到 ttyAMA0 持续输出和可输入；检查 25 MHz UART parent、reset、IRQ 31 |
| P1 | 双核 PSCI | 先 CPU0 空闲稳定，再移除临时 maxcpus=1，验证第二核和 timer 中断 |
| P1 | 四网口 MAC/MDIO/PHY/微码 | 先最小 MDIO 读取和单口收发；核对队列/IRQ/DMA/MAC/PHY ID，再扩至四口；不套用 SR1010 switch 拓扑 |
| P1 | 长时间稳定性 | 串口交互、timer、空闲运行、内存压力及重复重启；当前没有温控、cpufreq 或硬件 watchdog 接管 |
| P2 | NAND 持久安装 | 单独验证 SPI-NAND ECC/OOB、坏块、分区和恢复；保留原 boot/身份区；当前不生成升级镜像 |
| 延后 | 光口、USB、LED/按键 | 四网口路由功能稳定后逐项适配；光口最后研究 |

动态 PON 预留只保留原厂大小/对齐；Linux 重新选择的地址可能不同于固件 DMA 所用地址。
WOE 的固定 32 MiB 预留也不涵盖所有潜在 DMA 区域。
关闭 Linux watchdog 驱动不会关闭可能由固件启动的 watchdog；如果 RAM 启动后定时复位，
下一轮应基于证据实现接管，而非把错误归为随机内核崩溃。

### 本次检查与范围

- 从 kernel.org 下载 6.12.103 发布源码，SHA256 与发行版 kernel-6.12 文件匹配。
- 在独立临时源码目录中按发行版顺序应用 278 个 backport、142 个 pending、
  58 个 hack 补丁，再应用本 target 的 2 个补丁；全部 `patch --fuzz=0` 成功。
  没有通过 make 准备源码，也没有执行 C 编译器。
- 对新增配置符号、DT 宏引用、驱动 API 声明、FIT 生成脚本和启动入口进行静态核对。
- Python 镜像边界测试 7 项通过（含多组异常输入）；shell 语法检查通过，
  普通镜像检查与强制升级写入路径均返回 1；FIT ITS 文本的配置引用与加载地址正确。
  新增 101 个内核配置项名称均存在且无重复，DT 头文件与 ZX279133 宏引用可解析。
  对时钟 C 文件和 UART 补丁运行 checkpatch，修正补丁说明换行后无错误或警告；
  Git 空白检查通过。
- **未执行 make/defconfig、内核编译、dtc、dtbs_check、镜像生成或实机启动。**
  FIT ITS 文本可用占位输入检查生成规则，不能据此声称有可用固件文件。

## 现场回报 1：原厂 U-Boot version / bdinfo / printenv

来源：用户在原厂 U-Boot 提示符下执行三条命令后提供的输出。
这里只保存硬件交接相关字段，不复制 ethaddr 等本机身份字段。

| 现场字段 | 值与含义 |
|---|---|
| version | U-Boot 2021.01-svn308776，Jan 02 2025 09:09:11 +0800，与既有证据一致 |
| DRAM start / size | `0x80000000` / `0x20000000`，512 MiB |
| relocaddr / TLB | `0x9ff3c000` / `0x9fff0000` |
| irq_sp / sp start | `0x9f6efd30` |
| fdt_blob / new_fdt | `0x9f6efd40`；fdt_size `0xc0a0` |
| multi_dtb_fit | `0x82b00000`；本次新发现的低地址固件对象指针 |
| LMB reserved 0 | base `0x91000000`，size `0x02000000`，对应 WOE 保留区 |
| LMB reserved 1 | base `0x9f6ee080`，size `0x00911f80`，结束于 `0xa0000000` |
| bootcmd / bootdelay | `zxboot` / 1 秒；仍需尽早中断自动启动 |
| current eth / ethact | `eth0`；只能证明已选中设备，不能证明 TFTP 或链路成功 |
| ipaddr / serverip | `192.168.1.1` / `192.168.1.101` |
| bootargs | 含 console=ttyAMA0,115200n8、rdinit=/sbin/init 及厂商参数；RAM 启动时按 README 临时替换 |
| bootm_low / bootm_size / fdt_high / initrd_high | 完整环境中未列出；不能据此推断厂商编译时默认值 |

`memory.size=0` 与 `reserved.size=0` 是这一打印格式中的字段；RAM bank 和
LMB 区间条目明确列出非零大小，不能将这两处 0 理解为“没有内存/预留”。
`flashsize=0` 也不否定 SPI-NAND 的存在；本次环境仍列出了 spi-nand0 分区映射。

**改动：** 初版的 64 MiB Image 占用上限覆盖 `[0x80000000,0x84000000)`，
可能直接覆盖 `0x82b00000`。改为 32 MiB，内核占用结束不晚于 `0x82000000`，
距该指针保留 11 MiB 间隔；FIT 传输地址仍为 `0x88000000`，文件上限仍为 32 MiB。
这里限制的是包含 BSS 的 header image_size，不是只限制压缩后的文件。

依据 [U-Boot v2021.01 fdtdec.c](https://github.com/u-boot/u-boot/blob/v2021.01/lib/fdtdec.c)，
`multi_dtb_fit` 用于保存含多个 DTB 的 FIT 指针，`fdtdec_resetup()` 可再次读取它。
这是上游实现的行为；**尚未证明本机厂商 bootm 路径会再次访问该对象**。
收紧上限属于基于新证据的保守处理，不代表已复现覆盖故障。
没有修改 fdtcontroladdr、搬移原厂对象、写 NAND 或更换 U-Boot。

**下一步：** 获取 `help bootm`、`help tftpboot`、`help loady`，确认命令能力；
可用只读 `md.b 0x82b00000 0x28` 查看候选 FIT/FDT 头，检查 magic 和 totalsize。
随后核对编译机产物的 FIT 元数据、未压缩 Image header 与实际体积，再进行 RAM boot。
本轮数据没有证明网络收发、看门狗状态、DMA 停机或现代内核启动成功。

**验证：** 新增 multi_dtb_fit 覆盖回归用例；8 项 Python 测试通过，Git 空白检查通过。
未执行本地编译或设备写入。

## 2026-09-18：首次异机构建反馈，补齐 Kconfig

用户报告 `make -j20` 在 `target/linux compile` 失败；`make -j1 V=s` 进入
`SYNC include/config/auto.conf.cmd`、`Restart config...` 和 Kernel Features 问答。
日志尚未包含 C/汇编编译诊断，因此不能将其认定为驱动编译错误。

### 根因与修复

上一版只核对了配置符号是否存在，遗漏了“依赖满足后，可见选项是否有明确取值”的检查。
发行版 `include/kernel-build.mk` 为模块构建导出 `FAIL_ON_UNCONFIGURED=1`，
通用补丁 `205-kconfig-abort-configuration-on-unset-symbol.patch` 让无终端输入时的
未配置问答直接失败；有终端时可以看到问答。因而两个 make 命令的表现可以由同一缺项解释。

Linux `scripts/kconfig/conf.c` 的 `check_conf()` 遇到缺项后会重新进入其父菜单。
屏幕显示页大小、地址空间、CPU 数量等已有选项，不等于这些值全部丢失。
本次保留 4 KiB 页、39 位 VA、48 位 PA、小端、NR_CPUS=2、100 Hz 等既有设置。

本轮仅修改 `config-6.12`，没有改写公共构建流程或关闭未配置检查：

| 配置 | 明确取值及依据 |
|---|---|
| UNMAP_KERNEL_AT_EL0 | y，采用 ARM64 默认 KPTI 策略，由内核运行时判断适用性 |
| RODATA_FULL_DEFAULT_ENABLED | y，采用默认的只读映射权限策略 |
| ARM64_TAGGED_ADDR_ABI | y，采用默认用户态 tagged-address ABI |
| ARM64_PLATFORM_DEVICES | y，明确平台驱动菜单开关；该选项本身不添加板级驱动 |
| COMPAT_32BIT_TIME | n，与本 target 的 64 位用户态及 COMPAT=n 一致 |
| RPS / RFS_ACCEL / NET_FLOW_LIMIT | y，采用 SMP 网络栈默认值；不会因此产生硬件网口 |
| FRAME_WARN | 2048，采用 64 位内核默认栈帧警告阈值 |
| INITRAMFS_SOURCE | 空字符串作为基础值；生成 initramfs 时仍由现有流水线填入根目录 |
| HZ_PERIODIC / TICK_CPU_ACCOUNTING | y，显式选择本次基础配置下的默认计时/记账策略 |
| RANDSTRUCT_NONE / RANDSTRUCT_FULL | y / n，明确不随机化结构体布局 |
| CFI_CLANG | n，明确本次 GCC bring-up 不启用 Clang CFI |

### 验证与限制

用发行版自己的 `scripts/kconfig.pl` 合并 generic 与 target 配置，再对已打补丁的
Linux 6.12.103 Kconfig 源码进行解释式依赖分析。临时使用
[Kconfiglib 14.1.0](https://github.com/ulfalizer/Kconfiglib/tree/v14.1.0)，将新版
`modules` 声明在解析入口等价转换为其支持的 `option modules` 写法。

为遵守本地不编译要求，**拦截全部 Kconfig shell 调用，没有实际执行编译器、汇编器
或工具链能力探测**。按发行版默认 GCC 14.3.0 / binutils 2.44 建模，并分别用保守和
宽松的能力探测返回值检查可见性；这些返回值是静态分析假设，不是实际工具链测试结果。
修复后两组分析均未发现缺少取值的可见符号，也未发现缺少显式选择的可见 choice。
未把模型中的编译器版本、能力位或完整生成配置写入 target。

本地检查配置合并结果仍保留 UART、时钟、PSCI、GICv3，且 MTD/SPI/PCI/USB/watchdog
保持关闭；Git 空白检查通过。**未执行 make、内核配置工具的编译或固件编译**。
尚须用户在编译机重新执行 `make -j1 V=s target/linux/compile`，通过后再 `make -j20`。
后续如果出现 C/汇编错误，应依据新日志继续修复，不能把此次配置修正当作编译通过。

### 同次回报的 U-Boot 命令与对象头

现场 `help` 确认：

- `bootm` 支持 `addr#conf_uname`、`addr:subimg_uname`，并列出了分阶段子命令。
- `tftpboot [loadAddress] [[hostIPaddr:]bootfilename]` 可指定 RAM 地址。
- `loady [off] [baud]` 提供 YMODEM 接收。

`md.b 0x82b00000 0x28` 的 40 字节按大端 FDT header 解析为：

| 字段 | 值 |
|---|---|
| magic | `0xd00dfeed`，FDT 格式标识 |
| totalsize | `0x2c0`，704 字节 |
| off_dt_struct / off_dt_strings | `0x38` / `0x244` |
| off_mem_rsvmap | `0x28` |
| version / last_comp_version | 17 / 16 |
| boot_cpuid_phys | 0 |
| size_dt_strings / size_dt_struct | `0x7c` / `0x20c` |

结构区和字符串区的范围与 header 一致。但尚未读取节点：FDT magic 本身不能区分
普通 DTB 与 FIT，也不能排除 FIT 引用外部 payload。因此 704 字节不能当作整个
multi-DTB 对象连同数据的总占用，继续保留上一轮的 32 MiB Image 上限。
这些输出确认命令和头部信息，没有证明 TFTP/YMODEM 传输或新内核启动已成功。

### 后续每轮追加格式

```text
日期 / Git commit：
构建机系统、工具链、feed commits：
输入配置 / 固件 SHA256：
板卡硬件差异 / 原厂 U-Boot 版本：
本轮改动及依据：
测试步骤与完整日志位置：
观察到的问题 / 已排除原因：
下一轮改动及验收标准：
```
