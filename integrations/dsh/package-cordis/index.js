/**
 * eco-note-dsh-cordis — 记忆生态·兼容版 dsh 官方桌面版（cordis 体系）适配器
 *
 * 对应 EAC Extension SDK 版适配器（../package/）的两通道，官方 API 对应物：
 *   - 工具：ctx.tools.register(defineTool(...)) × 2
 *     （EAC 版 ctx.registerTool 的对应；契约照抄官方 dsh-tool-todo）
 *   - 注入：ctx.on('agent/pre-step', ..., { prepend: true })
 *     （EAC 版 provideContext 的对应；模式照抄官方 dsh-time-context：
 *      next() 取上游决定 → 命中则 append 插件署名 user message）
 *
 * 纪律与 EAC 版一致：
 *   - fail-open：Python 任何失败 → 注入返回原决定 / 工具返兜底文案，绝不阻塞宿主
 *   - 只读消费：不写经验内容（仅 experiences/.dsh_inject_state.json 注入状态）
 *   - 单源：Python 侧复用 ../package/python/，子进程语义复用 ../package/lib/common.js
 *   - 不注册任何 HTTP 路由
 *
 * v2.2.5 修复（对应 2026-09-30 兼容版审计报告）：
 *   - 注入消息 source.kind 必须是 producer 自己的名字（内核 v4：会拒绝 {kind:'plugin'}）
 *   - 检索工具 stdout 优先 + 退出码分档（未命中 exit 1 ≠ 检索不可用）
 *   - 位置参数走 `--` 分隔（检索词以 '-' 开头不再被 argparse 当选项）
 *   - 解释器解析为绝对路径（堵子进程 cwd 植入 python.exe → 任意执行，CWE-427）
 *   - 子进程最小环境（默认不透传凭据类变量）
 *   - 会话身份取 agent.id（原实现按 mtime 猜"当前会话"）；节流按会话分桶
 *   - 探针 reason 落 ctx.logger（原实现丢弃 → 装错/缺库/超时/无命中一律静默）
 *
 * 与 EAC 版的差异：
 *   - JS 侧加 60s 兜底节流（桌面长驻进程每步 spawn 开销约束）；
 *     权威窗口/冷却（10min/15min）仍在 Python 状态机，且两者都按会话隔离
 *   - 宿主包（dsh-tools / dsh-llm）运行期动态解析：link: 安装的包从仓库真实路径
 *     加载，Node 裸导入走不到宿主依赖树（our-free-model 靠零裸导入绕开同题）；
 *     此处用 createRequire 从宿主 node_modules 树定位后 import()。
 *     tools.register 只做结构校验、createUserMessage 是纯工厂——均跨实例安全。
 *
 * 环境变量（与 EAC 版同名同义，另加若干）：
 *   ECO_PYTHON           python 可执行文件（默认 "python"；v2.2.5 起解析为绝对路径）
 *   ECO_SCRIPTS_DIR      生态核心 scripts 目录（默认沿父目录链探测）
 *   MEMORY_ECOLOGY_ROOT  数据根（共享根模式指向主宿主数据根）
 *   ECO_PYTHON_DIR       Python 侧脚本目录（默认 ../package/python/，单源复用）
 *   DSH_ECO_HOST_MODULES 宿主 node_modules 根（默认探测 DSH_HOME 与 ~/.dsh）
 *   ECO_JS_THROTTLE_MS   JS 兜底节流（默认 60000ms，0=每步都探）
 *   ECO_KEEP_FULL_ENV=1  透传完整宿主环境给子进程（默认只透传系统+生态变量）
 * 以上均可被 patch config 同名字段覆盖（camelCase：ecoPython / ecoScriptsDir /
 * memoryEcologyRoot / pythonDir / hostModulesDir / jsThrottleMs / keepFullEnv）。
 */
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import common from '../package/lib/common.js';

/** Cordis 插件名（= patch insert id，也是注入消息的 producer source kind）。 */
const name = 'eco-note';
/** tools：注册检索工具；agents：订阅 agent/pre-step。缺一则整插件挂起（fail-safe）。 */
const inject = ['tools', 'agents'];

