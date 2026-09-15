# Memory Ecology Compat · 版本

- **v2.2.1**（2026-09-15）
- 集成层新增：观测舱 GUI v0.3（`integrations/gui/`，可操作驾驶舱——只读观测 +
  走确认闸门的写操作，全部转交核心 CLI；含急救箱与启动器三入口：cmd / .pyw /
  install_desktop.py 桌面生成器）
- 启动器加固：仓库内 cmd 全部相对路径 + 解释器探测（pythonw → pyw → 报错可读）；
  急救箱修复路径拼接 bug（双位置均不可用）并补失败检测；敏感词扫描扩展名新增
  .cmd/.html/.css/.pyw
- 核心脚本（src/memory_ecology）零改动，与上游 v2.2.0 功能一致

- **v2.2.0**（2026-09-06）
- 阶段 C 评测验收（迭代策略 §3）：伪 gold 构建器（eco_eval_gold.py，gold=实际被使用过的记忆）
  + eco_eval 两条新判定线（INJ 注入超限 ≤5 次/30 天、GOLD top5 相关率 ≥60%）+ --gate 纯规则门禁模式
- 安全写路径加固（v2.2 首项）：lib/safeio.py（路径白名单/防穿越 + schema 校验 + 原子写/备份）
  接线 write_gate / eco_note_adopt / 注入 hook 全部条目写入方
- 注入文本压缩（每条目 1 行）+ 注入遥测新增 chars/error 字段
- 独立 code review 14 项 findings 全修；双树三轮回测全绿、改动文件字节一致
- 历史：v2.1.2（2026-09-06）两树同号同源起点 / v2.1.1-compat.1（2026-09-05）首个兼容版
