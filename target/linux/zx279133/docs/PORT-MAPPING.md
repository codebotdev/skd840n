# SK-D840N：四个面板网口的 PHY 映射

## 最新结果：2026-09-18，063327 日志

本机此次测试已把四个面板插座与 MDIO 地址对应起来；旧版“尚待测量”仅描述下面的
前次 ID 测试。用户明确说明在切换网口前输入了 lan1/lan2/lan3/lan4，故按这些标记
划分后续测试段，不把重复的 `CASE panel-label-1-only` 当成四次都测 lan1。
`-ash: lan1: not found` 等只是把标签当作命令后的 shell 提示，不是 PHY 驱动错误。

| 用户面板标记 | 用户速率标签（非协商实测） | MDIO MMIO | PHY 地址（十六进制） | PHY ID | 本段 C22 stat_now |
|---|---|---|---|---|---|
| lan1 | 2.5g | 14f02000 | 05 | 001cc849 | 79ad / up |
| lan2 | 1g | 14f01000 | 0b | 84b95032 | 796d / up |
| lan3 | 1g | 14f01000 | 0c | 84b95032 | 796d / up |
| lan4 | 1g | 14f01000 | 0d | 84b95032 | 796d / up |

初始 all-unplugged 的五个响应地址均 down；后续每个标签段恰有一个 C22 地址 up，
其他已响应地址均 down。切到后续插座时此前地址恢复 down。
这是一个会话、每标签一次快照的接线证据，不宣称已经完成多轮回插或其他板卡验证。
14f01000:0a 始终返回有效 ID 84b95032，但在五组快照中始终 down：
**保持未映射；不是 lan1，不认定为不存在、损坏、光口或地址别名。**

lan1 还存在需要保留的层级差异：

```text
C22         05 22 -1 001cc849 1040 79a9 79ad up ok
C45 PMA/PMD 05 45 1  001cc849 2058 0002 0006 up ok
C45 PCS     05 45 3  001cc849 205c 0582 0582 down ok
```

C22/PMA 的第二次读取显示 up；PCS 的 bit 2 仍未置位。
这些不是同时取得的全局快照。日志不足以确定差异来自协商时序、寄存器语义、
MAC/SerDes 配置还是其他原因；不把 PCS 改判 up，不据此宣称 SerDes 损坏或配置正确。
这也没有证明实际协商为 2.5 Gbit/s、速率匹配模式或端到端流量可用。

[结构化证据](port-map-20260918.json) 只供文档和离线一致性测试使用，不被启动脚本读取。
MAC 编号、SerDes 模式、实际协商速率、完整 FIT SHA256 和精确固件提交均保持 null，
不把 PHY 地址顺序当成 MAC 编号，也不从用户的面板 lan 标签自动设置 WAN/LAN。
新镜像的链路采集已有本次实机证据；MAC/NP/FPP/IDM DMA 数据面仍未实现。

## 标记测试：不需要为本次映射重新编译

当前已运行的固件即可继续使用。输入注释式标记或 echo，不要将 lan1 单独当作命令：

```sh
echo 'CASE lan1'
skd840n-port-map --c22; echo "c22_rc=$?"
skd840n-port-map --c45; echo "c45_rc=$?"
```

本次仅给用户态工具增加可选 `--label LABEL`，以后可以直接执行：

```sh
skd840n-port-map --c22 --label lan1
skd840n-port-map --c45 --label lan1
```

LABEL 长度为 1..48，首字符为 ASCII 字母或数字，其余仅允许字母、数字、点、下划线、
短横线。标签只是输出 CASE 行；不选择总线、PHY、路径或 Linux 接口，不改变硬件。
老的单参数调用输出和采集逻辑不变。新参数需要新版脚本，但不需要新内核；
未更新脚本的当前镜像继续用 echo 方式即可。没有要求为标签重做四口测试。

以下保留前次 ID 结果、链路采集边界及原构建流程，供后续需要修改内核时参考。

## 前次 053232 日志：ID 结果

用户新日志中 MDIO-only FIT 通过 U-Boot 校验并进入 ImmortalWrt shell，
LSP1 注册 12 个时钟，两条 MDIO 控制器 probe 成功。
clk_summary 显示两路 MDIO wclk 均为 2500000 Hz。
缓存目录已包含 L1 Data、L1 Instruction、L2 Unified，日志未再出现 cacheinfo 警告。
CPU0 在线仍与 maxcpus=1 一致；没有因此证明双核或长期稳定性。

| 总线 MMIO | PHY 地址（十六进制） | C22 ID | C45 ID |
|---|---|---|---|
| 14f01000 | 0a、0b、0c、0d | 84b95032 | MMD 1/3 均为 ffffffff |
| 14f02000 | 05 | 001cc849 | MMD 1/3 均为 001cc849 |

其他已查询候选地址返回 ffffffff，无 error= 事务错误。
这证明当前固件交接下的 ID 读取，而不是永久接线表。
Linux 6.12.103 realtek.c 将 001cc849 定义为 RTL_8221B_VB_CG，
匹配名为 RTL8221B-VB-CG；这是驱动匹配名，不是芯片丝印复核。
84b95032 暂按原始 ID 记录，不凭相同 ID 推导地址别名或准确型号。
五个有响应的地址不能直接当作五个外部网口；板上实际四口的对应关系尚待测量。

