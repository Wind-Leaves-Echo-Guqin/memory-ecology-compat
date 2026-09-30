'use strict';
/**
 * eco-note-dsh 适配器共用薄层（v2.2.5 新增，两个插件包单源共用）。
 *
 * 为什么单源：EAC 版（package/index.js，CJS）与官方桌面版（package-cordis/index.js，
 * ESM）必须给出**同一套**子进程语义——解释器解析、环境裁剪、参数分隔、退出码分档。
 * v2.2.5 前这套逻辑在两边各写一份，已经分别长出过同类缺陷（见 CHANGELOG 2.2.5）。
 *
 * 契约：
 *   resolvePythonInterpreter -> 绝对路径优先（堵 Windows 的 cwd 植入 python.exe，CWE-427）
 *   curatedEnv               -> 子进程最小环境（默认只透传系统变量 + DSH_/ECO_/MEMORY_ECOLOGY_）
 *   buildScriptArgs          -> ['--top', N, '--', 位置参数]（`--` 防检索词以 '-' 开头被 argparse 当选项）
 *   runPython/runPythonSync  -> {out, err, code, failed}；**stdout 优先**，
 *                               exit 1/2 = CLI 的正常业务结论，不是故障
 *   toolResultText           -> 退出码分档文案（未命中 ≠ 检索不可用）
 */
const { execFile, spawnSync } = require('child_process');
const { existsSync } = require('fs');
const path = require('path');

const SPAWN_TIMEOUT_MS = 8000;
const MAX_BUFFER = 1 << 20; // 1MB：检索输出量级远小于此，超了就是异常

// 系统/解释器必需变量（大小写不敏感比较）。刻意不含任何凭据类变量。
const ENV_ALLOW = new Set([
  'PATH', 'PATHEXT', 'COMSPEC', 'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR',
  'TEMP', 'TMP', 'TMPDIR', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'HOME',
  'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)',
  'COMMONPROGRAMFILES', 'COMMONPROGRAMFILES(X86)', 'PROGRAMW6432',
  'OS', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'PROCESSOR_IDENTIFIER',
  'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'PYTHONUTF8', 'PYTHONIOENCODING',
  'PYTHONHOME', 'PYTHONPATH', 'PYTHONSTARTUP',
]);

// 宿主/生态自有命名空间整段透传（DSH_SESSIONS_ROOT、DSH_HOME、ECO_* 等）
const ENV_PREFIXES = ['DSH_', 'ECO_', 'MEMORY_ECOLOGY_'];

function keepFullEnvEnabled(config) {
  const v = config?.keepFullEnv ?? process.env.ECO_KEEP_FULL_ENV;
  return v === true || v === '1' || v === 'true';
}

/** 子进程最小环境：系统白名单 + 生态命名空间 + 本次显式 extra。 */
function curatedEnv(extra = {}, config = undefined) {
  if (keepFullEnvEnabled(config)) return { ...process.env, ...extra };
  const out = {};
  for (const [k, v] of Object.entries(process.env)) {
    const up = k.toUpperCase();
    if (ENV_ALLOW.has(up) || ENV_PREFIXES.some((p) => up.startsWith(p))) out[k] = v;
  }
  return { ...out, ...extra };
}

/** PATH 目录清单（去空项）。 */
function pathDirs() {
  return String(process.env.PATH || '').split(path.delimiter).filter(Boolean);
}

/**
 * 在 PATH 里找可执行文件（Windows 只认 .exe——execFile 无法直接跑 .cmd/.bat）。
 * v2.2.5 审查 P2：**只接受绝对结果**。PATH 里若有相对项（如 `.`，用户或安装器造成），
 * `path.join('.', 'python.exe')` 会返回相对路径却让调用方以为"已解析"——子进程 cwd
 * 恰是可写的生态 scripts 目录，"当前目录优先"的植入面原样复活。
 */
function findOnPath(name) {
  const exts = process.platform === 'win32'
    ? (/\.(exe|cmd|bat)$/i.test(name) ? [''] : ['.exe'])
    : [''];
  for (const dir of pathDirs()) {
    if (!path.isAbsolute(dir)) continue; // 跳过相对 PATH 项（安全优先）
    for (const ext of exts) {
      const cand = path.join(dir, name + ext);
      try {
        if (existsSync(cand) && path.isAbsolute(cand)) return cand;
      } catch (_) { /* 无权限的目录直接跳过 */ }
    }
  }
  return null;
}

/**
 * 解析 Python 解释器为**绝对路径**。
 * 裸名交给 CreateProcess/execvp 会按"当前目录优先"搜索——插件的子进程 cwd 正是
 * 生态 scripts 目录（可写仓库），等于把任意代码执行面敞给任何能往该目录落文件的进程。
 * 解析不到时退回原值（保持 fail-open：让 spawn 自己报可读错误），并在 how 里说明。
 * 配置值若"含分隔符/盘符却不是绝对路径"（如 `sub\python.exe`、`C:tools\python.exe`），
 * 会被子进程按自己的 cwd 解析 → 同一条植入面：此类配置**忽略并回落 PATH**（how 里说明）。
 */