const PKG_DIR = path.dirname(fileURLToPath(import.meta.url));
// JS 侧兜底节流（默认 60s）；patch config jsThrottleMs / env ECO_JS_THROTTLE_MS 可调，
// 0 = 每步都探（验证用）。权威窗口/冷却（10min/15min）仍在 Python 状态机。
const JS_THROTTLE_MS_DEFAULT = 60_000;
let jsThrottleMs = JS_THROTTLE_MS_DEFAULT;

/** 宿主 node_modules 根候选（有 dsh-tools 的那个胜出）。 */
function hostModulesRoots(config) {
  const home = os.homedir();
  const env = process.env.DSH_ECO_HOST_MODULES;
  const dshHome = process.env.DSH_HOME;
  return [
    config?.hostModulesDir,
    env,
    dshHome ? path.join(dshHome, 'profiles', 'node_modules') : null,
    path.join(home, '.dsh', 'profiles', 'node_modules'),
  ].filter(Boolean);
}

/**
 * 宿主包版本（沿入口路径向上找最近的 package.json）。
 * 为什么值得记：本机实测 ~/.dsh/profiles/node_modules/@deepseek-ai/* 是指向
 * npm 全局 dsh（0.1.1-rc.2）的 junction，而运行内核是桌面版 0.2.0-rc.2——
 * 插件加载的宿主包与内核不同源。当前无碍（register 仅结构校验、createUserMessage
 * 是纯工厂），但 v2.2.4 那次 source kind 事故正是这类漂移的产物，故把版本打进日志。
 */
function pkgVersionOf(entryPath) {
  let dir = path.dirname(entryPath);
  for (let i = 0; i < 4; i++) {
    const pj = path.join(dir, 'package.json');
    if (existsSync(pj)) {
      try {
        const { name: pkgName, version } = JSON.parse(readFileSync(pj, 'utf8'));
        return `${pkgName}@${version}`;
      } catch {
        return '';
      }
    }
    const up = path.dirname(dir);
    if (up === dir) break;
    dir = up;
  }
  return '';
}

/**
 * 从宿主树解析并 import 一个包（ESM）。
 * link: 安装的包从仓库真实路径加载，裸导入解析不到宿主依赖——
 * 这里显式借用宿主树上的解析起点。
 * v2.2.5：同时返回真实入口路径与包版本，激活日志据此暴露版本漂移线索。
 */
async function importHostModule(config, spec) {
  for (const root of hostModulesRoots(config)) {
    if (!existsSync(path.join(root, '@deepseek-ai', 'dsh-tools'))) continue;
    const req = createRequire(path.join(root, '_resolve_.js'));
    const entry = req.resolve(spec);
    const mod = await import(pathToFileURL(entry).href);
    return { mod, entry, root, version: pkgVersionOf(entry) };
  }
  throw new Error(
    `eco-note: 未找到宿主 node_modules 树（尝试过：${hostModulesRoots(config).join(' , ') || '（无候选）'}）。`
    + `可设 DSH_ECO_HOST_MODULES 或 patch config hostModulesDir 指向含 @deepseek-ai/dsh-tools 的目录。`,
  );
}

function firstDir(...cands) {
  for (const c of cands) {
    if (c && existsSync(c)) return c;
  }
  return null;
}

function locatePythonDir(config) {
  return firstDir(
    process.env.ECO_PYTHON_DIR,
    config?.pythonDir,
    // 单源：直接复用 EAC 适配器包的 python/（link 安装保持真实路径）
    path.join(PKG_DIR, '..', 'package', 'python'),
  ) ?? path.join(PKG_DIR, '..', 'package', 'python'); // 兜底：让 spawn 报可读错误
}

function locateScriptsDir(config) {
  const detected = firstDir(process.env.ECO_SCRIPTS_DIR, config?.ecoScriptsDir);
  if (detected) return detected;
  // package-cordis → dsh → adapters → 仓库根，两种布局（scripts / src/memory_ecology）
  let base = PKG_DIR;
  for (let i = 0; i < 4; i++) {
    base = path.dirname(base);
    for (const rel of ['scripts', path.join('src', 'memory_ecology')]) {
      const cand = path.join(base, rel);
      if (existsSync(cand)) return cand;
    }
  }
  return PKG_DIR;
}

