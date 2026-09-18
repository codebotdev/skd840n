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

## 2026-09-18：首次时钟驱动编译错误，修复 6.12 API 回移

用户新日志已进入 `CC drivers/clk/clk-zx279133.o`，本次没有再停在配置问答。
首个错误是 `devm_clk_hw_register_gate_parent_hw()` 隐式声明，随后七处调用均出现
int→pointer 错误。这里不能用强转或关闭警告处理；根因是初次从较新社区内核回移时
遗漏了一个 Linux 6.12.103 不提供的 API。此前的接口核对不完整，本轮纠正。

### 修改与语义核对

- 在 `files-6.12/drivers/clk/clk-zx279133.c` 增加局部辅助函数
  `zx279133_register_gate()`，用 `struct clk_parent_data.hw` 表达原来的父时钟，
  调用 6.12 已有的 `devm_clk_hw_register_gate_parent_data()`。
- 将缺失接口的七处调用全部替换为该辅助函数。父时钟、名字、flags、寄存器地址、
  bit index、gate flags、自旋锁参数均保持原值；没有改动时钟频率、门控策略或 DTS。
- 保留 devm 生命周期。对照 6.12.103 的 `drivers/clk/clk-gate.c`，确认该接口仍走
  `__devm_clk_hw_register_gate()`，注册失败和设备资源释放时均有对应清理。
- 对照 `drivers/clk/clk.c` 的 `clk_core_populate_parent_map()`，确认注册期间复制
  parent_data 的字段；局部 parent_data 的栈生命周期不会留给时钟核心悬空指针。
  index 显式设为 -1，与原 parent_hws 路径默认值一致；父时钟仍直接通过 hw 指针引用。
- 更新 `docs/SOURCES.sha256` 的本地适配文件哈希，保留社区原始输入哈希。
  README 增加此错误的处理方法，并将状态更新为“已开始异机构建，尚未完整通过”。

### 本轮验证

- 对七处调用逐一比较完整参数列表，仅更换调用函数，参数一致。
- 将驱动中用到的七种时钟 API 名称及每处调用的参数数量，与 6.12.103 的
  `include/linux/clk-provider.h` 声明/宏核对，全部匹配。该检查不等价于完整 C 类型检查。
- 时钟 C 文件通过 Linux checkpatch；来源哈希核对及 Git 空白检查通过。
- 未运行 make、C 编译器、预处理器或固件测试。尚须用户在编译机验证修复后的目标文件、
  完整内核和 FIT 镜像构建；此次反馈仅证明构建已走到时钟驱动这一阶段。

下一步：编译机同步提交，先 `make -j1 V=s target/linux/compile`，成功后 `make -j20`。
如仍有错误，继续收集第一处实际诊断，避免只保留末尾的递归 make Error 2。

## 2026-09-18：Image / DTB 已生成，修正 FIT 设备树路径

用户日志提供了新的异机验证结果：内嵌 initramfs 的内核完成链接并生成
`arch/arm64/boot/Image`；板级 DTS 完成预处理和 dtc，输出
`image-zx279133-skyworth-sk-d840n.dtb`。Image header/占用检查也已通过，随后执行 gzip。
失败发生在 FIT 打包：`mkits.sh -d` 得到了不存在的 `image-.dtb`，mkimage 因无法读取
该路径退出。本轮不是内核编译错误，也不是设备树语法错误。

### 原因与修改

`include/image.mk` 中的设备处理顺序是 Device/Init → Device/Default → 具体 Device。
Init 清空 DEVICE_DTS，板级 profile 稍后才将其设为 `zx279133-skyworth-sk-d840n`。
旧版在 Default 中使用立即赋值 `KERNEL_INITRAMFS := ... $$(DEVICE_DTS) ...`，
经 eval 解析赋值时，设备名仍为空，错误路径因此固化进流水线。

将 `image/Makefile` 中该赋值改为递归赋值 `KERNEL_INITRAMFS = ...`，保留变量的延迟
展开，让设备规则导出流水线时取得已经设置的 DEVICE_DTS；同时添加原因注释。
该模式与仓库中 microchipsw 等 FIT 目标的延迟展开写法一致。
没有修改 mkits.sh、公共镜像规则、DTS、加载地址或镜像大小限制。

### 验证与下一步

- 静态核对 Device 调用顺序、Device/ExportVar 和 Build/fit-its 的路径传递。
- 用 Python 对这条赋值及其先后顺序建立最小展开示例：旧版得到 `image-.dtb`，
  修正版得到 `image-zx279133-skyworth-sk-d840n.dtb`。这不是完整 GNU make 执行验证。
- 将示例从当前规则得出的 DTB 路径交给 mkits.sh，仅生成临时 ITS 文本，确认
  incbin 路径、conf@133 与 fdt@133 引用正确。未生成 FIT 二进制。
