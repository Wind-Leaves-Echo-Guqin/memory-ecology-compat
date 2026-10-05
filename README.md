# Memory Ecology Compat · 记忆生态·兼容版

多宿主（multi-host）的 agent 记忆与经验生态。源自 [memory-ecology](https://github.com/Wind-Leaves-Echo-Guqin/memory-ecology)（Hermes 版），
本仓库把**可移植核心**与**宿主适配层**分离，让 Hermes 之外的 agent（当前：dsh；规划：任意 CLI/MCP 宿主）
复用同一套记忆生命周期治理与经验笔记本。

> ⚠️ **当前边界（先读这段再用）**
> 1. **检索是词面级的，不是语义检索**：默认后端 difflib（字符级子串 + 词面打分），召回依赖关键词选择。
>    本地 embedding（`bge-small-zh-v1.5` + CLS pooling，golden 182 对 AUC 0.93 vs difflib 0.90）为
>    **可选后端，尚未默认启用**——选型依据与启用方式见 [models/README.md](models/README.md)、
>    `lib/similarity.py` 与 ARCHITECTURE_TODO §三。
> 2. **经验笔记本是半自动线**：signals/merge/gold 只标记不判定，成熟/合并需人工兜底，
>    详见「系统构成：两条纪律线」。
> 3. **Status: 🚧 Work in Progress**（2026-10-04 全量审计后进入收敛期）；
>    已知问题与架构待办见 [ARCHITECTURE_TODO.md](ARCHITECTURE_TODO.md)。

> 派生声明：本项目为原 memory-ecology（Hermes 单宿主版）的兼容衍生线，独立仓库、独立演进；
> 自 v2.1.2 起**版本号同步**（当前 v2.2.5）；compat = 治理核心（四道门）＋检索消费端，
> 捕获链（信号/提取/回填）为宿主树专属，移植路线见 ARCHITECTURE_TODO.md 第四节；
> 本线只多出「多宿主化 + 观测舱 GUI」这一集成层。

## 系统构成：两条纪律线

本系统由两条设计哲学不同的线构成（此前 README 统称"四道门+经验笔记本"，易误读为同质系统）：

| | 自动治理线（四道门） | 半自动沉淀线（经验笔记本） |
|---|---|---|
| 目标 | 自动化运转（提升/挤出/蒸馏/复核） | 低置信度标记 + 人工兜底 |
| 追求 | 可逆、幂等、稳态（防震荡） | 可观测、可追溯、零误伤 |
| 边界 | 只出合并/复活**候选**，不自动裁决 | signals/merge/gold 均只标记不判定 |

**定位澄清**：`eco_health_check.py` 检查的是 skills/ 生态（frontmatter 合法性、引用图、cron 健康），
**不是**四道门的运行状态仪表盘；门级运行状态看各自 gate_log/distill_log/review_log 与 idle 心跳。
`memory_query.py` 是记忆（detail+archive）的检索入口。

## English TL;DR

Memory Ecology Compat is the **multi-host derivative** of
[memory-ecology](https://github.com/Wind-Leaves-Echo-Guqin/memory-ecology) — an agent memory/experience
lifecycle governance toolkit that treats memories as an ecosystem: bounded, alive, and always reversible
(four lifecycle gates + an experience notebook + quality gate evaluation, rule-first — LLM participates
only in gate-① similar/conflict adjudication and gate-③ wording, physical deletion disabled at code
level). The portable core (`src/memory_ecology`) has **zero host
dependencies**; host adapters live in `integrations/` (Hermes reference implementation, dsh adapters
for both the EAC Extension SDK and the official cordis-based desktop). Since v2.1.2 the two lines are
feature-identical and version-aligned — this repo only adds the multi-host layer.
**Honest boundary:** retrieval is lexical by default (difflib substring/word-overlap scoring — local
semantic embedding is an optional, not-yet-default backend), and the experience notebook only *flags*:
maturation and merging keep a human in the loop. MIT licensed.

## 兼容性说明（本仓库兼容了什么）

「兼容」的不是某一个产品，而是「任何 agent 宿主」——三层含义：

| 层 | 兼容对象 | 说明 |
|---|---|---|
| **可移植核心** | 任意能 spawn Python 子进程的宿主 | `src/memory_ecology`（四道门 + 经验笔记本 + 评测门禁）零宿主依赖，CLI / 脚本 / MCP 封装均可直接调用 |
| **Hermes 适配层** | 上游 [memory-ecology](https://github.com/Wind-Leaves-Echo-Guqin/memory-ecology) 的宿主（参考实现全套） | `integrations/hermes/`：state.db 会话增量提取、pre_llm_call 报错注入 hook、cron 健康告警、基因库快照 |
| **dsh 适配层** | dsh 双插件体系 | `integrations/dsh/`：`package/`＝EAC 变体（Extension SDK `provideContext`/`registerTool`）；`package-cordis/`＝官方桌面版（cordis patch 层 `agent/pre-step` 注入 + `ctx.tools.register`）——均只读 + fail-open，报错根因注入 + `eco_note_query` / `eco_note_error_query` 原生只读工具 |
| **观测舱 GUI** | 人类（本机浏览器 / pywebview 原生窗口） | `integrations/gui/`：本地可视化驾驶舱（只读观测 + 走确认闸门的写操作），数据根同上，仅绑 127.0.0.1 |
| **共享数据根** | 多宿主并存 | 多宿主经 `MEMORY_ECOLOGY_ROOT` 指向同一条数据根即可共享记忆与经验；**单写入方纪律**保证四道门只有一个调度器 |

**适配新宿主**：照 `integrations/` 现有模式写薄壳（每回合 hook → subprocess 调检索 CLI → 拼注入文本），
核心零改动；接入步骤见根 README「快速开始」与 `integrations/dsh/README.md`。

## 它解决什么问题

agent 的记忆通常"只进不出、写入靠自觉、超限不可见"。本生态用**四道门**治理记忆生命周期，
用**经验笔记本**沉淀"做事经验"（错误/成功/已验证链路）——规则驱动为主、可回滚：
四道门自动运转零维护，LLM 仅参与门①相似/矛盾终审与门③措辞，其余环节零 LLM、离线可用：

- 门① 写入整合（`write_gate.py`）：类型分型 + 相似合并 + 矛盾失效（superseded 进隔离区，永不物理删除；相似/矛盾判定 LLM 终审，规则兜底）
- 门② 巩固/配额（`eco_quota.py`）：L1 常驻层挤出/提升，配额恒有界（纯规则，零 LLM）
- 门③ 蒸馏（`distill_stage.py`）：L2 稳定事实 → 用户画像（规则判稳，LLM 只措辞）
- 门④ 复核（`eco_review.py`）：遗忘曲线，超期复核 → dormant → archive（可逆）
- 经验笔记本（`eco_note_*`）：信号捕获 → 候选区 → 按需检索（不常驻注入）→ 成熟蒸馏为技能（**只标记不判定**，成熟/合并人工兜底）

## 架构总览

```
宿主层    Hermes │ dsh(EAC / cordis) │ 观测舱 GUI │ …任何能 spawn Python 子进程的宿主
          integrations/ 薄适配层：只读检索 + 向 pending/ 追加候选（宿主不直接写记忆）
                           │
                           ▼
数据根    MEMORY_ECOLOGY_ROOT：memories/ · experiences/ · pending/ · 门日志
                           ▲
                           │ 读写仅限唯一调度器（单写入方纪律）
                           │
治理核心   src/memory_ecology（零宿主依赖，规则驱动为主）
          ├─ 四道门     ① 写入整合 → ② 巩固/配额 → ③ 蒸馏 → ④ 复核
          ├─ 经验笔记本  信号捕获 → 候选区 → 按需检索 → 人工兜底 → 技能
          └─ 检索消费端  memory_query / eco_note_query
```

## 和「向量 RAG / Mem0 式记忆」的区别

多数记忆方案回答的核心问题是「**怎么召回**」（embedding → 向量库 → 检索拼接）；本项目的重心在它
前面的一层：「**记忆的生命周期怎么治理**」——写入、合并、挤出、蒸馏、遗忘、复核，检索只是治理
结果的消费端。

| 维度 | 典型向量 RAG 记忆 | 本项目 |
|---|---|---|
| 增长模型 | 只进不出，越攒越脏 | 配额恒有界（门②）+ 遗忘曲线（门④） |
| 事实冲突 | 新旧并存，召回看运气 | 矛盾失效 superseded → 隔离区，历史可追溯 |
| 删除 | 物理删除或放任 | 代码层禁用物理删除，一切状态转换可逆 |
| 做事经验 | 与事实混在同一库 | 独立经验笔记本：按需检索、只标记不判定、人工兜底后技能化 |
| 依赖 | 通常绑定 embedding 服务 | 规则优先、离线可用；LLM 仅门①终审与门③措辞 |
| 宿主 | 单应用视角 | 共享数据根 + 单写入方纪律，多宿主并存 |

> 简化对比，仅供定位：各家方案均在演进，以官方文档为准。另注意本项目的检索当前为词面级
> （见顶部「当前边界」），召回质量不是它的卖点——生命周期治理才是。

## 仓库结构

```
src/memory_ecology/     可移植核心（Python ≥3.10，零宿主依赖）
  ├── 四道门 + eco_note 可移植件 + lib/（config/fs/llm）
  └── test_*.py（fixture 隔离测试）
integrations/hermes/    Hermes 参考集成层（state.db/cron 耦合件，适配器参考实现）
integrations/dsh/       dsh 适配器（package/＝EAC Extension SDK；package-cordis/＝官方桌面版 cordis）
integrations/gui/       观测舱 GUI（可选：人类宿主适配器，本地可视化驾驶舱）
```

## 快速开始

### 1) 只用核心 CLI（任何宿主/无宿主）

```bash
pip install pyyaml zstandard
cd src/memory_ecology
# 经验检索（只读）
python eco_note_query.py "关键词"
python eco_note_error_query.py "Traceback ... NameError ..."   # 按根因分层匹配
# 体检（只读报告；注意会把报告副本写到 ~/Desktop）
python eco_health_check.py
```

数据根默认 `<仓库根>/`（memories/、experiences/ 按需生成）；多实例/自定义位置设
`MEMORY_ECOLOGY_ROOT` 环境变量。LLM 相关脚本用 DeepSeek 兼容端点（key 从
`MEMORY_ECOLOGY_API_KEY` 或数据根 `.env` 读取），纯规则脚本零依赖可跑。

### 2) dsh 宿主

见 [integrations/dsh/README.md](integrations/dsh/README.md)：报错时按根因自动注入经验，
另注册两个原生查询工具。两个插件包对应 dsh 的两套插件体系：

- `package/`——EAC 变体（Extension SDK：`dsh plugin add` / `dev_inject_plugin`）
- `package-cordis/`——官方桌面版（cordis patch 层：`dsh plugin --profile desktop add <package-cordis>`，
  数据根经 profile 的 cordis.patch.yml 同 id 条目配 `memoryEcologyRoot`）

### 3) Hermes 宿主

`integrations/hermes/` 为参考实现（会话提取/注入 hook/cron 健康告警等），
展示如何把核心接到"有会话库和调度器的宿主"上。其他宿主照此模式写适配器。

### 4) 观测舱 GUI（可选，人类宿主）

<!-- 截图占位：截一张观测舱主界面存为 docs/gui-observatory.png 后取消下面两行注释
![观测舱 GUI](docs/gui-observatory.png)
-->

```bash
# 方式一：命令行启动（默认 127.0.0.1:8788，自动开窗口）
python integrations/gui/eco_gui.py
# 方式二：双击 integrations/gui/启动生态观测舱.cmd（相对路径，clone 到哪都能用）
#        或 启动生态观测舱.pyw（pythonw 直启，无控制台黑框）
# 桌面双击入口：一次性生成指向本机安装位置的快捷 cmd
python integrations/gui/install_desktop.py
# GUI 服务异常时的自救：双击 integrations/gui/生态急救箱.cmd（自检→修复→重启→开窗）
```

数据根默认自动探测（`MEMORY_ECOLOGY_ROOT` 可覆盖，与上面各宿主一致）；
检索 CLI 在发布树自动定位到 `src/memory_ecology/`。纯离线、仅绑 127.0.0.1、
零第三方前端依赖（星图 3D 视图用随包分发的 Three.js，懒加载，WebGL 不可用自动回退 2D）；
写操作全部走确认闸门并转交核心 CLI 执行。
Hermes 专属动作（cron 重跑等）在未安装 Hermes 的机器上会降级为"可复制命令"。

## 多宿主纪律（重要）

- **单写入方**：四道门/捕获管线只允许一个调度器（一个宿主）执行；
  其他宿主只读消费 + 向 `pending/` 追加带宿主前缀的候选（如 `dsh-<date>.md`）
- **共享数据根**：多宿主共用一条根时经 `MEMORY_ECOLOGY_ROOT` 指向同一路径；
  隔离模式则各配各的根
- 一切动作可回滚：数据根建议纳入 git（基因库模式），删除永远代码层禁用

## 测试

```bash
cd src/memory_ecology
python test_eco_gates.py              # 四门持久化回归
python test_eco_note_error_query_v2.py  # 根因分层回归
python test_eco_version.py            # 健康行口径回归

# dsh 适配器（cwd=integrations/dsh；v2.2.5 起三件）
cd ../../integrations/dsh
node test_smoke.js                    # EAC：注册面 / fail-open / 探针 reason 日志
node test_common.js                   # 子进程语义（退出码分档 / `--` 分隔 / 解释器绝对路径 / 最小环境）
node test_cordis.mjs                  # 官方桌面版：纯函数 + apply() 端到端（假宿主包 + 桩 Python）
python package/python/test_eco_note_dsh_context.py     # 会话提取→注入→冷却（legacy 日志格式）
python package/python/test_eco_note_dsh_context_v4.py  # v4 结构化扫描 / 会话精确匹配 / 状态分片 / 载荷卫生
```
（以上测试缺 python 或 zstandard 时会**显式失败**而不是静默跳过——它们是 v2.2.5 关键护栏。）

## License

MIT © 2026 Wind-Leaves-Echo-Guqin（沿用上游 memory-ecology 许可）
