# Memory Ecology Compat · 版本

- **v2.2.0**（2026-09-06）
- 阶段 C 评测验收（迭代策略 §3）：伪 gold 构建器（eco_eval_gold.py，gold=实际被使用过的记忆）
  + eco_eval 两条新判定线（INJ 注入超限 ≤5 次/30 天、GOLD top5 相关率 ≥60%）+ --gate 纯规则门禁模式
- 安全写路径加固（v2.2 首项）：lib/safeio.py（路径白名单/防穿越 + schema 校验 + 原子写/备份）
  接线 write_gate / eco_note_adopt / 注入 hook 全部条目写入方
- 注入文本压缩（每条目 1 行）+ 注入遥测新增 chars/error 字段
- 独立 code review 14 项 findings 全修；双树三轮回测全绿、改动文件字节一致
- 历史：v2.1.2（2026-09-06）两树同号同源起点 / v2.1.1-compat.1（2026-09-05）首个兼容版
