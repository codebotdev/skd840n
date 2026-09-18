# SK-D840N 适配研究与修改记录

## 历史记录

[截至 9091062 的完整历史记录](RESEARCH-20260918-HISTORY.md) 原样分卷保留，
包括首次 RAM 启动、配置/API/FIT 修正、只读 I/O、cacheinfo、MTD 配置与链接问题。
归档复用原 RESEARCH.md 的 Git blob：
`faf0fb8a91545a90bcaf6f9c0e5d8e70451d7bf6`，不删改历史正文，
且保持同目录，原有相对来源链接仍有效。以下继续记录新结果。

## 2026-09-18：MDIO-only 实机成功；加入只读链路采样

开发基线 `90910628261ee049a8118fccb9fa0f6dc0624bb5`。
用户新附件为粘贴的串口文本（文件名时间 053232），88807 字节，
SHA256 `19bd508fbea38db80343828ede58b74bc1b0db9f2f4d655677cc7ee388d87e85`。
原始日志和闪存备份不提交；本轮没有重做备份解析。

### 实机证据与限度

FIT 的 kernel@1 和 fdt@133 校验成功，MDIO-only 板型进入 shell。
压缩内核 payload 8028322 字节、CRC32 c8ceb9eb、SHA1
468ba0a7490f15166b27db23803847a44a7dea6e；DTB 为 3576 字节、CRC32 737a8b59、
SHA1 6a22e3ba1e0579e437b8c62b1acbc1b6376899db。
这些是子镜像字段，不是完整 FIT 的尺寸或 SHA256。日志没有精确源码提交号；
不据此断言附带完整构建过程或对本分支每个提交做了独立验证。

缓存警告未再出现，CPU0 暴露 L1 Data/Instruction 和 L2 Unified；
没有容量字段，不填造容量。仍为 maxcpus=1，未证明 SMP 或长期稳定性。
两条 MDIO probe 成功，wclk 均 2500000 Hz：
总线 14f01000 的 C22 地址 0a/0b/0c/0d 返回 84b95032；
总线 14f02000 的地址 05，在 C22 与 C45 MMD 1/3 均返回 001cc849。
其他候选为 ffffffff；已给出的表中没有 error=。
这确认原生 C45 在已知响应地址工作，不将第一条总线的 C45 no-id 当成总线失败。

Linux 6.12.103 realtek.c 将 001cc849 匹配到 RTL8221B-VB-CG；
不沿用仅凭旧字符串猜测的 RTL8226 后缀，也不把驱动匹配名说成丝印复核。
84b95032 保留原始值；五个响应地址与四个物理插座的关系尚未测量。
/proc/mtd 只有表头，ip link 只有 lo，与 MDIO-only 边界和缺少数据面相符。
bdinfo:vid_list 提示依然存在，不伪造字段；不改变 PON/WOE 预留。

### 修改

MDIO 驱动保留原 ID sysfs ABI、probe、时钟/reset 与事务协议。
底层读取限制为标准寄存器 0..3，C22 或原生 C45 MMD 1/3。
新增 phy_links/phy_links_c45：见到支持 ID 后才读取 control、两次 status，
随后复核身份。保留锁存和当前两个值，用第二次的 bit 2 输出状态。
全 ffff 状态返回 ENODATA、身份变化返回 ESTALE，其他错误保留，
不输出假的 up。采样持有同一 mutex；未知 ID 不发状态读取。
读取 status 会消费锁存，不能称为没有副作用；没有 PHY 数据写、
厂商页切换、自动协商配置、外部 PHY reset 或默认/后台轮询。

新增 skd840n-port-map，显式 --c22/--c45 一次采样，只输出短表。
旧 skd840n-diag 完全不变。操作步骤与结果表见 PORT-MAPPING.md。
没有启用 phylib 自动枚举或 MAC/DMA，也没有修改 SFC、DTS、配置、FIT 或升级拒绝入口。
历史 RESEARCH 原 blob 分卷保留，README 更新真实验证阶段；
SOURCES-io 中仅刷新改变的 MDIO 哈希，新增文件校验与来源见 SOURCES-phy-link.sha256。

### 本轮验证与下一步

