# ARCHITECTURE_TODO — 架构待办与已知裂缝清单（2026-10-04 全量审计产出）

> 目的：让债可见。这是"从个人可用到开源可维护"最重要的一步——不是修完所有债，而是不再隐藏。
> 产出背景：2026-10-04 两轮 AI 评审 + 人工核对 + 三路深扫，全部发现已修复或登记于此。

## 〇-bis、v2.4.0 更新销账（2026-10-07，外部调研落地第二期）

1. **已落地**：S6 向量缓存 shadow index（lib/vector_cache.py，条目级 content-hash 增量 + 换模型整库失效 +
   「缓存=可丢弃派生物」心智模型）+ memory_query `--semantic` 灰度路（embedding 不可用自动回退词面）；
   S7 OR 召回（or_recall：任一 term 命中，补多词 AND 缺口）+ RRF 融合（rrf_fuse，K=60）+
   `--recall` 开关（**默认关闭=行为锁定**）。
2. **默认化决策**：embedding 后端与 OR 召回**均未切默认**——按 §二.4 观察期纪律，先由 S4 零结果率观测
   与实跑对比攒数据；`--semantic`/`--recall` 为用户显式启用入口。
3. **§三 检索层结论更新（FTS5 实测证伪，重要）**：原计划 FTS5 双路三配置实测均不适合本项目中英语料——
   ① porter 是英文词干器，中文零收益；② FTS5 trigram 分词器要求查询 ≥3 字符，而中文常用词恰是 2 字
   （「备份」「框架」「迁移」全零命中）；③ unicode61 与现有子串路结果高度重合。故第二路落为**零依赖
   OR 召回 + RRF**（无需索引、离线可用、无新依赖）。**FTS5 登记为 >1000 条的规模化路径**——
   届时若上 FTS5，应选 unicode61 + 中文分词预处理（或 jieba 类），而非 trigram；且必须先过
   golden 对比（纪律同 embedding）。
4. **观察期后候选动作**（登记，不在本轮）：① 零结果率连续 2 周 >30% → 评估 `--recall` 默认化；
   ② `--semantic` 实跑对比 difflib 的排序差异 → 决定 embedding 默认化。

## 〇、v2.3.0 更新销账（2026-10-07，外部调研落地第一期）

外部记忆系统调研（20+ 项目，`~/.memory-ecology/designs/外部记忆系统调研-v1.0.md`）落地：

1. **已落地**：S1 时间维检索（衰减三档半连续 + 发生时间过滤——首次消费 valid_time/transaction_time 双时态）；
   S2 失效带理由（superseded_reason/superseded_at，隔离区变历史时间轴）；
   S3 证据链（CONFLICT 新旧互指 evidence/superseded_by + 门③ distilled_at 蒸馏状态位可逆重蒸馏）；
   S4 零结果率观测（eco_retrieval_report + GUI 检索质量页签）。
2. **§二.4（embedding 默认化）进展**：在线观测工具就绪（零结果率 = 换内核证据）；
   v2.4.0 计划落 SQLite 向量缓存 shadow index + 灰度开关，观察 1-2 周对比数据后再定默认。
3. **§4.1 双树漂移口径更新**：sync_trees_check 已行尾归一化（CRLF 幻影漂移 34→16）；
   剩余 16 处为**真实内容漂移**（live/兼容版领先：GUI/first_aid/.agent-index 等），维持「不盲同步」纪律，
   逐文件人工确认哪侧为准后回迁；本轮只选择性同步实际改动文件（memory_query/write_gate/distill_stage/
   lib/safeio + 新增测试与观测脚本）。
4. **已回迁**：live 2026-10-05 蒸馏三修复（STABLE_DAYS/预算阶梯/批量 env）进 compat+兼容版；
   live eco_git_commit .lock 排除进兼容版。distill_stage/write_gate/eco_review 三文件经回迁后内容对齐。

## 一、五类"单源"裂缝（修复状态）