/**
 * 数据根默认值——必须与核心 scripts/lib/config.py 的 hermes_root() 同口径：
 *   scripts/ 布局            → scripts 的上一级
 *   src/memory_ecology 布局  → 再上一级（config.py 的 _P3.name == "src" 分支）
 * v2.2.5 前这里恒取"上一级"，开源树默认配置下算出 <repo>/src，与核心认定的
 * <repo> 不是同一目录 → 注入永远 no-hit（Q30 修掉的坑被适配器带回）。
 */
export function dataRoot(config, scriptsDir) {
  const explicit = process.env.MEMORY_ECOLOGY_ROOT || config?.memoryEcologyRoot;
  if (explicit) return explicit;
  const up = path.dirname(scriptsDir);
  return path.basename(up).toLowerCase() === 'src' ? path.dirname(up) : up;
}

/**
 * 注入消息构造（纯函数，便于测试断言 producer-owned source kind）。
 * 内核 v4 校验拒绝 {kind:'plugin'}——必须用 producer 自己的名字（= 本插件名）。
 */
export function buildInjectionMessage(createUserMessage, text, pluginName = name) {
  return createUserMessage({
    content: [{ type: 'text', text }],
    source: { kind: pluginName, form: 'snapshot', sections: [{ name: pluginName, text }] },
  });
}

/** 会话文件定位参数：agent.id → --session-id（Python 侧精确匹配；缺则回落 mtime 最新）。 */
function sessionArgs(sessionId) {
  const sid = String(sessionId ?? '').trim();
  return sid ? ['--session-id', sid] : [];
}

/** 工具：经验关键词检索（只读，文本输出 agent 可读）。 */
async function toolQuery(spawnScripts, args) {
  const kw = String(args?.keyword ?? '').trim();
  if (!kw) return { result: '（keyword 必填）' };
  // Q28 同源教训：检索 CLI 在核心 scripts 目录，不在本包 python/ 下
  const res = await spawnScripts('eco_note_query.py', common.buildScriptArgs(kw, args?.top));
  return { result: common.toolResultText(res) };
}

/** 工具：报错文本 → 根因分层经验检索（只读）。 */
async function toolErrorQuery(spawnScripts, args) {
  const text = String(args?.text ?? '').trim();
  if (!text) return { result: '（text 必填）' };
  const res = await spawnScripts('eco_note_error_query.py', common.buildScriptArgs(text, args?.top));
  return { result: common.toolResultText(res) };
}

/** per-session 节流表（原实现是模块级单变量 → 多会话互相压制）。 */
const lastProbe = new Map();
const MAX_THROTTLE_KEYS = 64;

function throttleAllows(key, now) {
  const prev = lastProbe.get(key);
  if (prev !== undefined && now - prev < jsThrottleMs) return false;
  lastProbe.set(key, now);
  if (lastProbe.size > MAX_THROTTLE_KEYS) {
    // 丢最旧的一半，避免长驻进程无界增长
    const entries = [...lastProbe.entries()].sort((a, b) => a[1] - b[1]);
    for (const [k] of entries.slice(0, Math.floor(entries.length / 2))) lastProbe.delete(k);
  }
  return true;
}

/**
 * 注入探针：spawn 会话报错扫描。返回 {text, reason, info}——
 * reason 供日志（no-recent-error / cooldown / no-hit / episodes-seen / ok）。
 */
async function contextProbe(spawn, sessionId) {
  const res = await spawn('eco_note_dsh_context.py', sessionArgs(sessionId));
  if (!res.out) return { text: null, reason: res.failed ? 'probe-failed' : 'no-payload', info: res };
  try {
    const payload = JSON.parse(res.out.trim());
    return { text: payload?.inject ?? null, reason: payload?.reason ?? 'unknown', info: payload };
  } catch {
    return { text: null, reason: 'bad-json', info: null };
  }
}

