# Changelog

## v2.2.3（2026-09-16）

- 观测舱 GUI v0.3.3：技能星图双形态（3D WebGL 玻璃球体 + 简单光追 / 2D 力导向平铺深空玻璃版）、
  血缘谱系真图（分层布局 + 方向箭头 + 流动光点）、记忆库与孵化台/时间线/体检/动作日志形象化动效、
  动效基座（anim.js + CSS token + 三档强度 + 低帧率自动降级）、内联 SVG 图标集与空态插画
- Three.js 3D 星图纳入主线（库随发布，懒加载，WebGL 不可用自动回退平铺视图）
- 核心脚本零改动；两树版本号同步

## v2.2.2（2026-09-15）

- 观测舱 GUI v0.3.2：视觉重构（三色板精修 + 自托管屏显字体 + 毛玻璃质感 + 响应式凝练布局）
- 体验升级：键盘快捷键与 Ctrl+K 命令面板、检索历史、一键导出、桌面通知、诊断包、
  动画强度三档、星图力导向物理引擎、粒子光晕、自动轮询、全屏巡航双模式
- 桌面集成：`setup_ecology.py` 一键安装（环境体检 → 依赖引导 → 端口探测 → 桌面入口 →
  首跑自检）、`tray.py` 托盘常驻 + 开机自启（当前用户级、默认关、可逆）
- Three.js 3D 星图：已评估、暂不纳入主线（原型不随发布）
- 核心脚本零改动；两树版本号同步

## v2.2.1（2026-09-15）

- 集成层新增观测舱 GUI v0.3（`integrations/gui/`）接入开源发布树：
  - 可操作驾驶舱：九视图只读观测 + 四类写操作（配额挤出/写入门整合/经验采纳/技能孵化），
    全部经确认闸门（高风险红字 + 勾选）转交核心 CLI 子进程执行，动作前后记 action_log.jsonl
  - 急救箱（first_aid.py）：自检 → 修复 → 重启服务 → 开窗；生产 cron 领地只诊断不动手
  - 启动器三入口：`启动生态观测舱.cmd`（相对路径）/ `启动生态观测舱.pyw`（无黑框）/
    `install_desktop.py`（按机器生成桌面指针入口，杜绝手工复制绝对路径）
- 启动器修复（安全）：急救箱 cmd 路径拼接 bug（原 `%~dp0integrations\gui\...`
  在桌面/仓库两个位置均指向不存在文件，双击失败且伪装成功）；补 python/py 探测链与
  errorlevel 失败提示；cmd 统一 CRLF
- 发布流水线：敏感词扫描扩展名白名单新增 .cmd/.html/.css/.pyw（堵住启动器与前端页面
  不被扫描的盲区）；scripts_dir 探测新增发布树 src/memory_ecology 布局
- 核心脚本零改动；数据根/检索 CLI 定位链保持 参数 > 环境变量 > 探测 > 兜底

## v2.2.0（2026-09-06）

- 阶段 C 评测验收：新增 eco_eval_gold.py 伪 gold 构建器（命中=实际使用，会话文本即证据）；
  eco_eval 新增 INJ（注入超限 ≤5 次/30 天）与 GOLD（top5 相关率 ≥60%）两条门禁判定线，
  INSUFFICIENT（数据不足）诚实单列不计 FAIL；--gate 纯规则模式可入 cron/回归
- 安全写路径：新增 lib/safeio.py（条目名白名单+防穿越、detail/experience frontmatter schema
  校验、原子写+备份、JSONL 容量护栏）；write_gate/eco_note_adopt/注入 hook 全部条目写入方接线
- 注入文本压缩为每条目 1 行（体积约减半）；.injected.jsonl 新增 chars/error 遥测字段
- 独立 code review 15 项 findings：14 项修复（adopt per-candidate 异常隔离、gold 时间归因、
  CONFLICT 时序、时区归一、原子化收口等），1 项记录为不可达口径（白名单小项）

## v2.1.2（2026-09-06）

- dsh 适配器修复：会话文件 glob 对齐真实命名（session.jsonl.zstd）、流式 zstd 解压
  （read_across_frames）、原生工具指向核心 scripts 目录、只扫最新活跃会话
- 注入质量门：verified 经验优先 + 注入文本标注状态 + 三次态确认命中 ≥2 自动 draft→verified
- hit 反馈回路修复（entry_ids 键名 + 判定记账去重），last_hit 真实回写
- write_gate 消费后标记 .done.md；健康告警产出探针复活且排除已消费件
- 数据根：开源 src 布局默认落到仓库根（原落 src/）；实现 MEMORY_ECOLOGY_API_KEY 环境变量
- 发布流水线：保留 .git、--out 守卫、敏感词扫描失败自动回滚
- 测试：日期炸弹修复 + 新增 mark_consumed / auto-verify 用例，全绿

## v2.1.1-compat.1（2026-09-05）

- 首个兼容版：自上游 v2.1.1 复制（四道门 + eco_note 家族 + lib + 测试，76 用例基线全绿）
- 多宿主化：全部路径常量收敛 `lib/config.py`（`MEMORY_ECOLOGY_ROOT` 可覆盖）
- 新增 dsh 适配器（Extension SDK：provideContext 根因注入 + eco_note_query/error_query 原生工具）
- 报错检索新增**根因分层**（异常类名提取：同根因 > 泛化 > 异根因沉底），修复按词匹配误注入
- eco_version 健康行字符数口径修复（len() 即字符数，删除 //3）
