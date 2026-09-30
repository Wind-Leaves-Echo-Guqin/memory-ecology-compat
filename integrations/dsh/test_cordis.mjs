/**
 * package-cordis（官方桌面版适配器）回归（v2.2.5）。
 *
 * 这里只测**纯函数面**（不需要 dsh 宿主，也不 spawn 子进程）：
 *   1. 注入消息的 source.kind 必须是 producer 自己的名字 —— 内核 v4 会拒绝
 *      {kind:'plugin'}（"format v4 message requires a producer-owned source kind"）。
 *      2026-09-30 审计：开源发布树仍是旧写法 → 注入通道在 dsh 0.2.0-rc.2 上必然被拒。
 *   2. dataRoot 默认值必须与核心 scripts/lib/config.py 的 hermes_root() 同口径：
 *      src/memory_ecology 布局下要再上一级（Q30 的坑，适配器曾把它带回）。
 *
 * 用法: node test_cordis.mjs（cwd=adapters/dsh）
 */
import assert from 'node:assert';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const mod = await import(new URL('./package-cordis/index.js', import.meta.url).href);
const { buildInjectionMessage, dataRoot, name } = mod;

let failed = 0;
let total = 0;
function check(label, fn) {
  total += 1;
  try {
    fn();
    console.log(`✅ ${label}`);
  } catch (e) {
    failed += 1;
    console.log(`❌ ${label} — ${e && e.message}`);
  }
}

check('插件名 = eco-note（同时是 patch insert id 与 source kind）', () => {
  assert.strictEqual(name, 'eco-note');
});

check('注入消息 source.kind 是 producer 自己的名字，绝不是 "plugin"', () => {
  let captured = null;
  const createUserMessage = (input) => { captured = input; return input; };
  const text = '【经验参考】(只读参考, 可忽略) 示例';
  buildInjectionMessage(createUserMessage, text);
  assert.notStrictEqual(captured.source.kind, 'plugin', '内核 v4 会拒绝 kind:"plugin"');
  assert.strictEqual(captured.source.kind, 'eco-note');
  assert.strictEqual(captured.source.form, 'snapshot');
  assert.strictEqual(captured.role, undefined); // 角色由宿主工厂补齐
  assert.strictEqual(captured.content[0].text, text);
  assert.strictEqual(captured.source.sections[0].name, 'eco-note');
  assert.strictEqual(captured.source.sections[0].text, text);
});

check('dataRoot：scripts/ 布局 → scripts 的上一级', () => {
  const prev = process.env.MEMORY_ECOLOGY_ROOT;
  delete process.env.MEMORY_ECOLOGY_ROOT;
  try {
    assert.strictEqual(dataRoot({}, path.join('/repo', 'scripts')), path.normalize('/repo'));
  } finally {
    if (prev !== undefined) process.env.MEMORY_ECOLOGY_ROOT = prev;
  }
});

check('dataRoot：src/memory_ecology 布局 → 再上一级（与 lib/config.py 同口径）', () => {
  const prev = process.env.MEMORY_ECOLOGY_ROOT;
  delete process.env.MEMORY_ECOLOGY_ROOT;
  try {
    const scripts = path.join('/repo', 'src', 'memory_ecology');
    assert.strictEqual(dataRoot({}, scripts), path.join('/repo'));
  } finally {
    if (prev !== undefined) process.env.MEMORY_ECOLOGY_ROOT = prev;
  }
});

check('dataRoot：显式配置/环境变量优先', () => {
  const prev = process.env.MEMORY_ECOLOGY_ROOT;
  try {
    assert.strictEqual(dataRoot({ memoryEcologyRoot: '/explicit' }, '/repo/scripts'), '/explicit');
    process.env.MEMORY_ECOLOGY_ROOT = '/from-env';
    assert.strictEqual(dataRoot({ memoryEcologyRoot: '/explicit' }, '/repo/scripts'), '/from-env');
  } finally {
    if (prev === undefined) delete process.env.MEMORY_ECOLOGY_ROOT;
    else process.env.MEMORY_ECOLOGY_ROOT = prev;
  }
});

check('模块可被宿主以 ESM 导入且暴露契约（name/inject/apply）', () => {
  assert.deepStrictEqual(mod.inject, ['tools', 'agents']);
  assert.strictEqual(typeof mod.apply, 'function');
  assert.strictEqual(typeof mod.buildInjectionMessage, 'function');
  assert.strictEqual(typeof mod.dataRoot, 'function');
});

// ── apply() 级端到端（假宿主包 + 桩 Python，不碰真 dsh、不碰真数据根）──────────
// 这一段是 v2.2.5 的核心护栏：激活面（两个工具注册）+ 注入面（消息形状）
// + 工具面（退出码语义穿过适配器）。三者都曾在真机上出过问题。
const fs = await import('node:fs');
const os = await import('node:os');
const common = (await import(new URL('./package/lib/common.js', import.meta.url).href)).default;