| 类型 | 定义 | 案例 | 状态 |
|---|---|---|---|
| A 功能对等、语义不同 | 两份实现都能跑但结论不同 | 门② vs memstore 的 detail_prefixes/is_same_source | ✅ 已收编（memstore 对齐门②生产语义：active-only + 单向包含） |
| B 公共库落后、本地更强 | 强行统一会丢能力 | lib/llm vs eco_note.llm_extract | ✅ 已升级 lib/llm.complete_json（重试/sentinel/三级 JSON 提取/usage）→ eco_note/eco_extract 收编 |
| C 语义模糊、都不完整 | 三处各自实现同一机制 | 水位线推进（signals/extract/note） | ✅ 已抽 lib/watermark.py（(timestamp,id) 复合键，同 ts 截断跳窗根治；旧标量格式兼容读取） |
| D 双源定义、互相矛盾 | 两个模块各自定义同一矩阵 | eco_state.VALID vs eco_health_check.LEGAL_MATRIX（candidate/retained） | ⏳ 未修：一行改动 `from eco_state import VALID`，待与 SKILL.md 实际数据核对后执行 |
| E 最弱版本拖垮整体 | 5+ 套 frontmatter 解析并存，最弱的不兼容 CRLF | eco_state（已修）/safeio/eco_health_check(PyYAML)/eco_eval/eco_note_index | ⏳ 部分修：CRLF 已修；完全收敛到 memstore 宽松/严格两版待做（eco_eval/eco_state/eco_health_check/eco_note_index 四处） |

## 二、"未来会统一"登记（4+ 处）

1. **inject_gate**：drop 灰度只对「occurrences==1 且 last_seen>60d」生效；经验线仅规则 2/5 在跑
   （occurrences/last_seen/quarantine/hit_count_30d 未传）——遥测字段上线后补全。
2. **eco_note_backfill**：`--out` 并行模式（cand-<session>.md 防写竞态）仅工具支持，主流程未并行化。
3. **eco_version**：v3.0.0 于架构升级完成时统一切换并移居 lib。
4. **lib/similarity**：simhash 待实现（原计划的 L2，现仅 difflib/ngram/embedding 三后端）；
   L3 embedding 选型已定（bge-small-zh-v1.5+CLS，见下节），**默认化待定**——需先在生产跑一段
   观察期（对比 difflib 的 UPDATE/NOOP 判定差异），确认无回归再切默认。
5. **eco_note_backfill**：`--max-cands` 是**每会话**限额（2026-09-03 设计变更），docstring 已声明；全局限额与并行调度待重设计。

## 三、相似度/语义层路线（必须离线可用）

- **当前**：lib/similarity.py 统一层，默认后端 difflib（行为锁定：门① SIM_NEAR=0.80 / 门④ 0.70 阈值语义不漂移）。
- **L1 ngram**（纯 stdlib）：已实现。golden v1（182 对）评测：FPR=0 下可回收 43/99 same（最优阈值 0.58）。
- **L3 本地 embedding**：**已落地并选定模型（2026-10-04）**。
  - **选型：`bge-small-zh-v1.5` + CLS pooling**（`MEMORY_ECOLOGY_EMBED_POOLING=cls`）。
    实测（golden 182 对：99 same / 32 similar / 51 distinct）：
    | 后端 | AUC(same\|sim+dis) | AUC(hard\|sim+dis) | 误合并≤10% 时 same 召回 | 耗时 | 体积 |
    |---|---|---|---|---|---|
    | difflib（旧默认） | 0.901 | 0.798 | 54% | ~0.1s | — |
    | ngram | 0.901 | 0.798 | 64% | ~0.1s | — |
    | **bge-small-zh-v1.5[cls]** | **0.931** | **0.858** | **74%** | **5s** | **95MB** |
    | bge-base-zh-v1.5[cls] | 0.914 | 0.831 | 59% | 27s | 407MB |
    | bge-large-zh-v1.5[cls] | 0.926 | 0.841 | 71% | 102s | 1.3GB |
    - **CLS > mean**：三个 bge 模型上 CLS 的 AUC/hard 召回一致更高（BGE 官方推荐，实测印证）。
    - **small 够用**：bge-small 不逊于 base/large 且快 5~20×、小 4~14×；8GB 卡/离线约束下无理由上大模型。
    - **text2vec-base-chinese-paraphrase 淘汰**：分数压缩到 0.98+，判别力尽失（FPR=0 只回收 5/99）。
  - **阈值迁移**：门阈值不再硬编码，统一经 `similarity.gate_threshold(role)` 按生效后端解析
    （`GATE_THRESHOLDS`：suspect / merge / noop / candidate）。迁移原则 = 保持旧 difflib 阈值的
    **操作点**（捕获率/误合并率）不变，只换刻度，杜绝"换内核忘改阈值"的静默漂移。
    embedding 口径：`suspect=0.76 / merge=0.84 / noop=0.97 / candidate=0.88`。
    配置 embedding 但模型缺失时，阈值自动跟随回退后的 ngram（`effective_backend()`），避免
    "阈值按 embedding 校准、分数来自 ngram"的错配。可用 `MEMORY_ECOLOGY_SIM_THRESHOLD_<ROLE>` 显式覆盖。
  - **启用方式**：`MEMORY_ECOLOGY_SIM_BACKEND=embedding` + 模型拉取（`fetch_embed_models.py`，
    走 hf-mirror；模型不入库，见 models/README.md）。生产解释器 hermes-agent/venv 已含 onnxruntime/numpy/tokenizers。