- Git 空白检查通过；未在本地运行 make、编译器、dtc 或 mkimage。

此前硬编码正确 DTB 路径的 ITS 检查不能覆盖 Makefile 变量展开时机的问题，
本次明确记录这一验证局限。用户同步修复后直接 `make -j1 V=s` 重试即可，
无需清理内核，也不通过手动生成 `image-.dtb` 绕过错误。
最终 FIT 成功生成、完整构建成功和实机启动仍待确认。

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

## 2026-09-18：基础 FIT 实机启动确认；隔离的只读 I/O 适配

开发基线：`5c6b70eba8ce5e464cdb633bde809084b7060934`。本轮用户提供完整串口日志和
256 MiB 主数据备份，要求继续适配、不需要光口。历史记录保留；本节更新最新状态。

### 日志结论

此前基础 FIT 已通过原厂 U-Boot 的 conf@133 校验、解压与 FDT 搬移，Linux 6.12.103
进入 ImmortalWrt 25.12.2 的串口 shell。日志显示 512 MiB RAM、PSCI、GICv3、
25 MHz timer，以及 earlycon 到 ttyAMA0 的正常切换。本轮附件从 bootm 开始，
不包含完整 TFTP 传输、构建过程或该 FIT 的 SHA256。

只有 CPU0 在线与 maxcpus=1 相符，不足以断言双核故障。
旧版没有 /proc/mtd、只有 lo，与关闭 MTD/SPI、缺少 Ethernet 数据面相符。
ip -br 不被该 BusyBox 支持，改用 ip link show；debugfs 已挂载，避免重复 mount。
/soc/bdinfo:vid_list 修正失败和 cacheinfo 警告未阻止此次启动；本轮没有伪造
板型字段、缓存参数来消除提示，也没有声称已经修复这两处警告。
固件 DMA 停机、watchdog handoff、SMP 和长期稳定性仍待实机确认。

### 实现及依据

新增 skyworth_sk-d840n-io profile、专用 DTS 和 build-io.config。保留基础 DTS、
原 FIT 布局、32 MiB 上限和升级写入拦截。两种 profile 共用新内核配置，
重新构建的基础镜像也不是此前已测试过的二进制，应保留旧 .itb 对照。

新增 MDIO 驱动只通过显式 sysfs 读取两条总线上原厂候选地址的 C22/C45 PHY ID。
不在 probe 自动枚举、不写 PHY 数据或外部 reset GPIO、不注册 Ethernet netdev。
新增 SFC 驱动采用单线、最高 25 MHz、PIO；整片分区只读，exec_op 还有不可配置的
命令白名单，拒绝 WREN、program、erase、OTP 和坏块 LUT 写入。
仅实际识别 ef aa 22 后允许 W25N02KV 的受限读取和必要易失特征配置。
SPI-NAND 探测仍需选择写缓存模板，因此 supports_op 描述传输能力，
写保护放在实际执行入口，避免只读芯片也探测失败。
RESET 保留已确认的不可变芯片身份，兼容 resume 不重读 ID 的配置流程。

两驱动均依赖原厂遗留 pinmux；MDIO 还依赖分频配置，不是完整冷启动初始化。
未导入 SR1010 交换机/PHY 拓扑、微码或旧模块。Ethernet MAC/NP/FPP/IDM DMA、
四口映射和 WAN/LAN 配置尚未实现。USB、GPIO/LED/按键、温控和 watchdog 尚未启用。
光口不在功能目标内，但未证明固件 DMA 停止之前仍保留 PON/WOE 内存预留。

新增 skd840n-diag，默认被动采集，--mdio 才读取候选 ID；不读取闪存内容或配置文件，
不自动挂载 debugfs、不进行任意 MMIO 扫描。详细构建、RAM 启动及验收步骤见
[IO-BRINGUP.md](IO-BRINGUP.md)，本机备份证据见 [FLASH-EVIDENCE.md](FLASH-EVIDENCE.md)，
来源与校验值见 [SOURCES-io.sha256](SOURCES-io.sha256)。
原始备份、提取的内核/DTB和设备身份数据不提交。

### 本轮验证与限制

9 项 Python 测试通过，包括从实际 C 白名单函数解析出的有限 AST 解释测试：
遍历不允许的 opcode、65536 个特征地址/值组合、未知芯片和页边界；
另检查保护先于硬件访问、MDIO 无写操作、profile/配置边界及 shell 参数。
5 项策略测试依赖 pycparser，缺少时明确 skipped，不得报告全套通过。
该模型使用范围内 Python 整数，不模拟完整 C 类型/溢出、SPI core、MMIO、中断和硬件。