const py = common.resolvePythonInterpreter('python');
if (!py.resolved) {
  // 审查 P2：核心护栏不得静默跳过 —— 缺依赖的机器上"全绿但零覆盖"比红灯更危险
  console.log('❌ 环境缺 python：apply() 端到端是 v2.2.5 关键护栏，不得跳过。'
    + '请装 Python，或用 ECO_PYTHON 指向绝对路径的解释器。');
  process.exit(1);
}
{
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'eco-cordis-'));
  const hostRoot = path.join(tmp, 'host', 'node_modules');
  const mkPkg = (name, body) => {
    const dir = path.join(hostRoot, '@deepseek-ai', name);
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, 'package.json'),
      JSON.stringify({ name: `@deepseek-ai/${name}`, version: '0.0.0-test', main: 'index.mjs' }));
    fs.writeFileSync(path.join(dir, 'index.mjs'), body);
  };
  mkPkg('dsh-tools', 'export const defineTool = (def) => def;\n');
  mkPkg('dsh-llm', 'export const createUserMessage = (input) => ({ ...input, role: "user", id: "msg-1" });\n');

  const pythonDir = path.join(tmp, 'pyside');
  const scriptsDir = path.join(tmp, 'scripts');
  const root = path.join(tmp, 'root');
  fs.mkdirSync(pythonDir, { recursive: true });
  fs.mkdirSync(scriptsDir, { recursive: true });
  fs.mkdirSync(path.join(root, 'experiences'), { recursive: true });
  fs.writeFileSync(path.join(pythonDir, 'eco_note_dsh_context.py'),
    'import json,sys\nprint(json.dumps({"inject": "【经验参考】(只读参考, 可忽略) 桩注入",'
    + ' "n_errors": 1, "reason": "ok", "episodes": ["e1"], "ms": 1, "schema": "v4",'
    + ' "session": "session-abc"}))\n');
  fs.writeFileSync(path.join(scriptsDir, 'eco_note_query.py'),
    'import sys\nprint("未命中：桩关键词")\nsys.exit(1)\n');
  fs.writeFileSync(path.join(scriptsDir, 'eco_note_error_query.py'),
    'import sys\nprint("未能从文本提取关键词（太短或无有效 token）")\nsys.exit(2)\n');

  process.env.DSH_ECO_HOST_MODULES = hostRoot;
  process.env.ECO_PYTHON_DIR = pythonDir;
  process.env.ECO_SCRIPTS_DIR = scriptsDir;
  process.env.MEMORY_ECOLOGY_ROOT = root;
  try {
    const registered = [];
    const handlers = new Map();
    const logs = [];
    const ctx = {
      logger: { info: (m) => logs.push(['info', String(m)]), debug: (m) => logs.push(['debug', String(m)]) },
      tools: { register: (def) => registered.push(def) },
      on: (event, fn) => handlers.set(event, fn),
    };
    await mod.apply(ctx, { jsThrottleMs: 0 });

    check('apply()：注册两个工具且名字/契约正确', () => {
      assert.deepStrictEqual(registered.map((d) => d.name), ['eco_note_query', 'eco_note_error_query']);
      assert.strictEqual(typeof registered[0].execute, 'function');
      assert.strictEqual(registered[0].output.render({}, { result: 'x' })[0].text, 'x');
    });

    check('apply()：激活日志带解释器/宿主包/数据根（可观测性回归）', () => {
      const line = logs.map((l) => l[1]).find((m) => m.includes('host='));
      assert.ok(line, '激活时必须打出诊断行');
      assert.match(line, /root=/);
      assert.match(line, /scripts=/);
    });

    const handler = handlers.get('agent/pre-step');
    assert.strictEqual(typeof handler, 'function');

    const decision = await handler(
      { agent: { id: 'session-abc' }, signal: { aborted: false } },
      async () => ({ kind: 'enter', messages: [] }),
    );
    check('pre-step：注入消息形状合法（v4 校验面）', () => {
      assert.strictEqual(decision.kind, 'enter');
      assert.strictEqual(decision.messages.length, 1);
      const msg = decision.messages[0];
      assert.strictEqual(msg.role, 'user');
      assert.notStrictEqual(msg.source.kind, 'plugin');
      assert.strictEqual(msg.source.kind, 'eco-note');
      assert.match(msg.content[0].text, /【经验参考】/);
    });

    const toolOut = await registered[0].execute({ keyword: '任意' });
    check('工具面：未命中（exit 1）不再被报成"检索不可用"（钉住 CLI 自己的 stdout）', () => {
      // 钉住桩脚本自己的文本：若有人退回"丢 stdout、只留退出码兜底文案"，此断言必炸
      // （旧写法只 match /未命中/，而 toolResultText 的 exit-1 兜底串也含"未命中"）
      assert.match(toolOut.result, /桩关键词/, `应保留 CLI stdout，实际: ${toolOut.result}`);
      assert.ok(!/检索不可用/.test(toolOut.result), `实际: ${toolOut.result}`);
    });

    const toolOut2 = await registered[1].execute({ text: 'x' });
    check('工具面：关键词提取失败（exit 2）有独立文案', () => {
      assert.match(toolOut2.result, /未能从文本提取关键词|未提取到关键词/);
      assert.ok(!/检索不可用/.test(toolOut2.result), `实际: ${toolOut2.result}`);
    });
  } finally {
    delete process.env.DSH_ECO_HOST_MODULES;
    delete process.env.ECO_PYTHON_DIR;
    delete process.env.ECO_SCRIPTS_DIR;
    delete process.env.MEMORY_ECOLOGY_ROOT;
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

console.log(failed === 0 ? `\n全部通过（${total} 项）` : `\n失败 ${failed}/${total} 项`);
process.exit(failed === 0 ? 0 : 1);
