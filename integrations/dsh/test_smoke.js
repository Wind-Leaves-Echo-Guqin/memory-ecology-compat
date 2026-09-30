'use strict';
/** eco-note-dsh 冒烟测试（mock ctx，零 dsh 依赖）：node test_smoke.js，退出码 0=全过 */
const path = require('path');
const mod = require(path.join(__dirname, 'package', 'index.js'));

let failures = 0;
function check(name, cond) {
  console.log(`${cond ? '✅' : '❌'} ${name}`);
  if (!cond) failures += 1;
}

// mock Extension SDK ctx
function makeMockCtx() {
  return {
    tools: {},
    _ctxFn: null,
    logs: [],
    registerTool(name, meta, handler) { this.tools[name] = { meta, handler }; },
    provideContext(fn) { this._ctxFn = fn; },
    on(_evt, _cb) {},
    log(level, msg) { this.logs.push([level, String(msg)]); },
  };
}

const ctx = makeMockCtx();
mod.activate(ctx);

check('注册了 eco_note_query 工具', !!ctx.tools.eco_note_query);
check('注册了 eco_note_error_query 工具', !!ctx.tools.eco_note_error_query);
check('注册了 provideContext 回合贡献', typeof ctx._ctxFn === 'function');

// 注入路径：mock runContext（契约：返回 {inject, reason, ...} 载荷对象）
const origRun = mod._internals.runContext;
mod._internals.runContext = () => ({ inject: '【经验参考】(只读参考, 可忽略) 测试注入', reason: 'ok', n_errors: 1, schema: 'v4', session: 's1', ms: 5 });
check('有注入载荷时返回文本', ctx._ctxFn() === '【经验参考】(只读参考, 可忽略) 测试注入');
check('探针 reason 落日志（审查 #13 的 EAC 侧）', ctx.logs.some(([, m]) => m.includes('reason=ok') && m.includes('n_errors=1')));

// fail-open：无载荷/异常 → null（绝不阻塞宿主回合）
mod._internals.runContext = () => ({ inject: null, reason: 'cooldown' });
check('无载荷返回 null', ctx._ctxFn() === null);
check('无命中/冷却也记 reason', ctx.logs.some(([, m]) => m.includes('reason=cooldown')));
mod._internals.runContext = () => { throw new Error('boom'); };
check('Python 侧异常 fail-open 不抛出', ctx._ctxFn() === null);
mod._internals.runContext = origRun;

// 工具 handler：空参返回 error 字段（不抛异常）
const bad = mod._internals.toolQuery({});
check('toolQuery 空参返回 error 字段', !!(bad && bad.error));
const bad2 = mod._internals.toolErrorQuery({});
check('toolErrorQuery 空参返回 error 字段', !!(bad2 && bad2.error));

console.log(failures === 0 ? '\n全部通过' : `\n失败 ${failures} 项`);
process.exit(failures === 0 ? 0 : 1);