新增补丁四个 hunk 对照已取得的 Linux 6.12.103 原始文本片段以零 fuzz 应用成功；
不代表已应用完整 ImmortalWrt 补丁链。shell 语法及 Git 空白检查通过。
备份大小和 SHA256 复核，原文件未修改。
本轮未执行 make、defconfig、C 预处理器/编译器、dtc、mkimage、完整配置求值或
新镜像实机启动，也没有重跑此前的 Image 边界测试。新增代码不是已验证固件。

下一步先在编译机构建 -io 镜像，RAM 启动后采集 PHY ID、MTD 几何/flags/ECC，
必要时只读第一页作哈希比对。再依据真实 PHY/SerDes/微码 ABI 实现单口 DMA 收发，
随后扩至四口。原厂/社区微码 CRC 不同不能证明绝对不兼容，也不能直接互换。
双核测试与新外设测试分开进行，不同时取消 maxcpus=1 和 clk_ignore_unused。

## 2026-09-18：cacheinfo 首核回退修复；MDIO 与 NAND 分阶段验证

开发基线：`65d9b2cf8eee351f207ef61cfc4c7be0367fe851`，
分支 `skd840n/readonly-io-20260918`。本次“继续”没有附带新的实机测试；
仍以此前基础镜像的成功启动日志为硬件证据，不把新增代码写成已验证功能。

### 问题与修改

