# ARCHITECTURE_TODO — 架构待办与已知裂缝清单（2026-10-04 全量审计产出）

> 目的：让债可见。这是"从个人可用到开源可维护"最重要的一步——不是修完所有债，而是不再隐藏。
> 产出背景：2026-10-04 两轮 AI 评审 + 人工核对 + 三路深扫，全部发现已修复或登记于此。

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
4. **lib/similarity**：simhash 待实现（原计划的 L2，现仅 difflib/ngram/embedding 三后端）；L3 embedding 默认化（见下节）。
5. **eco_note_backfill**：`--max-cands` 是**每会话**限额（2026-09-03 设计变更），docstring 已声明；全局限额与并行调度待重设计。

## 三、相似度/语义层路线（必须离线可用）

- **当前**：lib/similarity.py 统一层，默认后端 difflib（行为锁定：门① SIM_NEAR=0.80 / 门④ 0.70 阈值语义不漂移）。
- **L1 ngram**（纯 stdlib）：已实现。种子集评测：FPR=0 下可回收全部 same 对，但最优阈值 ~0.31
  ——**阈值重校准是切换前置条件**，盲切会把门①的 UPDATE/NOOP 判定全部漂移。
- **L3 本地 embedding**（必须离线）：OnnxEmbedder 已实现（onnxruntime+tokenizers+int8 模型文件，
  `MEMORY_ECOLOGY_MODELS` 指向模型目录，缺依赖自动回退 ngram）。
  候选模型：bge-small-zh-v1.5 / m3e-small / gte-base-zh（int8 量化版）。
- **评测标尺**：`python eco_sim_eval.py`（golden/similarity_golden.json，seed-v0 13 对）。
  **切换前置条件**：golden set 扩充至 80-120 对真实标注（detail 现库 + gate_log + dsh 评审 45 条近重复对；
  P0.5 月度仪式顺带积累），候选模型在 same_recall↑ / distinct_fpr 不升 下对比选优。
- **自研/微调（Phase 6，远期）**：系统的人工裁决数据（合并勾选=正样本、复活勾选=正样本、
  reject=负样本）天然构成对比训练集——数据飞轮已内建。触发线：golden>500 对或单模型 recall<90%。
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