export async function apply(ctx, config = {}) {
  const tools = await importHostModule(config, '@deepseek-ai/dsh-tools');
  const llm = await importHostModule(config, '@deepseek-ai/dsh-llm');
  const { defineTool } = tools.mod;
  const { createUserMessage } = llm.mod;

  jsThrottleMs = Number(config?.jsThrottleMs ?? process.env.ECO_JS_THROTTLE_MS ?? JS_THROTTLE_MS_DEFAULT);
  if (!Number.isFinite(jsThrottleMs) || jsThrottleMs < 0) jsThrottleMs = JS_THROTTLE_MS_DEFAULT;

  const pythonDir = locatePythonDir(config);
  const py = common.resolvePythonInterpreter(process.env.ECO_PYTHON || config?.ecoPython);
  const scriptsDir = locateScriptsDir(config);
  const root = dataRoot(config, scriptsDir);
  // 诊断线索：解释器是否落到绝对路径、宿主包解析到哪棵树与哪个版本、数据根最终取值
  ctx.logger.info(`eco-note: python=${py.cmd}（${py.how}）`
    + ` | host=${tools.version || tools.entry} | scripts=${scriptsDir} | root=${root}`);

  const spawnWith = (cwd) => (script, args) => common.runPython(py.cmd, path.join(cwd, script), args, {
    cwd,
    env: common.curatedEnv({ ECO_SCRIPTS_DIR: scriptsDir, MEMORY_ECOLOGY_ROOT: root }, config),
  });
  const spawn = spawnWith(pythonDir);        // 会话扫描脚本在包内 python/
  const spawnScripts = spawnWith(scriptsDir); // 检索类脚本在核心 scripts 目录

  ctx.tools.register(defineTool({
    name: 'eco_note_query',
    description: '记忆生态经验库关键词检索（只读）。想查"以前有没有踩过这个坑/这类事怎么做"时用。',
    parameters: {
      keyword: { type: 'string', required: true, description: '检索关键词' },
      // DSL 契约：可选参数省略 required（写了 false 反而是非法 schema）
      top: { type: 'integer', description: '最多返回条数（默认 3）' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: { result: { type: 'string', required: true } },
      },
      render: (_args, value) => [{ type: 'text', text: value.result }],
    },
    execute: (args) => toolQuery(spawnScripts, args),
    presentCall: (args) => ({
      card: 'generic',
      title: '记忆生态经验检索',
      kind: 'other',
      rawInput: args,
    }),
  }));

  ctx.tools.register(defineTool({
    name: 'eco_note_error_query',
    description: '把报错文本交给记忆生态经验库做根因匹配检索（只读）。遇到看不懂的报错时用。',
    parameters: {
      text: { type: 'string', required: true, description: '报错/异常文本' },
      top: { type: 'integer', description: '最多返回条数（默认 3）' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: { result: { type: 'string', required: true } },
      },
      render: (_args, value) => [{ type: 'text', text: value.result }],
    },
    execute: (args) => toolErrorQuery(spawnScripts, args),
    presentCall: (args) => ({
      card: 'generic',
      title: '记忆生态报错根因检索',
      kind: 'other',
      rawInput: args,
    }),
  }));

  // 每步注入：next() 后追加插件署名消息（dsh-time-context 模式）。
  // JS 边界兜底 fail-open：任何异常都返回原决定。
  ctx.on('agent/pre-step', async ({ agent, signal }, next) => {
    const decision = await next();
    if (decision.kind === 'reject' || signal?.aborted) return decision;
    const sessionId = String(agent?.id ?? process.env.DSH_SESSION_ID ?? '');
    const throttleKey = sessionId || '__anonymous__';
    try {
      if (!throttleAllows(throttleKey, Date.now())) return decision;
      const { text, reason, info } = await contextProbe(spawn, sessionId);
      if (!text) {
        // 原实现把 reason 全丢了：装错目录/缺 zstandard/超时/无命中看起来一模一样
        ctx.logger.debug(`eco-note 探针: reason=${reason}`
          + ` n_errors=${info?.n_errors ?? '-'} schema=${info?.schema ?? '-'}`
          + ` session=${info?.session ?? (sessionId || '-')} ms=${info?.ms ?? '-'}`);
        return decision;
      }
      ctx.logger.debug(`eco-note 注入: episodes=${(info?.episodes || []).length}`
        + ` n_errors=${info?.n_errors ?? '-'} session=${info?.session ?? (sessionId || '-')}`);
      return {
        kind: 'enter',
        messages: [...decision.messages, buildInjectionMessage(createUserMessage, text)],
      };
    } catch (e) {
      ctx.logger.debug(`eco-note 探针异常（fail-open）: ${e?.message ?? e}`);
      return decision;
    }
  }, { prepend: true });
}

export { name, inject };