核对 [Linux 6.12.103 cacheinfo.c](https://github.com/gregkh/linux/blob/v6.12.103/drivers/base/cacheinfo.c)
（Git blob `89410127089b934a4a6871c88cd1aef67846588d`）发现
cache_setup_properties 在 use_arch_cache_info() 成立时只设置 use_arch_info，
却仍返回 DT/ACPI 失败码，导致首核的 cache_shared_cpu_map_setup 提前退出。
补丁 `130-cacheinfo-complete-arch-fallback.patch` 在这一分支清零 ret；
未支持架构回退时仍返回错误，没有直接屏蔽 printk。

[ARM64 对应实现](https://github.com/gregkh/linux/blob/v6.12.103/arch/arm64/kernel/cacheinfo.c)
（Git blob `309942b06c5bc27628b0ff2d9d19e5db5b47afbd`）从 CLIDR_EL1 取得层级和类型。
同类返回路径修复的[内核邮件讨论](https://lists.openwall.net/linux-kernel/2026/06/11/980)
作为外部核对，不据此声称该修复已在所用稳定版本生效。本次补丁仅放入本 target，
不编造 cache-size/sets，不改硬件缓存控制；具体 sysfs 属性完整度仍需实机验证。

重新只读解析附件备份的有效 FDT：原厂 CPU 节点与当前最小 DTS 均无缓存参数，
原厂有单 cluster、两 core 的 cpu-map。未据此推导缓存容量、PHY ID 或双核已可用。
备份 SHA256 仍为 `0d168f3691fe0fa0e5496b7cfa3163fc27c8d198a988a3d11f5c80fb4e367d64`；
原文件不变，未提交原始 DTB、备份、身份信息或凭据。

新增 `skyworth_sk-d840n-mdio` profile 和配置种子。其 DTS 复用 I/O DTS，
再删除整个 spifc 子树及 spi0 别名，避免首次 PHY 识别同时依赖未验证的 NAND 驱动。
原基础和 -io profile 保留，三者共用内核配置及补丁；未改变 MDIO/SFC C 驱动、
配置片段、FIT 地址和 32 MiB 限制、内存预留、sysupgrade 拒绝路径。
这仅描述源码中的探测边界，不保证 U-Boot 更早阶段未访问闪存。

skd840n-diag 新增 --mdio-c22 / --mdio-c45，保留 --mdio 兼容行为。
默认仍不发 PHY 事务，新增缓存元数据采集；缺失可选字段不伪造。
请求的 MDIO 端点不存在、读取失败或结果包含 error= 时退出 1，并继续完成报告，
避免旧脚本将缺失端点或事务超时静默视为成功。退出 0 不是链路或数据面验收。

README 更新优先顺序，新操作说明见 [MDIO-BRINGUP.md](MDIO-BRINGUP.md)；
先基础对照，再 MDIO，最后独立 NAND。CPU1 试验另行使用基础镜像，只去掉 maxcpus=1。
bdinfo:vid_list 提示仍未修复，未用未知厂商字段消音；当前光口不在目标内。

### 验证与限制

新增 `tests/test_handoff.py` 的 15 项测试全部通过、无 skipped：
5 项解释实际补丁后 C 函数的 AST，2 项检查源码契约，8 项在假 sysroot 下测试 shell。
旧函数复现首核错误；新函数在 DT/ACPI 回退时返回 0，不支持回退时保留原错误。
默认/C22/C45/兼容双读、参数拒绝、缺失端点和 error=-110 的结果均有回归覆盖。

补丁对复制自 6.12.103 第 326–340 行的函数 fixture 零 fuzz、零 offset 应用；
不是完整内核或发行版补丁链验证。DTS 检查只是源文本约束，不是 dtc/DT ABI 验证。
shell 语法、改动文件哈希及 Git 空白检查通过；原 README/RESEARCH/manifest 和
本轮修改的原始代码副本均以 Git blob SHA 核对，研究记录只追加、历史不改写。

没有执行 make、defconfig、C 编译器/预处理器、dtc、mkimage、完整配置求值或实机启动。
没有重跑上轮 9 项 I/O 策略测试和旧 Image 边界测试，不将它们计入本轮通过数。
下一步由用户在编译机完整构建 -mdio FIT，记录提交和产物 SHA256，保持已验证 RAM
地址与单核参数启动，分别回传被动/C22/C45 日志。真实 PHY、MAC/SerDes 接线、
微码 ABI 与 DMA 收发仍未验证，Ethernet 四口数据面和持久安装仍未实现。

## 2026-09-18：MDIO 构建反馈，补齐 MTD_BLOCK_RO 的显式关闭值

基线：`6f46d7ed2367a89496bb6c994fa3e75786e195b4`。
用户提供 `build-skd840n-mdio-kernel.log`，SHA256：
`b337087d43532cf321377f84ca811a2f1702dcc9bf0eba9596e40325e4b11e24`。
只记录诊断与哈希，不提交含用户工作目录的完整原始日志。

### 本次异机日志证明的进度

278 个 generic backport、142 个 pending、58 个 hack 和本 target 的 100/110/120/130
四个补丁均走到应用完成，随后建立 .prepared 并安装用户态头文件。
在 Image/modules 阶段的 syncconfig 重新进入 MTD 菜单，首个失败点是：

```text
Readonly block device access to MTD devices (MTD_BLOCK_RO) [N/m/y/?] (NEW)
```

随后 syncconfig 退出，auto.conf.cmd 缺失与递归 make Error 2 是连带结果。
本日志没有新 MDIO/SFC 驱动的目标代码编译、内核链接、FIT 生成或新镜像启动证明。

### 根因及最小修复

generic/config-6.12 设置 MTD=y、MTD_BLOCK=y，没有 MTD_BLOCK_RO 的显式取值。
[Linux 6.12.103 drivers/mtd/Kconfig](https://github.com/gregkh/linux/blob/v6.12.103/drivers/mtd/Kconfig)
在 if MTD 内定义 MTD_BLOCK_RO，依赖 MTD_BLOCK!=y && BLOCK。
target 在上一轮启用 MTD 后仍关闭 MTD_BLOCK，使原本隐藏的替代块设备选项变得可见，
但漏掉了它的回答。这是 target 配置遗漏，不是工具链或新驱动 C 代码报错。

仅在 config-6.12 增加 `# CONFIG_MTD_BLOCK_RO is not set`。
不通过启用 MTD_BLOCK、关闭整个 MTD/SPI 或移除 FAIL_ON_UNCONFIGURED 绕过。
MTD_BLOCK_RO 是只读块设备接口，不是 NAND 只读保护总开关；现有 SFC 命令白名单、
只读分区与 MDIO-only DTS 不变。不修改公共配置、驱动、设备树、FIT 布局或启动参数。

复查已取得的 generic MTD/SPI 相关配置片段：软件 ECC、UBI、SPI slave/spidev 等
已有显式取值；本轮没有重新求值完整 Kconfig，也没有用户顶层 .config 的完整输入。
因此不承诺已经排除所有后续缺项。此前测试仅覆盖若干保护选项，未覆盖此隐藏转可见情形。

### 验证、文档与下一步

新增 tests/test_mtd_config.py 的 7 项标准库测试：显式关闭值、保留诊断核心配置、
区分缺失与 n、缩小的配置覆盖示例、拒绝启用替代块设备、重复符号及错误格式。
用原始 config-6.12 运行时，两处检查因 MTD_BLOCK_RO 缺失而失败；添加一行后 7 项通过，
没有跳过。覆盖示例只模拟赋值优先级并假设 BLOCK=y，不是完整 Kconfig 可见性求值。
Git 空白检查、已修改配置及新增测试的 SHA256 核对通过；保持旧来源条目不变。
更新 MDIO-BRINGUP.md 的重试说明和 SOURCES-io.sha256。

未在本地执行 make、defconfig、编译器/能力探测、dtc 或固件启动。
未重跑未修改的旧 I/O、handoff 与 Image 测试，不能把它们计入本轮通过数。
编译机保留个人 .config 和 feeds，同步后仅清理 target/linux 并重新编译；
无需重建工具链、重新初始化 feeds 或替换 U-Boot。编译/实机结果仍待用户回传。
