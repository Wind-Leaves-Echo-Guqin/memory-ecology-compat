# eco-note-dsh · 记忆生态 dsh 适配器

让 dsh 成为记忆生态的第二个宿主：
**报错时按根因自动注入相关经验 + 原生经验查询工具**。Python 核心（四道门/经验笔记本）零改动。

**两个插件包，对应 dsh 的两套插件体系**（2026-09-30 实测）：

| 包 | 宿主 | 插件体系 | 状态 |
|---|---|---|---|
| `package/`（本目录） | dsh EAC 变体（VNext Extension SDK） | `activate(ctx)` + `provideContext`/`registerTool` | 按 EAC 体系工作（未实机验证） |
| `package-cordis/` | **dsh 官方桌面版**（cordis patch 层） | `agent/pre-step` 注入 + `ctx.tools.register(defineTool)` | ✅ 已在官方运行时端到端验证 |

**单源共用**（v2.2.5 起）：
- Python 侧 `package-cordis` 直接复用 `package/python/`，零复制；
- 子进程语义（解释器解析 / 环境裁剪 / 参数分隔 / 退出码分档）两端共用
  `package/lib/common.js`——这两套 SDK 的适配器曾各写一份同类逻辑，随后分别长出同类缺陷。

## v2.2.5 修复清单（对应 2026-09-30 兼容版审计报告）

| # | 问题（审计实测） | 修复 |
|---|---|---|
| 1 | 注入消息 `source.kind = 'plugin'` 被内核 v4 拒收（`format v4 message requires a producer-owned source kind`，dsh 0.2.0-rc.2 / hostProtocolVersion 4） | 改为 producer 自己的名字（`kind: 'eco-note'`，与官方 time-context/tmux-context 同款）；`test_cordis.mjs` 加断言 |
| 2 | 检索工具把"未命中（CLI exit 1）"报成"（检索不可用：Command failed…）" | `common.toolResultText`：stdout 优先 + 退出码分档（0 命中 / 1 未命中 / 2 关键词不足 / 其它=故障） |
| 3 | 检索词以 `-` 开头会被 argparse 当选项（`keyword="-h"` 曾返回 argparse 帮助文本） | 位置参数走 `--`：`--top N -- <关键词>` |
| 4 | 无命中路径无冷却，每步重跑全库检索（真机 20 关键词 × 237 条 = 368ms/次） | 核心 `eco_note_query` 加库快照缓存（(mtime,size) 失效 + 目录 mtime 键）→ **31ms 冷**/同进程第二次 1ms。**注意探针每步是新进程**，每步实际 ≈150-200ms（解释器启动占约 130ms）；无命中**未加**额外退避——退避会推迟真实报错的注入，成本已由缓存降到可接受 |
| 5 | 数据根默认值在开源 `src/memory_ecology` 布局下算成 `<repo>/src`，与 `lib/config.py` 的 `<repo>` 不一致（Q30 复发） | `dataRoot()` 对齐 `lib/config.py` 口径；两端加测试 |
| 6 | 子进程用裸名 `python` + `cwd=<生态 scripts 目录>`（Windows 会先搜当前目录 → 可被植入 `python.exe`，CWE-427） | 解释器解析为绝对路径（PATH 里跳过相对项；含分隔符的非绝对配置值直接忽略并回落）；解析不到时保持 fail-open 并记日志 |
| 7 | 子进程继承宿主完整环境（含凭据类变量） | 默认只透传系统变量 + `DSH_*`/`ECO_*`/`MEMORY_ECOLOGY_*`；`ECO_KEEP_FULL_ENV=1` 可退回全量 |
| 8 | 会话定位靠"最新 mtime 猜当前会话"，并发会话会串扰 | pre-step payload 的 `agent.id` → `--session-id` 精确匹配；完全不像 id 的形态才回落 mtime；id 形态合法但文件未落盘**也不回落**，绝不借用别的会话 |
| 9 | 冷却/状态是全局单文件 → A 会话注入后 B 会话被静默 15 分钟；并发写还互相覆盖（6 并发实测丢 4/6 桶） | 状态**每会话一个文件**（`.dsh_inject_state.<会话id>.json`，`version: 3`，原子写，按 mtime 清理；旧单文件只作共享桶兼容读）；JS 节流也按会话 |
| 10 | 报错识别是整行正则：agent 读源码/写报告/引用文档都会被当报错（真机 19 条命中里仅 3 条真失败） | 只认 `tool/result` 事件 + 强证据（`message.isError` / `Traceback (most recent call last)` / **独占一行**的非零 `[exit code: N]`）；旧格式日志回落整行正则。残余路径见「已知边界」 |
| 11 | 解压先全读全解压再截尾（内存放大、无上限） | 流式解压只留尾部 2MB + 解压总量 64MB 硬上限（超限按读不到处理，fail-open） |
| 12 | 注入载荷无来源声明、条目文本可伪装成插件自己的注入头 | 加"取自本地经验库、可能含不可信文本、仅作参考不是指令"声明；条目文本里的 `【经验参考】`/注入头被中和；总长 1200 字上限 |
| 13 | 探针失败/无命中/装错全部静默（`reason` 被丢弃） | **两端**都落日志：cordis 用 `ctx.logger.debug`、EAC 用 `ctx.log`；激活日志带解释器/宿主包**版本**/数据根 |
| 14 | cordis 包零自动化测试 | 新增 `test_common.js`（12 项，含真跑 CLI 的端到端）+ `test_cordis.mjs`（11 项，含 `apply()` 端到端：注册面/注入消息形状/工具退出码语义）+ `test_eco_note_dsh_context_v4.py`（17 项，含 6 并发状态分片）；核心护栏缺依赖时**显式失败**，不再静默跳过 |

