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
 *   - 单源：Python 侧直接复用 ../package/python/，本包零复制
 *   - 不注册任何 HTTP 路由
 *
 * 与 EAC 版的差异：
 *   - JS 侧加 60s 兜底节流（桌面长驻进程每步 spawn 开销约束）；
 *     权威窗口/冷却（10min/15min）仍在 Python 状态机
 *   - 宿主包（dsh-tools / dsh-llm）运行期动态解析：link: 安装的包从仓库真实路径
 *     加载，Node 裸导入走不到宿主依赖树（our-free-model 靠零裸导入绕开同题）；
 *     此处用 createRequire 从宿主 node_modules 树定位后 import()。
 *     tools.register 只做结构校验、createUserMessage 是纯工厂——均跨实例安全。
 *
 * 环境变量（与 EAC 版同名同义，另加一个）：
 *   ECO_PYTHON           python 可执行文件（默认 "python"）
 *   ECO_SCRIPTS_DIR      生态核心 scripts 目录（默认沿父目录链探测）
 *   MEMORY_ECOLOGY_ROOT  数据根（共享根模式指向主宿主数据根）
 *   ECO_PYTHON_DIR       Python 侧脚本目录（默认 ../package/python/，单源复用）
 *   DSH_ECO_HOST_MODULES 宿主 node_modules 根（默认探测 DSH_HOME 与 ~/.dsh）
 * 以上均可被 patch config 同名字段覆盖（camelCase：ecoPython / ecoScriptsDir /
 * memoryEcologyRoot / pythonDir / hostModulesDir）。
 */
import { execFile } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

/** Cordis 插件名（= patch insert id）。 */
const name = 'eco-note';
/** tools：注册检索工具；agents：订阅 agent/pre-step。缺一则整插件挂起（fail-safe）。 */
const inject = ['tools', 'agents'];

const PKG_DIR = path.dirname(fileURLToPath(import.meta.url));
const SPAWN_TIMEOUT_MS = 8000;
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
 * 从宿主树解析并 import 一个包（ESM）。
 * link: 安装的包从仓库真实路径加载，裸导入解析不到宿主依赖——
 * 这里显式借用宿主树上的解析起点。
 */
async function importHostModule(config, spec) {
  for (const root of hostModulesRoots(config)) {
    if (!existsSync(path.join(root, '@deepseek-ai', 'dsh-tools'))) continue;
    const req = createRequire(path.join(root, '_resolve_.js'));
    const entry = req.resolve(spec);
    return await import(pathToFileURL(entry).href);
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

function dataRoot(config, scriptsDir) {
  // 派生默认与 scripts/lib/config.py 一致（scripts 的上一级 = 数据根）
  return process.env.MEMORY_ECOLOGY_ROOT
    || config?.memoryEcologyRoot
    || path.join(scriptsDir, '..');
}

/** fail-open 的 Python spawn：任何失败 resolve(null+err)，绝不 reject。 */
function runPython(pythonDir, python, scriptsDir, root, script, args) {
  return new Promise((resolve) => {
    execFile(python, [path.join(pythonDir, script), ...args], {
      cwd: scriptsDir,
      timeout: SPAWN_TIMEOUT_MS,
      windowsHide: true,
      env: { ...process.env, ECO_SCRIPTS_DIR: scriptsDir, MEMORY_ECOLOGY_ROOT: root },
    }, (err, stdout) => {
      resolve(err || !stdout ? { out: null, err: err?.message ?? '（空输出）' } : { out: stdout, err: null });
    });
  });
}

/** 工具：经验关键词检索（只读，文本输出 agent 可读）。 */
async function toolQuery(spawnScripts, args) {
  const kw = String(args?.keyword ?? '').trim();
  if (!kw) return { result: '（keyword 必填）' };
  const top = String(args?.top ?? 3);
  // Q28 同源教训：检索 CLI 在核心 scripts 目录，不在本包 python/ 下
  const { out, err } = await spawnScripts('eco_note_query.py', [kw, '--top', top]);
  return { result: out?.trim() || `（检索不可用：${err}）` };
}

/** 工具：报错文本 → 根因分层经验检索（只读）。 */
async function toolErrorQuery(spawnScripts, args) {
  const text = String(args?.text ?? '').trim();
  if (!text) return { result: '（text 必填）' };
  const top = String(args?.top ?? 3);
  const { out, err } = await spawnScripts('eco_note_error_query.py', [text, '--top', top]);
  return { result: out?.trim() || `（检索不可用：${err}）` };
}

let lastProbe = 0;

/** 注入探针：节流 + spawn 会话报错扫描。返回注入文本或 null。 */
async function contextProbe(spawn) {
  const now = Date.now();
  if (now - lastProbe < jsThrottleMs) return null;
  lastProbe = now;
  const { out } = await spawn('eco_note_dsh_context.py', []);
  if (!out) return null;
  try {
    const payload = JSON.parse(out.trim());
    return payload?.inject ?? null;
  } catch {
    return null;
  }
}

export async function apply(ctx, config = {}) {
  const { defineTool } = await importHostModule(config, '@deepseek-ai/dsh-tools');
  const { createUserMessage } = await importHostModule(config, '@deepseek-ai/dsh-llm');

  jsThrottleMs = Number(config?.jsThrottleMs ?? process.env.ECO_JS_THROTTLE_MS ?? JS_THROTTLE_MS_DEFAULT);
  if (!Number.isFinite(jsThrottleMs) || jsThrottleMs < 0) jsThrottleMs = JS_THROTTLE_MS_DEFAULT;

  const pythonDir = locatePythonDir(config);
  const python = process.env.ECO_PYTHON || config?.ecoPython || 'python';
  const scriptsDir = locateScriptsDir(config);
  const root = dataRoot(config, scriptsDir);
  const spawn = (script, args) => runPython(pythonDir, python, scriptsDir, root, script, args);
  // 检索类脚本在核心 scripts 目录（Q28 教训）；会话扫描脚本在本包 python/ 下
  const spawnScripts = (script, args) => runPython(scriptsDir, python, scriptsDir, root, script, args);

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
  ctx.on('agent/pre-step', async ({ signal }, next) => {
    const decision = await next();
    if (decision.kind === 'reject' || signal?.aborted) return decision;
    try {
      const text = await contextProbe(spawn);
      if (!text) return decision;
      return {
        kind: 'enter',
        messages: [...decision.messages, createUserMessage({
          content: [{ type: 'text', text }],
          source: { kind: 'plugin', plugin: name, form: 'snapshot', sections: [{ name, text }] },
        })],
      };
    } catch {
      return decision;
    }
  }, { prepend: true });
}

export { name, inject };
