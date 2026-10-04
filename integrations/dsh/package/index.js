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
 *   - 只读消费：不写任何经验内容（仅 experiences/.dsh_inject_state.<会话id>.json 按会话注入状态文件，v3）
 *   - 超时：spawn 8s 上限（SDK 宿主另有每回合贡献超时兜底）
 *   - 单源：子进程语义（解释器解析/环境裁剪/参数分隔/退出码分档）复用
 *     ./lib/common.js —— 与官方桌面版 package-cordis 同一套，防两端漂移
 *
 * v2.2.5 修复（对应 2026-09-30 兼容版审计报告）：
 *   - 检索工具 stdout 优先 + 退出码分档（未命中 exit 1 ≠ 检索不可用；
 *     原实现 `r.status !== 0` 直接丢弃 stdout，把"库里没有"报成"检索坏了"）
 *   - 位置参数走 `--` 分隔（检索词以 '-' 开头不再被 argparse 当选项）
 *   - 解释器解析为绝对路径（堵子进程 cwd 植入 python.exe → 任意执行，CWE-427）
 *   - 子进程最小环境（默认不透传凭据类变量）
 *   - 会话身份取 DSH_SESSION_ID（有则精确匹配会话文件，无则回落 mtime 最新）
 *   - 失败文案带 stderr 摘要（原实现只回一句"无命中或检索不可用"，三种原因糊成一句）
 *
 * 已知边界：本 SDK 的 provideContext 是同步契约 → 子进程走 spawnSync，最长阻塞宿主
 * 8s（官方桌面版 cordis 版走异步 execFile，无此约束）。宿主侧若出现卡顿，优先查这里。
 *
 * 环境变量：
 *   ECO_PYTHON        python 可执行文件（默认 "python"，v2.2.5 起解析为绝对路径）
 *   ECO_SCRIPTS_DIR   生态核心 scripts 目录（默认自动探测开发/发布布局）
 *   ECO_PYTHON_DIR    Python 侧脚本目录（默认同包 python/）
 *   ECO_KEEP_FULL_ENV=1 透传完整宿主环境给子进程（默认只透传系统+生态变量）
 */
const path = require('path');
const fs = require('fs');

const common = require('./lib/common.js');

const PY = common.resolvePythonInterpreter(process.env.ECO_PYTHON || 'python');

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

const PYTHON_DIR = process.env.ECO_PYTHON_DIR && fs.existsSync(process.env.ECO_PYTHON_DIR)
  ? process.env.ECO_PYTHON_DIR
  : path.join(__dirname, 'python');

/** 会话身份：Extension SDK 无 agent 上下文，退用宿主注入的会话 id（有则精确匹配）。 */
function sessionArgs() {
  const sid = String(process.env.DSH_SESSION_ID || '').trim();
  return sid ? ['--session-id', sid] : [];
}

/** 统一的子进程调用（解释器绝对路径 + 最小环境 + 显式 ECO_SCRIPTS_DIR）。 */
function run(scriptDir, script, args) {
  const scriptsDir = locateScriptsDir();
  return common.runPythonSync(PY.cmd, path.join(scriptDir, script), args, {
    cwd: scriptDir,
    env: common.curatedEnv({ ECO_SCRIPTS_DIR: scriptsDir }),
  });
}

/**
 * 每回合上下文贡献的完整载荷：{inject, reason, n_errors, schema, session, ms}。
 * v2.2.5（审查 #13）：原实现只回 inject、把 reason 全丢了——装错目录/缺 zstandard/
 * 超时/无命中在宿主看来一模一样，用户无法自查。
 */
function runContext() {
  const res = run(PYTHON_DIR, 'eco_note_dsh_context.py', sessionArgs());
  if (!res.out) return { inject: null, reason: res.failed ? 'probe-failed' : 'no-payload' };
  try {
    const payload = JSON.parse(res.out.trim());
    return { ...payload, inject: payload && payload.inject ? payload.inject : null };
  } catch (_) {
    return { inject: null, reason: 'bad-json' };
  }
}

/** 原生工具：经验关键词检索（text 输出，agent 可读）。 */
function toolQuery(args) {
  const kw = String((args && args.keyword) || '').trim();
  if (!kw) return { error: 'keyword 必填' };
  // Q28 修复（2026-09-06）：检索 CLI 在核心 scripts 目录，不在本包 python/ 下——
  // 原实现拼 __dirname/python → spawnSync ENOENT → 工具恒返兜底文案
  const res = run(locateScriptsDir(), 'eco_note_query.py', common.buildScriptArgs(kw, args && args.top));
  return { result: common.toolResultText(res) };
}

/** 原生工具：报错文本 → 根因分层经验检索。 */
function toolErrorQuery(args) {
  const text = String((args && args.text) || '').trim();
  if (!text) return { error: 'text 必填' };
  const res = run(locateScriptsDir(), 'eco_note_error_query.py', common.buildScriptArgs(text, args && args.top));
  return { result: common.toolResultText(res) };
}

module.exports.activate = function activate(ctx) {
  ctx.log('info', `eco-note-dsh 已激活（记忆生态·兼容版适配器）python=${PY.cmd}（${PY.how}）`
    + ` scripts=${locateScriptsDir()}`);

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
      const r = module.exports._internals.runContext();
      if (!r) return null;
      // 诊断行（原实现把 reason 丢掉）：装错目录/缺 zstandard/无命中从此可区分
      ctx.log('debug', `eco-note 探针: reason=${r.reason ?? 'unknown'}`
        + ` n_errors=${r.n_errors ?? '-'} schema=${r.schema ?? '-'}`
        + ` session=${r.session ?? '-'} ms=${r.ms ?? '-'}`);
      return r.inject ?? null;
    } catch (_) {
      return null;
    }
  });

  ctx.on('turn-end', (info) => {
    ctx.log('debug', `eco-note-dsh turn-end: ${JSON.stringify(info ?? {}).slice(0, 100)}`);
  });
};

// 测试与诊断钩子（生产无副作用）
module.exports._internals = {
  PY,
  runContext,
  toolQuery,
  toolErrorQuery,
  locateScriptsDir,
  sessionArgs,
  common,
};
