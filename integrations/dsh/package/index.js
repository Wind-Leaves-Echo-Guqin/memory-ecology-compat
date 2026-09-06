'use strict';
/**
 * eco-note-dsh — 记忆生态·兼容版 dsh 适配器（Extension SDK V1）
 *
 * 通道（设计稿《记忆生态-兼容版-dsh适配设计稿-v0.1》§2）：
 *   - provideContext：每回合 spawn Python 侧 eco_note_dsh_context.py——
 *     扫 dsh 会话窗口内报错 → 根因分层检索经验库 → 返回注入文本（无错/冷却/无命中 → null）
 *   - registerTool：eco_note_query / eco_note_error_query 原生工具（agent 主动查询）
 *
 * 纪律：
 *   - fail-open：Python 侧任何失败都返回 null（不注入），绝不阻塞宿主回合
 *   - 只读消费：不写任何经验内容（仅 experiences/.dsh_inject_state.json 注入状态文件）
 *   - 超时：spawn 8s 上限（SDK 宿主另有每回合贡献超时兜底）
 *
 * 环境变量：
 *   ECO_PYTHON        python 可执行文件（默认 "python"）
 *   ECO_SCRIPTS_DIR   生态核心 scripts 目录（默认自动探测开发/发布布局）
 */
const { spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const PYTHON = process.env.ECO_PYTHON || 'python';
const SPAWN_TIMEOUT_MS = 8000;

function locateScriptsDir() {
  const env = process.env.ECO_SCRIPTS_DIR;
  if (env && fs.existsSync(env)) return env;
  // package/ → eco-note-dsh → extensions(或 adapters/dsh) → 根
  const repoRoot = path.join(__dirname, '..', '..', '..');
  for (const rel of ['scripts', path.join('src', 'memory_ecology')]) {
    const cand = path.join(repoRoot, rel);
    if (fs.existsSync(cand)) return cand;
  }
  return repoRoot; // 兜底：让 python 侧报出可读错误
}

function runPython(scriptPath, args, cwd) {
  const r = spawnSync(PYTHON, [scriptPath, ...args], {
    cwd,
    encoding: 'utf8',
    timeout: SPAWN_TIMEOUT_MS,
    windowsHide: true,
  });
  if (r.error || r.status !== 0 || !r.stdout) {
    return null; // fail-open
  }
  return r.stdout;
}

/** 每回合上下文贡献：返回注入文本或 null（无错/冷却/无命中）。 */
function runContext() {
  const out = runPython(path.join(__dirname, 'python', 'eco_note_dsh_context.py'),
                        [], locateScriptsDir());
  if (!out) return null;
  try {
    const payload = JSON.parse(out.trim());
    return payload && payload.inject ? payload.inject : null;
  } catch (_) {
    return null;
  }
}

/** 原生工具：经验关键词检索（text 输出，agent 可读）。 */
function toolQuery(args) {
  const kw = String((args && args.keyword) || '').trim();
  if (!kw) return { error: 'keyword 必填' };
  const top = String((args && args.top) || 3);
  // Q28 修复（2026-09-06）：检索 CLI 在核心 scripts 目录，不在本包 python/ 下——
  // 原实现拼 __dirname/python → spawnSync ENOENT → 工具恒返兜底文案
  const scriptsDir = locateScriptsDir();
  const out = runPython(path.join(scriptsDir, 'eco_note_query.py'), [kw, '--top', top], scriptsDir);
  return { result: out || '（无命中或检索不可用）' };
}

/** 原生工具：报错文本 → 根因分层经验检索。 */
function toolErrorQuery(args) {
  const text = String((args && args.text) || '').trim();
  if (!text) return { error: 'text 必填' };
  const top = String((args && args.top) || 3);
  const scriptsDir = locateScriptsDir();
  const out = runPython(path.join(scriptsDir, 'eco_note_error_query.py'), [text, '--top', top], scriptsDir);
  return { result: out || '（无命中或检索不可用）' };
}

module.exports.activate = function activate(ctx) {
  ctx.log('info', 'eco-note-dsh 已激活（记忆生态·兼容版适配器）');

  ctx.registerTool(
    'eco_note_query',
    {
      description: '记忆生态经验库关键词检索（只读）。想查"以前有没有踩过这个坑/这类事怎么做"时用。',
      parameters: {
        keyword: { type: 'string', required: true, description: '检索关键词' },
        top: { type: 'string', required: false, description: '最多返回条数（默认 3）' },
      },
    },
    toolQuery,
  );

  ctx.registerTool(
    'eco_note_error_query',
    {
      description: '把报错文本交给记忆生态经验库做根因匹配检索（只读）。遇到看不懂的报错时用。',
      parameters: {
        text: { type: 'string', required: true, description: '报错/异常文本' },
        top: { type: 'string', required: false, description: '最多返回条数（默认 3）' },
      },
    },
    toolErrorQuery,
  );

  // 经 _internals 间接调用：测试可替换 runContext（生产路径不变）；
  // JS 边界兜底 fail-open——任何异常都不阻塞宿主回合
  ctx.provideContext(() => {
    try {
      return module.exports._internals.runContext();
    } catch (_) {
      return null;
    }
  });

  ctx.on('turn-end', (info) => {
    ctx.log('debug', `eco-note-dsh turn-end: ${JSON.stringify(info ?? {}).slice(0, 100)}`);
  });
};

// 测试与诊断钩子（生产无副作用）
module.exports._internals = { runContext, toolQuery, toolErrorQuery, locateScriptsDir };