18 项测试通过、无跳过：8 项用 pycparser 解释实际 C 辅助函数，
2 项静态约束检查，8 项隔离假 sysroot 的 shell 测试。
覆盖 ID 白名单、锁存低后当前高、当前低、C45 MMD、零状态、
全 ffff、各步错误短路、ID 变化、命令参数和错误退出。
解释器不是完整 C 类型/溢出模型，不执行真实 MMIO/中断、内核 sysfs 或硬件。
pycparser 缺失会跳过 8 项，不可把 skipped 计成通过。
shell 语法和 Git 空白检查通过；原 MDIO、README、来源清单均以旧 blob SHA 核对后修改。
未重新运行未修改的旧测试，也没有在本地执行 make、编译器、预处理器、dtc 或镜像生成。
本轮新链路采样仍须异机编译与实机验证，不用原 ID 读取成功替代它的验收。

下一步保留已能启动的 MDIO-only FIT，构建同 profile 的新镜像，
在 RAM 下按面板标签逐口插拔并采集 C22/C45 短表，确定可复现的 PHY-插座关系。
不同时测试 NAND/SMP，不取消 clk_ignore_unused；随后对接本机 MAC/SerDes 与单口 DMA。
Ethernet 四口数据收发及持久安装仍未实现，光口明确不在目标内。

## 2026-09-18：lan1..lan4 标签确认物理 PHY 对应

开发基线 `265f872832cde706265e4f1fce029dfc5fa7a929`。
用户附件文件名时间 063327，148360 字节，SHA256：
`f67d653d49eee5850c9ff7eb0c8660b2e2abafbcef8f9a73ad99dbc970d51117`。
用户说明在换网口前输入 lan1/lan2/lan3/lan4，按该标记而非重复的
CASE panel-label-1-only 划分后续段。shell 的 not found 不算作驱动错误。
本轮只读处理此附件，未重读或改写闪存备份，不提交原始串口日志。

### 已观察结果

初始全拔及四个标签段共五组，每组 C22 14 行、C45 28 行，共 210 行；
十次工具退出码均为 0。初始五个响应地址均 down，之后每段唯一 C22 up：
lan1=14f02000:05 / 001cc849，lan2=14f01000:0b / 84b95032，
lan3=14f01000:0c / 84b95032，lan4=14f01000:0d / 84b95032。
切换到后续口时此前地址恢复 down；未提供每口多轮回插，不能写成重复验证完成。
14f01000:0a 五组均有有效 ID 且 down，保留未知归属，不删除或分配为 lan1。

lan1 的 C22 79a9->79ad、PMA 0002->0006 表示第二次读为 up；
PCS 0582->0582 的 bit 2 为 0，仍报告 down。保留这一跨层级/采样时间差异，
不从单次状态推断 MAC/SerDes 模式、损坏或根因。
2.5g/1g 是用户标签，不是本次测出的协商速率。
对应完整值、边界与结构化摘录见 PORT-MAPPING.md、port-map-20260918.json。

MDIO-only FIT 经 U-Boot 校验进入 shell，新链路采集已有实机证据。
kernel payload 为 8030572 字节、CRC32 1abd5d94、SHA1
1ad64a9220caf7d56aa2d58bd0482b7b1fcf6341；DTB 3576 字节、CRC32 737a8b59。
它们不是完整 FIT 的尺寸或 SHA256；精确源码提交未在附件给出。
只有 lo 仍符合缺少 MAC/DMA 驱动的状态，未证明数据面、SMP 或长期稳定性。

### 修改与验证

仅修改文档、结构化证据、来源清单和 skd840n-port-map 用户态参数处理。
增加可选 --label LABEL（1..48 ASCII 安全字符），只打印 CASE 行，不选择 PHY
或路径；原单参数调用与整段采集代码保持不变。非法参数在任何采集前退出 2。
不修改任何内核驱动、DTS、内核配置、FIT、NAND 保护或 WAN/LAN 设置。
现有镜像不必重编，继续用 echo 'CASE lan1' 即可；新增标签只需新版脚本。

新增 tests/test_port_labels.py 的 16 项标准库测试通过，无跳过。
12 项在假 sysroot 下验证 shell 参数、标签边界、只加 CASE、不改变读取路径、
保留错误/PCS 差异；4 项检查结构化证据内部一致性，并非重复硬件测试。
原脚本在新的标签用例中按预期失败，新脚本通过。shell 语法与 Git 空白检查通过；
原五个修改文件先核对远端 Git blob，变更文件 SHA256 已更新。
未执行 make、编译器、预处理器、dtc 或本轮新实机测试；旧 C 解释测试未重跑。
下一开发阶段是 MAC/SerDes 与 NP/FPP/IDM 单口收发，不再把此附件当作 PHY 归属未知；
不猜测 MAC 编号或微码 ABI，光口仍排除在目标外。