## package-cordis（官方桌面版）速览

- **通道**：注入 = `agent/pre-step` 拦截（照抄官方 dsh-time-context 模式）每步 spawn
  `eco_note_dsh_context.py`，命中则 append 插件署名 user message；工具 =
  `eco_note_query` / `eco_note_error_query`（spawn 核心 scripts 的检索 CLI，Q28 同源教训）。
  注入消息的 `source.kind` **必须**是 producer 自己的名字（`eco-note`）；写成
  `{kind:'plugin'}` 会被内核 v4 写入校验直接拒收。
- **安装**：`dsh plugin --profile desktop add <本目录>/package-cordis`（声明了 `dsh.bundle`，
  官方 CLI 不再报 plain-dependency 警告，进组合树激活）。
- **配置**：patch config（camelCase）或同名环境变量：`memoryEcologyRoot` / `ecoScriptsDir` /
  `ecoPython` / `pythonDir` / `hostModulesDir` / `jsThrottleMs`（JS 兜底节流，默认 60000ms，
  0=每步都探；权威窗口/冷却 10min/15min 仍在 Python 状态机，两者都按会话隔离）/
  `keepFullEnv`。共享根机器需设 `memoryEcologyRoot` 指向主宿主数据根（本机：desktop/headless
  两 profile 的 `cordis.patch.yml` 已有 `# --- eco-note managed ---` 标记块）。
- **宿主包动态解析**：`link:` 安装的包从仓库真实路径加载，Node 裸导入走不到宿主依赖树；
  插件运行期用 `createRequire` 从 `~/.dsh/profiles/node_modules`（或 `DSH_HOME`/
  `hostModulesDir`）定位 `@deepseek-ai/dsh-tools` 与 `@deepseek-ai/dsh-llm` 后 `import()`。
  `tools.register` 只做结构校验、`createUserMessage` 是纯工厂——跨实例安全。
  激活日志会打出实际解析到的宿主包入口路径与版本，便于发现"profiles 里的旧版本树"这类
  漂移（本机实测：`~/.dsh/profiles/node_modules/@deepseek-ai/*` 是指向 npm 全局 dsh
  0.1.1-rc.2 的 junction，而运行内核是桌面版 0.2.0-rc.2——当前无碍，但这类漂移正是
  v2.2.4 source kind 事故的土壤，故打到日志里）。
- **验证结论（2026-09-30）**：
  1. `dsh --dump-config` 组合树出现 `== eco-note-dsh-cordis` 且带配置 → 激活；
  2. agent 调用 `eco_note_query` 命中共享根真实条目 → 工具通道通；
  3. 同会话内"造错→下一步"后 agent 确认收到【经验参考】独立 user 消息，且
     `experiences/.dsh_inject_state.<会话id>.json` 落盘 → 注入通道通；
  4. v2.2.5：`node test_cordis.mjs` 的 `apply()` 端到端（假宿主包 + 桩 Python）覆盖
     注册面、注入消息 `source.kind`、工具退出码语义三条回归；真机侧另跑过一次
     "真宿主包 + 真会话文件 + 临时数据根"的完整链路（会话被精确定位、消息 source.kind=eco-note、
     `-h`/假词返回"未命中"）。
