# Changelog

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
