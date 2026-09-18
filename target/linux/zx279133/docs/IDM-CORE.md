# SK-D840N：原厂证据与 IDM 数据面基础代码

Continuation: [IDM RX transport and build integration](IDM-RX.md). The
original record below describes the preceding offline-only implementation.

## 当前交付边界

本轮基线为 `ca218d16a8560ec5961f63435132b59baae29b88`。以用户提供的
`skd840n-test.tar`、此前 256 MiB 闪存备份及固定版本社区源码为依据。
新增 `files-6.12/drivers/net/ethernet/zte/skd840n-idm-core.h`、离线解析工具和测试。
它们实现描述符 CPU 视图字段访问、队列布局规划、IRQ 分组与完成计数校验；
**未连接 Kbuild/Kconfig、未注册 netdev、未写寄存器、未启动 DMA。**
这不是可收发的 Ethernet 驱动，重新编译当前固件也不会因此出现 eth0。

当前可启动的基础、MDIO-only、I/O profile 和运行驱动保持不变。
不要为了本次离线基础代码重新编译、刷写或重复插拔四口。
新数据面开发单独保存在 `skd840n/ethernet-core-20260918`；
现有 `skd840n/readonly-io-20260918` 保持原提交作为对照。

## 1. 新采集材料实际支持什么

原始报告 `FACTORY-ETHERNET-RESEARCH.md` 的组织和结论没有改写；以下是针对代码的
交接摘录。完整输入及关键证据哈希见
[结构化来源和结果](factory-ethernet-20260918.json)，原始日志、DTB、模块和微码不入库。

压缩包 2232320 字节，SHA256
`be768b0721fb8c7ce709102bb66d8d3ff5b5a49915a2fc462afad54cf258638e`。
包内 SHA256SUMS 的 70 项全部匹配；这证明交付内容一致，不独立证明设备读数正确。
没有执行 collect.py 或 batch 命令，也没有连接原厂设备。
报告明确区分参数查询写入、可能读清的统计与未执行的操作入口；
不能把全部 sysfs 节点当成无副作用读取。

| 原厂证据 | 代码侧采用方式 |
|---|---|
| 用户确认 LAN4=eth3=SDK port3；SMAC3 计数与收发相符 | 首口开发基准由此前建议的 LAN2 改为证据更完整的 LAN4 |
| eth0/1/2/3 对应 SDK 4/1/2/3 | 保留软件映射；不把未实测的其余物理口全部补成确定 MAC 编号 |
| LAN4 查询为 1000Mbps/full；eth3 抓到 3 请求+3回复 | 仅作为原厂包级基线，不是新内核通过 |
| RX24、TX4、32字节描述符、深度1024 | 按实际几何建模，不照搬社区的默认单口/队列常量 |
| ARP/ICMP/DHCP 具名协议表指向 CPU qid6 | RX 掩码覆盖全部24队列，不只处理 q0；不声称所抓 ICMP 必经 q6 |
| 27项队列分到4个 IRQ组 | 组号不是 Linux CPU编号；GIC INTID33..36 对应 DTS SPI1..4 |
| NPPT/IDM 的原厂 DT status=disabled，但模块仍在收发 | disabled 不作为 DMA 已停机或不需初始化的证明 |

与此前逐口日志合并可把 LAN4 的 PHY `14f01000:0d / 84b95032`
与本次 SDK3/SMAC3 锚点关联。该关联跨两次同机采集，并非本轮原厂直接读取 PHY 得出。
接口模式仍未知；LAN2→eth1→SDK1/SMAC1 保留候选级别。
原 `port-map-20260918.json` 不被覆盖，保持各次会话证据的边界。

`sw` 上独立抓包只见 TX，但 ping 3/3 成功，不可将其当作无 RX。
抓包呈现98字节标准 Ethernet II，不证明 DMA buffer 没有私有 metadata。
原厂内存地址仅用于离线复核，不写入新 C 头文件或 DTS。

## 2. 本轮额外进行的备份二进制核对

新包没有模块函数体。本轮从此前备份的 FIT/initramfs 只读提取
`np_133.ko` 和 `mcode_133.bin` 到私有临时目录，未修改原备份。
备份 SHA256 仍为
`0d168f3691fe0fa0e5496b7cfa3163fc27c8d198a988a3d11f5c80fb4e367d64`。

模块 5208864 字节，SHA256
`732425ba1b0cf902af72402054f4f50628c487f5134f3f1e4647871646c897e1`。
新采集 kallsyms 中 770 个同名 text 函数相对该模块都得到相同加载偏移。
这是布局对应证据，**不等于运行模块每个字节已与备份比较**。
使用 ELF 符号/重定位、只读 AArch64 反汇编和打印格式串核对：

| 函数 | .text 偏移 | 长度 | 本轮用途 |
|---|---:|---:|---|
| dump_desc_rx | 0xf6d0 | 200 | RX CPU视图字段 |
| dump_desc_tx | 0xf798 | 252 | TX CPU视图字段 |
| idm_net_lan_tx_direct | 0x1196c | 1576 | 特定调试 TX 路径的16位完成计数回绕 |
| sw_port_change_to_smac_index | 0x120c44 | 172 | SDK0..6返回同编号SMAC；不推广到全部端口 |
| outport_and_real_port_map_table_set | 0xc05b8 | 304 | 表0x27的 real_port/real_queue 字段 |
| outport_and_real_port_map_table_init | 0xc06e8 | 348 | 仅识别初始化默认值，不冒充运行中的转发表 |