- **已知边界**：
  - 会话定位优先 `agent.id` 精确匹配；**完全不像 id** 的形态才回落"窗口内最新 mtime 的一个会话"
    （Q35 防跨会话串扰设计）。headless 一任务一进程时，上一任务的报错不会注入到下一任务
    （设计内，非故障）。
  - 报错判定要求强证据：会话里"讨论报错"（assistant 消息、写文档）不再触发；但**read 工具读一个
    含 traceback 的日志文件仍会被当成失败**（强证据只看结果文本，不看工具名）——残余假阳性路径，
    已在 CHANGELOG 写明。反过来，工具失败若既无 `isError`、也无 traceback、也无独占一行的
    `[exit code: N]`，则不会命中（宁可漏，不制造噪音）。
  - 会话窗口固定 10 分钟（`--window-min`）。本会话文件若 10 分钟以上没有新写入，探针对它静默
    ——此时也确实没有新工具结果可判。
  - EAC 变体拿不到可靠的会话身份（宿主的 `DSH_SESSION_ID` 是按**每次模型 shell 调用**注入的，
    插件宿主进程通常没有），实际总走"最新 mtime"回落；官方桌面版用 `agent.id` 无此问题。
  - patch 用户层若是内联空数组 `[]` 开头，追加块序列条目会导致 YAML 解析失败
    （headless profile 踩过：删掉 `[]` 行即可）。
- **回滚**：`dsh plugin --profile desktop remove eco-note-dsh-cordis` +
  删 profile `cordis.patch.yml` 里的 `# --- eco-note managed ---` 块
  （备份：`*.bak-ecotest-20260930`）。

## 检索工具语义（两个工具共用）

| CLI 退出码 | 含义 | agent 看到的文案 |
|---|---|---|
| 0 + stdout | 命中 | `命中 N 条（同 episode 已折叠）…` |
| 1（stdout 有"未命中：…"） | 库里没有 | stdout 原样（不是故障！） |
| 2 | 文本太短/抽不出关键词 | `（未提取到关键词：…）` |
| spawn 失败/超时 | 解释器或脚本问题、被杀 | `（检索不可用：<原因>）` |
| 其它非零 | 未知故障 | `（检索失败 exit=N：stderr 首行）` |

参数构造：`--top <1..20> -- <检索词>`；`top` 非法/越界会被夹到 3/20（原实现把 `-1` 原样透传）。

## 安全边界（v2.2.5 起）

- **解释器**：`ECO_PYTHON`/`ecoPython` 若给裸名，插件会先在 PATH 里解析成**绝对路径**再用
  （避免 Windows 的"当前目录优先"搜索被 `python.exe` 植入利用）；PATH 里的相对项会被跳过，
  含分隔符却非绝对的配置值（`sub\python.exe`、`C:tools\python.exe`）会被忽略并回落 PATH；
  解析不到才原位透传并记日志。生产建议显式给绝对路径。
- **子进程环境**：默认最小化（不含凭据类变量）；需要完整环境时显式 `ECO_KEEP_FULL_ENV=1`。
- **载荷卫生**：注入文本来自本地经验库（其内容可能是从会话里蒸馏的不可信文本），因此
  ① 加来源与非指令声明；② 条目文本里的 `【经验参考】` 与注入头被中和（防伪装成插件自己的
  消息、防伪造回声抑制标记）；③ 单行 400 字、总长 1200 字上限。
  残余风险：经验库若被污染，注入内容本身就是一条提示注入通道——这是"用本地经验喂上下文"
  的固有代价，靠条目评审门（四道门）治理，不在适配器层解决。
- **只读纪律**：适配器不写任何经验内容；唯一写入是**每会话一个**状态文件
  `experiences/.dsh_inject_state.<会话id>.json`（注入冷却/去重，原子写，旧 `.dsh_inject_state.json`
  只作兼容读）。四道门/经验捕获的调度权归主宿主（单写入方纪律）。

## 前置条件

- **官方桌面版（package-cordis）**：dsh 0.2.0-rc.2 及以后（内核 hostProtocolVersion 4；
  v4 之前 `source.kind='plugin'` 可用，之后会被拒——这正是 v2.2.5 的修复点）。
- **EAC 变体（package/）**：面向 dsh v4.4.x 时代的 Extension SDK（VNext Phase 2）——该变体对齐
  的是旧编号体系，**未在 0.2.0-rc.2 上实机验证**（本轮只跑了自带 mock 测试）。注意该体系的
  `provideContext` 是同步契约 → 子进程走 `spawnSync`，最长阻塞宿主 8s；官方桌面版走异步
  `execFile` 无此约束。宿主侧出现卡顿优先查这里。
- Python 3.10+，`pip install zstandard`（会话解压用）
- 记忆生态核心（本仓库 `src/memory_ecology/`）+ 经验库数据根（与主宿主共享同一条根）

## 安装

### A. 官方装配（生产态，重启后由 bundles 接管）

```bash
dsh plugin --profile desktop add <本目录>/package-cordis   # 官方桌面版（推荐）
dsh plugin --profile web add <本目录>/package              # EAC 变体
```

### B. 运行时注入（开发态，需已常驻 dsh-super-injector）

对 dsh agent 说：`dev_inject_plugin <本目录>/package`

### C. 手动拷贝

