# eco-note-dsh · 记忆生态 dsh 适配器

让 dsh（DeepSeek Harness EAC，cordis 插件体系）成为记忆生态的第二个宿主：
**报错时按根因自动注入相关经验 + 原生经验查询工具**。Python 核心（四道门/经验笔记本）零改动。

## 能力

| 通道 | 机制 | 说明 |
|---|---|---|
| 经验注入 | Extension SDK `provideContext` | 每回合 spawn `package/python/eco_note_dsh_context.py`：扫 dsh 会话（`~/.dsh/sessions/**/session.jsonl.zstd`，UUID 在目录名）窗口内报错 → 根因分层检索 → 返回注入文本；无错/冷却/无命中返回 null |
| 原生工具 | `registerTool` | `eco_note_query`（关键词检索）、`eco_note_error_query`（报错根因检索），agent 可主动调用 |
| 防回声 | ECHO_MARK | 注入文本带 `【经验参考】` 标记，信号扫描不当作新报错 |
| fail-open | 双层 | Python 失败→null；JS 闭包再兜 try/catch——绝不阻塞宿主回合 |

**纪律：本插件对经验库零内容写入**（只读消费；仅留 `experiences/.dsh_inject_state.json` 注入状态文件，见「卸载」节）；四道门/经验捕获的调度权归主宿主（单写入方纪律）。

## 前置条件

- dsh v4.4.1（**不要**装到 v5 半成品目录）；Extension SDK（VNext Phase 2）可用
- Python 3.10+，`pip install zstandard`（会话解压用）
- 记忆生态核心（本仓库 `src/memory_ecology/`）+ 经验库数据根（与主宿主共享同一条根）

## 安装（三选一，推荐 A）

### A. 官方装配（生产态，重启后由 bundles 接管）

```bash
dsh plugin --profile web add <本目录>/package
```

### B. 运行时注入（开发态，需已常驻 dsh-super-injector）

对 dsh agent 说：`dev_inject_plugin <本目录>/package`

### C. 手动拷贝

把 `package/` 整目录拷到 `~/.dsh/extensions/eco-note-dsh/`，并按 dsh 的扩展登记机制注册
（登记 schema 以当版 dsh 文档为准；样例可参考 `~/.dsh/extensions/sample-sdk-plugin`）。

## 配置（环境变量，装在哪台机器配哪台）

| 变量 | 默认 | 说明 |
|---|---|---|
| `ECO_SCRIPTS_DIR` | 自动探测仓库 `src/memory_ecology` | 生态核心 scripts 目录。**注意：安装到 `~/.dsh/extensions/` 后父目录链上没有仓库，自动探测必然失效——安装态必须显式设置此变量** |
| `MEMORY_ECOLOGY_ROOT` | `<scripts>/..` | 数据根（共享根模式：指向主宿主数据根） |
| `ECO_PYTHON` | `python` | Python 解释器 |
| `DSH_SESSIONS_ROOT` | `~/.dsh/sessions` | dsh 会话根（注入检测用） |

窗口/冷却等阈值在 `eco_note_dsh_context.py` 常量区（window 10min / cooldown 15min）。

## 验证

1. dsh 会话里让 agent 制造一个报错（如运行 `python -c "print(nope)"`）
2. 下一回合若经验库有同根因条目（如 NameError 类），上下文应出现
   `【经验参考】(只读参考, 可忽略) 最近出错…`
3. 工具列表出现 `eco_note_query` / `eco_note_error_query`，可手动调用

## 测试（不依赖 dsh）

```bash
node test_smoke.js                    # mock ctx：注册面/fail-open/工具
python package/python/test_eco_note_dsh_context.py   # 会话提取→注入→冷却状态机（fixture 隔离）
```

## 卸载

`dsh plugin` 移除或直接删 `~/.dsh/extensions/eco-note-dsh/`；数据根里只留下
`experiences/.dsh_inject_state.json`（可删）。

## 已知边界

- Extension SDK 属 VNext（接口可能随 dsh 升级变动；peer 语义不硬编码版本）
- 插件崩溃由 dsh crashStreak 机制自动禁用，不影响宿主
- **不注册任何 HTTP 路由**（规避 dsh duplicate exact route 冲突教训）
- 对准 dsh v4.4.x；v5 半成品 junction 污染问题未解前不要装 v5
