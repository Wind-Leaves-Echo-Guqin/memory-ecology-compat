'use strict';
/**
 * adapters/dsh/package/lib/common.js 回归（v2.2.5）。
 *
 * 覆盖 2026-09-30 审计里两条**实测复现**的缺陷：
 *   1. 检索工具把"未命中（CLI exit 1）"当"检索不可用"——旧实现 err||!stdout 直接丢 stdout
 *   2. 检索词以 '-' 开头被 argparse 当选项——实测 keyword="-h" 返回了 argparse 帮助文本
 * 外加解释器绝对路径解析（CWE-427 植入面）与环境裁剪。
 *
 * 用法: node test_common.js（cwd=adapters/dsh）
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const common = require(path.join(__dirname, 'package', 'lib', 'common.js'));

let failed = 0;
let total = 0;
function check(name, fn) {
  total += 1;
  try {
    fn();
    console.log(`✅ ${name}`);
  } catch (e) {
    failed += 1;
    console.log(`❌ ${name} — ${e && e.message}`);
  }
}

// ── 纯函数：参数分隔 / top 归一 / 退出码分档 ──
check('buildScriptArgs 把位置参数放在 -- 之后', () => {
  assert.deepStrictEqual(common.buildScriptArgs('-h', 2), ['--top', '2', '--', '-h']);
  assert.deepStrictEqual(common.buildScriptArgs('记忆生态', undefined), ['--top', '3', '--', '记忆生态']);
});

check('normalizeTop 夹住非法值（原实现把 -1 原样传给 argparse）', () => {
  assert.strictEqual(common.normalizeTop(-1), 3);
  assert.strictEqual(common.normalizeTop('abc'), 3);
  assert.strictEqual(common.normalizeTop(0), 3);
  assert.strictEqual(common.normalizeTop(999), 20);
  assert.strictEqual(common.normalizeTop('5'), 5);
});

check('toolResultText：stdout 优先（未命中不是故障）', () => {
  assert.strictEqual(common.toolResultText({ out: '未命中：记忆生态', code: 1 }), '未命中：记忆生态');
  assert.match(common.toolResultText({ out: '', code: 1 }), /未命中/);
  assert.match(common.toolResultText({ out: '', code: 2, err: '太短' }), /未提取到关键词/);
  assert.match(common.toolResultText({ out: '', code: null, failed: true, err: '未找到解释器或脚本' }),
    /检索不可用/);
  assert.match(common.toolResultText({ out: '', code: 3 }), /检索失败 exit=3/);
});

check('shapeResult：非零退出 ≠ 启动失败', () => {
  const quit = common.shapeResult({ code: 1 }, '未命中：x', '');
  assert.strictEqual(quit.failed, false);
  assert.strictEqual(quit.code, 1);
  const spawnFail = common.shapeResult({ code: 'ENOENT', message: 'Command failed: python x.py' }, '', '');
  assert.strictEqual(spawnFail.failed, true);
  assert.strictEqual(spawnFail.err, '未找到解释器或脚本'); // 不回显完整命令行
});

// ── 环境裁剪 ──
check('curatedEnv 丢弃凭据类变量、保留系统与生态命名空间', () => {
  process.env.MY_TEST_SECRET = 'super-secret';
  process.env.DSH_FAKE_SESSION = 'sid-1';
  process.env.ECO_FAKE_KNOB = 'k';
  try {
    const env = common.curatedEnv({ MEMORY_ECOLOGY_ROOT: '/tmp/root' });
    assert.strictEqual(env.MY_TEST_SECRET, undefined);
    assert.strictEqual(env.DSH_FAKE_SESSION, 'sid-1');
    assert.strictEqual(env.ECO_FAKE_KNOB, 'k');
    assert.strictEqual(env.MEMORY_ECOLOGY_ROOT, '/tmp/root');
    // Windows 大小写不敏感：环境块里的键常常写作 `Path` 而非 `PATH`
    //（发布树实测踩到：写死 env.PATH 会 undefined）。按键名不敏感地比对值。
    const pathKey = Object.keys(env).find((k) => k.toUpperCase() === 'PATH');
    if (pathKey) {
      assert.strictEqual(env[pathKey], process.env[pathKey]);
    } else {
      assert.ok(!process.env.PATH && !process.env.Path, 'PATH 在宿主里存在时，curatedEnv 必须透传它');
    }
  } finally {
    delete process.env.MY_TEST_SECRET;
    delete process.env.DSH_FAKE_SESSION;
    delete process.env.ECO_FAKE_KNOB;
  }
});

check('curatedEnv：keepFullEnv 时全量透传', () => {
  process.env.MY_TEST_SECRET = 'super-secret';
  try {
    const env = common.curatedEnv({}, { keepFullEnv: true });
    assert.strictEqual(env.MY_TEST_SECRET, 'super-secret');
  } finally {
    delete process.env.MY_TEST_SECRET;
  }
});

// ── 解释器解析 ──
check('resolvePythonInterpreter：配置的绝对路径原样返回', () => {
  const abs = process.platform === 'win32' ? 'C:\\Windows\\System32\\cmd.exe' : '/bin/sh';
  const r = common.resolvePythonInterpreter(abs);
  assert.strictEqual(r.cmd, abs);
  assert.strictEqual(r.how, 'configured-absolute');
});

check('resolvePythonInterpreter：PATH 里的裸名被解析成绝对路径', () => {
  const r = common.resolvePythonInterpreter('python');
  if (common.findOnPath('python')) {
    assert.ok(path.isAbsolute(r.cmd), `应解析为绝对路径，实际 ${r.cmd}`);
    assert.strictEqual(r.resolved, true);
  } else {
    assert.strictEqual(r.resolved, false); // 无 python 的环境保持 fail-open
  }
});

check('resolvePythonInterpreter：相对 PATH 项不得产出相对路径（CWE-427 复审项）', () => {
  const prevPath = process.env.PATH;
  let probed;
  try {
    // PATH='.' 时旧实现会返回 'python.exe' 且 resolved:true —— 子进程 cwd 是可写仓库
    process.env.PATH = `.${path.delimiter}${prevPath || ''}`;
    probed = common.resolvePythonInterpreter('python');
  } finally {
    process.env.PATH = prevPath;
  }
  if (probed.resolved) {
    assert.ok(path.isAbsolute(probed.cmd), `PATH 含相对项时仍必须绝对，实际 ${probed.cmd}`);
  }
});

check('resolvePythonInterpreter：含分隔符的非绝对配置值被忽略（不按子进程 cwd 解析）', () => {
  const r = common.resolvePythonInterpreter(path.join('sub', 'python.exe'));
  if (r.resolved) {
    assert.ok(path.isAbsolute(r.cmd), `应回落绝对路径，实际 ${r.cmd}`);
    assert.strictEqual(r.how, 'ignored-relative-config');
  } else {
    assert.strictEqual(r.how, 'unresolved-relative');
  }
});

// ── 端到端（真跑核心 CLI；缺 python/脚本则跳过） ──
// 两种布局都认：开发树 <root>/scripts，发布树 <root>/src/memory_ecology
const repo = path.join(__dirname, '..', '..');
const scriptsDir = ['scripts', path.join('src', 'memory_ecology')]
  .map((rel) => path.join(repo, rel))
  .find((dir) => fs.existsSync(path.join(dir, 'eco_note_query.py'))) || path.join(repo, 'scripts');
const py = common.resolvePythonInterpreter('python');
const canRun = py.resolved && fs.existsSync(path.join(scriptsDir, 'eco_note_query.py'));

if (!canRun) {
  // 审查 P2：这是"未命中 ≠ 检索不可用"和"-- 分隔"两条回归的唯二端到端断言，不得静默跳过
  console.log('❌ 无法运行端到端检索用例（缺 python 或 scripts/eco_note_query.py）——'
    + '这是 v2.2.5 关键护栏，不得跳过。请检查环境/仓库布局。');
  process.exit(1);
}
{
  const os = require('os');
  const tmpRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'eco-common-'));
  fs.mkdirSync(path.join(tmpRoot, 'experiences'), { recursive: true });
  const opts = {
    cwd: scriptsDir,
    env: common.curatedEnv({ ECO_SCRIPTS_DIR: scriptsDir, MEMORY_ECOLOGY_ROOT: tmpRoot }),
  };
  const q = (kw, top) => common.runPythonSync(
    py.cmd, path.join(scriptsDir, 'eco_note_query.py'), common.buildScriptArgs(kw, top), opts);

  check('端到端：未命中 → exit 1 且 stdout 被保留（旧实现报"检索不可用"）', () => {
    const res = q('ZZZ_不存在的关键词_ZZZ', 2);
    assert.strictEqual(res.code, 1, `期望 exit=1，实际 ${res.code} / ${res.err}`);
    assert.match(res.out, /未命中/);
    assert.match(common.toolResultText(res), /未命中/);
  });

  check('端到端：以 "-" 开头的检索词不再被 argparse 当选项', () => {
    const res = q('-h', 2);
    assert.ok(!/usage: eco_note_query/.test(res.out), `不应回显 argparse 帮助：${res.out.slice(0, 60)}`);
    assert.strictEqual(res.code, 1, `" -h" 应作为关键词查不到，实际 exit=${res.code}`);
  });

  fs.rmSync(tmpRoot, { recursive: true, force: true });
}

console.log(failed === 0 ? `\n全部通过（${total} 项）` : `\n失败 ${failed}/${total} 项`);
process.exit(failed === 0 ? 0 : 1);