- **评测标尺**：`python eco_sim_eval.py`（golden/similarity_golden.json，**v1-2026-10-04：182 对**，
  150 对生产语料人工标注 + 30 随机负样本 + 2 超短串钉死）。对比脚本：
  `compare_embed_models.py`（多模型×pooling）、`tradeoff_table.py`（误合并预算下召回）、
  `calibrate_thresholds.py` / `migrate_thresholds.py`（阈值迁移）、`analyze_discriminative.py`（难例分层）。
  - **已知边界**：即便 embedding，hard_same（difflib<0.5 的语义改写）在 similar 误合并 ≤6% 约束下
    召回仅 ~30%——**语义层解决"字符级必漏"，但"same vs similar"的细粒度边界仍需 LLM 终审**
    （门①已有 LLM 决策路径，规则仅兜底，与此结论一致）。
- **自研/微调（Phase 6，远期）**：触发线不变（golden>500 对或最佳现成模型 recall<90%）。
  **当前判断：不需要微调**——bge-small 在 golden 上已达 AUC 0.93，瓶颈是标注集规模而非模型能力；
  237 条生产数据比 bge-small 的 1 亿+ 训练对差 4 个数量级，微调难有泛化增益。
- **蒸馏**：**已评估为不必要**（teacher bge-large 随时可调用，蒸馏只为省 90MB；数据量差 4 个数量级）。
  登记为"远期、需满足部署条件（边缘部署/百万级请求）"。
- **reranker**：bge-reranker 是 query-doc 交叉编码器，**只适用于 memory_query 检索路**（top-20→top-5
  重排），对门①/②/④ 的成对去重结构上不适用。是否引入待用户确认（当前未引入）。
- **SQLite 向量索引**：当前 `scan_memory_dirs` 每次全量读文件+现算 embedding，237 条可接受
  （门④ 40 条两两比较 1.3s）；>1000 条应加向量缓存（SQLite BLOB + numpy 余弦）。
- **LLM 终审**：合并候选预审走后台批处理（离线约束允许），人工终审不变。


## 四、双树偏差（重要）

compat 仓库（本仓库）**不包含**捕获链（dev scripts 独有非测试 .py 共 10 个：纯捕获链 9 个
eco_evolve/eco_extract/eco_git_commit/eco_health_alert/eco_l1_audit/eco_note/eco_note_backfill/
eco_note_backfill_runall/eco_note_signals，另有 _ecoreview_selftest.py 自测；dev 独有测试 4 个另计）
——它们依赖宿主 state.db，
留在 兼容版/hermes 生产树。但 README 曾宣称"两线核心功能完全一致"——与文件清单不符。
**待办**：要么 publish_compat 补齐捕获链的可移植化（state.db 访问抽象成宿主接口），
要么 README 明确"compat=治理核心+检索，捕获链=宿主树专属"。本次审计采用后者口径，
并以 `tools/sync_trees_check.py` 防止共有文件继续漂移。

### 4.1 未回迁漂移清单（2026-10-04 sync_trees_check 实测，待人工逐个核对）

以下文件 compat 与 兼容版/hermes 存在**内容级**差异（兼容版侧领先：GUI/first_aid 支持、
`.agent-index` 路径等），本次未盲同步——需人工确认哪侧为准后回迁：
`lib/fs.py`、`lib/metrics.py`、`lib/safeio.py`、`lib/inject_gate.py`、`lib/__init__.py`、
`eco_search.py`、`eco_note_query.py`、`eco_note_error_query.py`、`eco_note_index.py`、
`eco_note_merge_list.py`、`eco_eval_gold.py`、`test_eco_eval_v22.py`、
`test_eco_note_error_query_v2.py`、`test_eco_version.py`（共 14 个）。
本次已同步：四道门+lib 五件套+memory_query+eco_sim_eval+全部新增测试（兼容版/hermes 各 43 文件，均带 .bak）。

## 五、其他已知边界（有意为之，非遗漏）

- 门④合并候选/merge_list 只出清单不执行（人工兜底纪律）。
- 复活是半自动：memory_query 只写复活候选提案，`--revive` 人工确认执行。
- quarantine 账本 `src` 字段区分天数来源（目录日期优先于 mtime）。
- 物理删除代码层禁用；archive 只进不出（基因库模式，存储成本可忽略，注意力成本由检索解决）。