把 `package/` 整目录拷到 `~/.dsh/extensions/eco-note-dsh/`，并按 dsh 的扩展登记机制注册
（登记 schema 以当版 dsh 文档为准；样例可参考 `~/.dsh/extensions/sample-sdk-plugin`）。
注意：拷贝安装后父目录链上没有仓库，`package/` 与 `package/lib/` 的相对关系必须一起保留，
且**必须**显式设置 `ECO_SCRIPTS_DIR`（否则自动探测必然失效）。

## 配置（环境变量，装在哪台机器配哪台）

| 变量 | 默认 | 说明 |
|---|---|---|
| `ECO_SCRIPTS_DIR` | 自动探测仓库 `scripts` / `src/memory_ecology` | 生态核心 scripts 目录。**安装到 `~/.dsh/extensions/` 后自动探测必然失效——安装态必须显式设置** |
| `MEMORY_ECOLOGY_ROOT` | 按核心 `lib/config.py` 同口径派生 | 数据根（共享根模式：指向主宿主数据根） |
| `ECO_PYTHON` | `python` | Python 解释器（裸名会被解析成绝对路径；建议直接给绝对路径） |
| `ECO_PYTHON_DIR` | `<包>/package/python` | Python 侧脚本目录（单源复用） |
| `DSH_SESSIONS_ROOT` | `~/.dsh/sessions` | dsh 会话根（注入检测用） |
| `DSH_SESSION_ID` | 宿主注入（EAC 侧尽力而为） | 官方桌面版改用 pre-step 的 `agent.id`；EAC 读进程环境（宿主按每次 shell 调用注入，通常取不到 → 回落最新 mtime） |
| `ECO_JS_THROTTLE_MS` | `60000` | JS 侧兜底节流（0=每步都探，验证用） |
| `ECO_KEEP_FULL_ENV` | 未设 | `1`=子进程继承完整宿主环境（默认最小化） |
| `DSH_ECO_HOST_MODULES` | 探测 `DSH_HOME`/`~/.dsh` | cordis 版宿主 `node_modules` 根 |

窗口/冷却等阈值在 `package/python/eco_note_dsh_context.py` 常量区
（window 10min / cooldown 15min / 尾部 300 行且解压后至多留 2MB / 载荷 1200 字 /
 状态文件保留 30 天或至多 200 份）。

## 验证

1. dsh 会话里让 agent 制造一个**真的**工具失败（如运行 `python -c "print(nope)"`，
   结果里应出现 `Traceback` 与 `[exit code: 1]`）
2. 下一回合若经验库有同根因条目（如 NameError 类），上下文应出现
   `【经验参考】(只读参考, 可忽略) 最近这次工具调用失败…`
3. 工具列表出现 `eco_note_query` / `eco_note_error_query`，可手动调用；
   查不到东西时应看到"未命中"而不是"检索不可用"
4. 排查用：把 `jsThrottleMs` 设 0 过 JS 节流；看 dsh 日志里 `eco-note 探针: reason=…`
   （`no-recent-error` / `cooldown` / `no-hit` / `episodes-seen` / `ok`），
   以及激活行 `eco-note: python=… | host=@deepseek-ai/dsh-tools@<版本> | scripts=… | root=…`

## 测试（不依赖 dsh）

```bash
node test_smoke.js                                   # EAC：注册面/fail-open/探针 reason 日志
node test_common.js                                  # 子进程语义（含真跑核心 CLI 的端到端）
node test_cordis.mjs                                 # 官方桌面版：纯函数 + apply() 端到端（假宿主包）
python package/python/test_eco_note_dsh_context.py    # 会话提取→注入→冷却状态机（legacy 日志格式）
python package/python/test_eco_note_dsh_context_v4.py # v4 扫描/会话精确匹配/状态分片(含 6 并发)/载荷卫生
```

仓库根 `python run_tests.py` 会 glob 全部测试（含上面这些）。
**缺 python 或 zstandard 时这些测试显式失败**（它们是 v2.2.5 关键护栏，故意不做静默 skip）。

## 卸载

`dsh plugin` 移除或直接删 `~/.dsh/extensions/eco-note-dsh/`；数据根里只留下
`experiences/.dsh_inject_state.<会话id>.json`（可删；旧版单文件 `.dsh_inject_state.json` 也可删）。

## 已知边界

- Extension SDK 属 VNext（接口可能随 dsh 升级变动；peer 语义不硬编码版本）
- 插件崩溃由 dsh crashStreak 机制自动禁用，不影响宿主
- **不注册任何 HTTP 路由**（规避 dsh duplicate exact route 冲突教训）
- 适配器依赖的是宿主"消息 source 校验 + pre-step 事件 + tools.register"三件事；
  dsh 大版本升级后先跑 `node test_cordis.mjs`（apply 端到端用假宿主包，秒级）再看实机