function resolvePythonInterpreter(configured) {
  const cand = String(configured ?? '').trim() || 'python';
  if (path.isAbsolute(cand)) return { cmd: cand, resolved: true, how: 'configured-absolute' };
  const looksPathed = /[\\/]/.test(cand) || /^[A-Za-z]:/.test(cand);
  if (looksPathed) {
    const fallback = findOnPath('python');
    if (fallback) return { cmd: fallback, resolved: true, how: 'ignored-relative-config' };
    return { cmd: cand, resolved: false, how: 'unresolved-relative' };
  }
  const direct = findOnPath(cand);
  if (direct) return { cmd: direct, resolved: true, how: 'PATH' };
  if (cand === 'python') {
    for (const alias of (process.platform === 'win32' ? ['python3', 'py'] : ['python3'])) {
      const hit = findOnPath(alias);
      if (hit) return { cmd: hit, resolved: true, how: 'PATH-alias' };
    }
  }
  return { cmd: cand, resolved: false, how: 'unresolved' };
}

/** top 归一：非数字/非正数/超大 → 保守默认（原实现会把 "-1" 原样传给 argparse）。 */
function normalizeTop(top, fallback = 3, max = 20) {
  const n = Number.parseInt(String(top ?? ''), 10);
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.min(n, max);
}

/** CLI 参数：选项在前、`--` 分隔、位置参数在后（位置参数以 '-' 开头也不会被当选项）。 */
function buildScriptArgs(positional, top) {
  return ['--top', String(normalizeTop(top)), '--', String(positional ?? '')];
}

function firstLine(text) {
  return String(text || '').split(/\r?\n/).find((l) => l.trim()) || '';
}

/** 统一子进程结果形状（execFile / spawnSync 共用）。status：spawnSync 路径的退出码
 *  （非零退出时 r.error 为 null、r.status 才是码——v2.2.5 回归实测曾把它吞成 0）。 */
function shapeResult(err, stdout, stderr, status = null) {
  const out = String(stdout ?? '').trim();
  const errText = String(stderr ?? '').trim();
  if (!err) return { out, err: errText, code: (typeof status === 'number' ? status : 0), failed: false };
  const numeric = typeof err.code === 'number' ? err.code : null;
  if (numeric === null) {
    // spawn 层失败（ENOENT/超时被杀/权限）——不回显 err.message（含完整命令行与路径）
    const why = err.code === 'ENOENT' ? '未找到解释器或脚本'
      : (err.killed || err.signal) ? `子进程被终止（${err.signal || 'timeout'}）`
        : `子进程启动失败（${err.code || 'unknown'}）`;
    return { out, err: errText || why, code: null, failed: true };
  }
  return { out, err: errText, code: numeric, failed: false }; // 正常跑完但退出码非 0
}

function spawnOptions({ cwd, env, timeout = SPAWN_TIMEOUT_MS } = {}) {
  return { cwd, timeout, windowsHide: true, env, maxBuffer: MAX_BUFFER, encoding: 'utf8' };
}

/** 异步执行（cordis 版用；不阻塞宿主事件循环）。 */
function runPython(python, scriptPath, args, opts = {}) {
  return new Promise((resolve) => {
    execFile(python, [scriptPath, ...args], spawnOptions(opts),
      (err, stdout, stderr) => resolve(shapeResult(err, stdout, stderr)));
  });
}

/** 同步执行（EAC 版用；该 SDK 的 provideContext 契约是同步返回）。 */
function runPythonSync(python, scriptPath, args, opts = {}) {
  const r = spawnSync(python, [scriptPath, ...args], spawnOptions(opts));
  return shapeResult(r.error || null, r.stdout, r.stderr, typeof r.status === 'number' ? r.status : null);
}

/**
 * 检索类工具的结果文案。
 * 退出码语义（核心 CLI 契约）：0=命中 1=未命中 2=关键词提取失败。
 * 旧实现把"非 0"一律当"检索不可用"，直接丢掉 stdout——agent 会把"库里没有"
 * 误判成"检索坏了"（v2.2.5 审计实测复现）。
 */
function toolResultText(res) {
  if (!res) return '（检索不可用：内部错误）';
  if (res.out) return res.out; // CLI 的 stdout 即权威（含"未命中：xxx"）
  if (res.failed) return `（检索不可用：${res.err || '子进程启动失败'}）`;
  if (res.code === 1) return '（未命中：经验库暂无相关条目）';
  if (res.code === 2) return `（未提取到关键词：${firstLine(res.err) || '文本太短或无有效 token'}）`;
  if (res.code === 0) return '（无输出）';
  return `（检索失败 exit=${res.code}${res.err ? '：' + firstLine(res.err) : ''}）`;
}

module.exports = {
  SPAWN_TIMEOUT_MS,
  ENV_ALLOW,
  ENV_PREFIXES,
  curatedEnv,
  buildScriptArgs,
  findOnPath,
  firstLine,
  normalizeTop,
  resolvePythonInterpreter,
  runPython,
  runPythonSync,
  shapeResult,
  toolResultText,
};