函数体 SHA256 保存在 JSON，可用相同私有模块及 llvm-objdump 按范围复核。
没有运行 C 预处理器、编译器、链接器或模块加载。

### CPU 数值视图字段

word0..word7 是原厂 dump 逐个打印的 **数值 u32**，不是未知字节序的原始 DMA bytes。
下面新恢复的位域属于本轮二进制分析，**不是新采集报告原本已经提供的结论**。

| 方向/word | 字段 |
|---|---|
| RX word0 | address 31:0；address_type 决定后续解释，不能直接当可解引用指针 |
| RX word1 | length13:0、addr_type14、omci15、source_port21:16、checksum_correct22、output28:23、reorder29 |
| RX word2 | gemport_llid_id15:0 |
| TX word0 | address31:0 |
| TX word1 | addr_type0、length14:1、checksum_en15、offset23:16、inport29:24、ipv4_ipv6_flag30、tcp_udp_flag31 |
| TX word2 | omci0、l3_offset7:1、l4_offset15:9、output_port21:16、output_port_valid22 |
| TX word6 | gemport_llid_id15:0、queue_id24:16 |

其余位保留为未分类；没有发明 owner、valid、RX reason、硬件队列字段。
TX word2 bit8、bits31:23 和 word6 bits31:25 尤其不能清零后假装完整可发包描述符。
特定调试发包路径会设置不属于上述命名字段的高位，因此不能只填写已解码字段就启动 TX。
保留光口字段的格式名称只是准确描述共享描述符，不启用光口功能。

表0x27中 real_port 占5:0，real_queue 占14:6（9位）。
它与 CPU RX qid、IDM TX ring 是不同编号空间。
默认初始化中的403、运行日志中的419都不能直接当 LAN4/其他面板口的发包队列。

## 3. C 辅助接口与错误行为

`get_field/set_field` 只接受8个word、合法连续字段；错误输入不更改输出，
写字段时保留全部其他位，拒绝超宽值。它不是完整 TX 描述符生成器。

`plan` 按原厂几何计算9段连续区域，总大小 `0xee5000`；
要求4KiB对齐、空间足够且所用范围在保守32位DMA边界内。
它不申请内存、不声明预留、更不复用原厂地址。
实际驱动仍应使用 DMA API 并核对硬件可达性、一致性和停机时序。

`irq_masks` 校验27个组值均为0..3，输出与原厂四组掩码一致；
`rx_events` 保留全部24个RX事件位，排除后三个free队列事件。
`route_word` 对 real_port/real_queue 作范围检查并保留其余位，仅操作内存中的表项。

`completed16` 对16位计数作回绕差分，拒绝超过软件拥有数量的完成数；
这是可复用算术保护，不是已经验证的正常TX队列回收协议。
调用者还须保证采样间隔内不会出现无法区分的多次整圈回绕、并确定计数单位。
没有软件拥有关系、缓存屏障和停机确认时，不得释放真实DMA缓冲区。

## 4. 对此前微码 CRC 顾虑的进一步限定

备份微码为103456字节，SHA256
`251acd2858aa1486dac66386842285a3e4aef55a5335891da5d595b937084db0`，
CRC32 `c839b946`。新采集提供运行版本 `0x0001.0x0108.0x31ba6a03`；
版本输出不能证明运行中的每个微码字节等于备份。

对照固定提交
[社区 np.c](https://github.com/cnjn/linux-mainline-zte-zxslc-sr1010/blob/07f8687248578d4be6931c665ff5d08bb6cc3d9d/drivers/net/ethernet/zte/zx279133-np.c)，
`ef1d7647` 与长度校验用于可选 RX-hash 注入的校准条件；不满足时日志为
保留原生 RX 队列，**不是一概拒绝其他基础微码的条件**。
本轮不将 `c839b946` 加入该注入白名单，不导入社区已修补程序，
也不把CRC不同当作整个数据面必然不兼容。仍须逐项确认 ABI。

## 5. 离线使用、测试与后续接入

在源码根目录运行，以下输入为**合成示例，不是真实捕获的DMA描述符**：

```sh
python3 target/linux/zx279133/tools/idm_core.py rx 90000000 07c30062 0 0 0 0 0 0
python3 target/linux/zx279133/tools/idm_core.py layout --dma-base 0x90000000 --allocated-bytes 0xee5000
PYTHONDONTWRITEBYTECODE=1 python3 target/linux/zx279133/tests/test_idm_core.py
```

示例仅输出 length=98、source_port=3 等字段，不访问所列地址。
工具不接受 /dev/mem、原始字节文件或自动字节序猜测。
布局的硬件 recv 对应 CPU TX，硬件 sent 对应 CPU RX，避免双向命名反用。

本轮20项测试通过，无跳过：11项解释实际C函数AST，9项标准库/CLI及证据一致性检查。
解释器对本次使用的u32/u64运算建模，包括无符号回绕、短路和数组边界；
未知语法直接报错。位域预期值另由反汇编转录成独立测试常量。
这不是完整C编译器、并发、ABI或硬件模型，也不能独立证明位域语义；
pycparser 缺失会明确跳过11项，不能报告全部通过。
无 make/defconfig、dtc、镜像构建或新硬件测试，未重跑未修改的历史测试。

下一步仍是 LAN4 的正常收发路径：先确认 RX 环数据转换、生产/消费指针和
安全停机，接入完整 TX metadata、微码装载/表初始化、MAC/SerDes 及 DMA API。
不能把本轮未接入的基础函数称为已经注册网卡或 ping 通。
无需再以相同目的采集PHY表；不混入 NAND、双核、光口或时钟策略改动。