/proc/mtd 只有表头符合 MDIO-only DTS 删除 SFC/NAND 的设计。
ip link 只有 lo 符合尚无 MAC/DMA 数据面驱动的状态，不靠 UCI 配置生成硬件网口。
bdinfo:vid_list 提示仍未修复；保留 PON/WOE 预留，不加入光口功能。

## 本轮新增：显式链路快照

旧 phy_ids、phy_ids_c45 的格式及 skd840n-diag 默认行为保持不变。
新增只读 sysfs：phy_links（C22）与 phy_links_c45（原生 C45 MMD 1/3）。
读取标准寄存器 2/3 确认 ID 后，才读取 control 0、status 1 两次；
采样后再次核对 ID。仅对上表已见的 ID/协议组合放行状态读取。
仍使用已有候选地址表；没有全地址扫描、厂商页切换、间接 MMD 写入、
外部 PHY reset、自动协商重启、PHY 数据写或 probe-time PHY 事务。
控制器本身的时钟/reset 和事务控制写入仍存在，不能称为完全没有寄存器写。

表格字段：

```text
candidate clause mmd phy_id ctrl stat_first stat_now link result
```

地址为十六进制，clause/mmd 为十进制。C22 的 mmd=-1。
stat_first 保留首次读到的锁存状态，stat_now 是第二次读值；link 使用 stat_now 的 bit 2。
读取本身会消费 latched-low 状态，不能同时把它当成无副作用的链路事件记录器。
C45 的 PMA/PMD 与 PCS 分别输出，不把其中一条自动等同于端到端链路。
ctrl 原值保留，不从自动协商使能时的 BMCR 推断实际协商速率、双工或 MAC 接口。
up 仅表示该状态位；不表示可以 ping、转发或 DMA 已正常。

不支持的 ID 不读取状态；全 ffff 状态、身份变化或事务失败均输出 unknown/error，
不能将 ffff 的 bit 2 当作 link-up。no-id 不是事务超时。
同一总线的 ID、状态两读及身份复核由 mutex 串行化；不同 PHY 行不是同一时刻的全局快照。
新工具 skd840n-port-map 只打印这些表，必须显式选择 --c22 或 --c45。
接口缺失、读取失败、坏表或 error=/unsupported-id 返回 1；0 只表示采集成功。
不自动循环、不挂载文件系统、不读闪存内容或本机配置。

## 构建与启动

保留本次已启动的旧 MDIO-only ITB，不覆盖唯一副本。
在现有分支、配置和 feeds 上同步；本地无未处理冲突时，在编译机 Bash 执行：

```bash
(
    set -e
    set -o pipefail
    git fetch origin skd840n/readonly-io-20260918
    git switch skd840n/readonly-io-20260918
    git merge --ff-only origin/skd840n/readonly-io-20260918
    git log -1 --oneline
    make target/linux/clean
    make -j1 V=s target/linux/compile 2>&1 | tee build-skd840n-links-kernel.log
    make -j"$(nproc)" V=s 2>&1 | tee build-skd840n-links.log
)
```

只清理内核以重新复制 overlay，不重建工具链、不重置 .config、不修改 Kconfig 检查。
产物仍是 immortalwrt-zx279133-generic-skyworth_sk-d840n-mdio-initramfs-fit.itb；
不新增 profile。记录完整 FIT 的 SHA256、源码提交及 dumpimage -l 输出。
RAM 启动完全沿用 MDIO-BRINGUP.md 已验证的地址/参数：0x88000000、conf@133、
load/entry 0x80000000、maxcpus=1、clk_ignore_unused。
不修改持久环境，不写 NAND，不启动 zxboot，不取消未知 DMA 内存预留。

## 逐口测量

先完成 RAM 启动，再拔掉全部 RJ45 网线，保持 TTL 串口连接：
不要在 TFTP 尚未完成时拔线。然后执行：

```sh
echo 'CASE all-unplugged'
skd840n-port-map --c22; echo "c22_rc=$?"
skd840n-port-map --c45; echo "c45_rc=$?"
```

使用同一根已知良好网线和同一个开启的电脑/交换机网口，一次只插一个插座。
等待对端完成链路协商后，按面板原始标签记录，不先叫 WAN/LAN：

```sh
echo 'CASE panel-label-1-only'
skd840n-port-map --c22; echo "c22_rc=$?"
skd840n-port-map --c45; echo "c45_rc=$?"
```

每个插座都重复“仅插此口 → 全拔”并采集两组，确认状态变化能重复出现。
只在一根线变化时某一地址随之变化，才能建立该次测试的 PHY-插座对应证据。
某个地址始终 down、多个地址一起变化或没有变化时保留原始表；
不要通过写 BMCR、重置 PHY/GPIO、换微码或刷 NAND 来强行制造 link-up。
回传面板标签、各次表格、退出码、对端协商速率（如可见）及固件 SHA256。
不需要公开原厂配置、MAC、序列号或闪存内容。

## 仍未完成

还需将已测 PHY 与本机 MAC/SerDes 对齐，再实现 NP/FPP/IDM 的单口 DMA 收发、
缓存一致性及中断处理，之后扩展至四口和 WAN/LAN 配置。
本次没有把参考板的交换机拓扑或未经匹配的微码写入设备。
物理链路与网络数据面分开验收；不因 PHY ID 成功宣称四口路由器已完成。
