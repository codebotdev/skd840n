# SK-D840N 闪存备份硬件证据（2026-09-18）

用户输入 `SK-D840N_whole_256M.bin`，268435456 字节。本轮只读分析，原文件未改动。
不提交整片备份、提取内核/DTB、配置、身份字段或凭据。

| 对象 | 位置/长度 | SHA256 |
|---|---|---|
| 主数据备份 | 256 MiB | `0d168f3691fe0fa0e5496b7cfa3163fc27c8d198a988a3d11f5c80fb4e367d64` |
| 两份完全相同的有效 FDT | `0x00698660`、`0x02698660`；各 49307 字节 | `99885f52b7271bda8944b1e30c065db1c91d0fc6f4e36e291dc469c3c08206af` |

通过 magic 定位并解析 FDT header、结构区、字符串区；未用 dtc、OCR 或编译器。
以下是原厂模板线索，不等同于 Linux 实际探测结果：

| 项目 | 证据和限制 |
|---|---|
| MDIO | `0x14f01000`、`0x14f02000`，各 4 KiB。两条总线 uni-phy 均列出 `0xa,0xb,0xc,0xd,0x5,0x1c,0x2`，只是候选地址。 |
| PHY | bdinfo 有 phy_RTL8226 字符串，不能据此确定真实 ID 或后缀，也不能确定物理端口。 |
| SPI | `0x10d0f000`，SPI 33，单线 TX/RX。模板 span 0x40000 覆盖 GPIO；社区寄存器实现仅到 0x38，本轮映射 4 KiB。 |
| NAND | ef aa 22 来自先前 RESEARCH 的本机原厂日志，不是从裸备份推导。本轮新驱动必须实际 READ ID 才放行对应特征配置。Linux 6.12.103 Winbond 表对应 W25N02KV。 |
| GPIO | bank 在 `0x10d10000/40/80/c0`、`0x10d10100`，SPI 34–38；pinmux 映射非平凡，不把全局编号当成 bank offset。 |
| LED/按键 | 多套 leds、leds_2、leds_5 等板型节点并存，不同时启用，不猜测 reset/power 接线。 |
| USB | 模板包含 `0x15008000` / SPI 63、`0x15010000` / SPI 65，不是本轮启用或实测结果。 |
| RAM | 原厂模板含 mem=1024M 等参数，不能照搬；本次实机日志明确为 512 MiB。 |

原分区模板含 boot、双 kernel、配置及业务区，U-Boot 还可能修正 DT。
不把模板当作已验证的安装布局，不猜测坏块、ECC/OOB 或原厂双系统升级规则。
新 profile 仅提供整片只读 MTD 视图，不启用 UBI 或持久 overlay。

控制面地址和 PHY 名称不能证明 Ethernet DMA 队列、IRQ、缓存一致性或微码 ABI。
不用光口也不证明原厂 PON/WOE DMA 已停止，故保留原内存预留。
后续采集仅需 PHY ID、MTD/ECC 状态和启动日志，不需要公开原厂凭据或整片内容。
