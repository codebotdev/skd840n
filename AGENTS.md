# SK-D840N / ZX279133 开发指引

本仓库基于 ImmortalWrt v25.12.2，为 Skyworth SK-D840N（ZX279133）添加设备支持。
本文件提供项目入口和默认工作约定；用户后续的明确指示优先。

## 目标与当前阶段

- 第一阶段功能目标是四网口有线路由器，光口功能后续研究。
- 当前实现是原厂 U-Boot 下的 RAM bring-up：设备树、串口、时钟/reset 和 initramfs FIT。
  Ethernet、PON 和持久安装尚未实现，不要将其描述为已可用的四口路由器固件。
- 内核沿用发行版的 Linux 6.12.103。社区 ZX279133 支持是下游移植，不能称为已进入主线。
- 用户在另一台机器构建、在实机上测试。本地静态检查不能替代编译或启动验证；最新结果以研究记录为准。

## 先读这些文件

1. [研究记录](target/linux/zx279133/docs/RESEARCH.md)：硬件证据、来源版本、历次修改、问题和现场反馈。
2. [编译与启动说明](target/linux/zx279133/README.md)：异机构建、原厂 U-Boot RAM 启动、日志采集及故障处理。
3. [编译配置种子](target/linux/zx279133/docs/build.config)：顶层 `.config` 的起点，不是内核配置。

## 代码位置

全部设备适配集中在 `target/linux/zx279133/`：

| 路径 | 作用 |
|---|---|
| [Makefile](target/linux/zx279133/Makefile) | aarch64 / Cortex-A53 target、内核版本及默认软件包 |
| [generic/target.mk](target/linux/zx279133/generic/target.mk) | generic 子目标 |
| [config-6.12](target/linux/zx279133/config-6.12) | 与 generic 配置合并的内核配置片段 |
| [dts/](target/linux/zx279133/dts/) | ZX279133 最小 SoC 描述及 SK-D840N 板级 DTS |
| [files-6.12/](target/linux/zx279133/files-6.12/) | 内核新增文件：时钟/reset 驱动和 DT binding 头文件 |
| [patches-6.12/](target/linux/zx279133/patches-6.12/) | 时钟 Kconfig/Kbuild 接入、ZTE UART/earlycon 补丁 |
| [image/Makefile](target/linux/zx279133/image/Makefile) | 设备 profile 与 initramfs FIT 生成规则 |
| [image/check-image.py](target/linux/zx279133/image/check-image.py) | ARM64 Image header、内存占用与 FIT 大小检查 |
| [image/test_check_image.py](target/linux/zx279133/image/test_check_image.py) | 不涉及编译的合成镜像边界测试 |
| [base-files/lib/upgrade/platform.sh](target/linux/zx279133/base-files/lib/upgrade/platform.sh) | 拒绝普通及强制 sysupgrade 的写入路径 |
| [docs/SOURCES.sha256](target/linux/zx279133/docs/SOURCES.sha256) | 导入来源和适配文件的哈希记录 |

优先在此 target 内修改，避免无必要地影响其他平台。修改公共文件时说明原因和影响范围。

## 工作约定

- **不在本地编译。** 当前工作区不执行 make、defconfig、dtc 编译或编译器能力探测；
  需要构建的命令写给用户在编译机执行。不要因“只是检查配置”而间接启动编译器。
- 可以进行文件分析、配置合并、补丁应用检查、Python 测试、shell 语法检查和 Git 检查。
  使用模拟工具链能力的静态分析时，必须注明假设，不能报告为实际工具链验证通过。
- 不通过删除 `FAIL_ON_UNCONFIGURED`、自动回答所有问答等方式绕过配置缺项。
  `Restart config...` 应检查 generic/target/顶层配置合并结果及可见选项是否完整。
- 每轮实质修改或新的构建/实机反馈都追加到 `docs/RESEARCH.md`：依据、修改、问题、
  验证结果、未验证内容和下一步。启动或编译方法变更时同步 README；导入代码变更时维护来源和哈希。
- 按用户要求提交或推送；只纳入本次相关文件，保留用户改动，不强推或擅自改写历史。
  提交说明和最终回复明确区分“静态检查通过”“异机编译通过”“实机验证通过”。

## RAM 启动边界

- 保留原厂 U-Boot，不写 NAND、不改持久环境，不生成或启用未经验证的刷写升级流程。
- Image load/entry 为 `0x80000000`，header text_offset 必须为 0，含 BSS 占用上限为 32 MiB。
  FIT 传输地址为 `0x88000000`，文件上限为 32 MiB，配置名为 `conf@133`。
- 原厂 `multi_dtb_fit` 指针为 `0x82b00000`。其 header totalsize=704 不代表外部 payload 的总大小，
  不据此放宽 Image 上限。纯 FIT 启动不加原厂分区包装的 `0x1e0` 偏移。
- 原厂 `zxboot` 可能保存环境；具体启动流程见 README。不能把“不执行 saveenv”当作整个启动过程未写闪存的证明。
- 未描述外设节点不代表固件 DMA 已停机，关闭 Linux watchdog 驱动也不代表固件 watchdog 已关闭。
- 不直接复用 SR1010 的交换机/PHY 拓扑或旧内核模块。四网口映射、微码 ABI 和 PHY ID 需按本机证据验证。

## 常用本地检查

在仓库根目录执行，按修改范围选择：

```sh
PYTHONDONTWRITEBYTECODE=1 python3 target/linux/zx279133/image/test_check_image.py
sh -n target/linux/zx279133/base-files/lib/upgrade/platform.sh
git diff --check
git diff --cached --check
```

外部输入可能位于仓库的同级目录：`../skd840nbackup/`、`../SK-D840N-OpenWRT/`、
`../firmware_v2.0.zip`、`../bringup-planning/`。这些不是构建依赖，其他机器不一定具备。
保持原始备份不变，不将整片闪存、凭据、设备身份数据或构建产物提交到 Git。
