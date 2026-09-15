/* ============================================================
   views/firstaid.js — 急救箱页 + 急救模式页（T12/F）
   · 正常入口：顶栏「急救箱」按钮 → goto('firstaid')
   · 急救模式：api 连续失败时 EcoFirstAidMode() 强切本视图（含缓存快照降级）
   ============================================================ */
'use strict';

RENDER.firstaid = async function (v) {
  v.innerHTML = `<div class="vh">急救箱 <small>服务与窗口 · 数据完整性 · 缓存与锁（生产 cron 领地只诊断不动手）</small></div>
    <div class="loading">正在深度自检…</div>`;
  let r;
  try {
    r = (await EcoApi.get('/api/firstaid')).result;
  } catch (e) {
    EcoFirstAidMode();
    return;
  }
  v.innerHTML = `
    <div class="vh">急救箱 <small>诊断于 ${esc((r.ts || '').replace('T', ' '))} · 数据根 ${esc(r.root)}</small></div>
    <div class="row" style="margin-bottom:12px">
      <span class="chip">结论 <b>${r.all_ok ? '✅ 全部通过' : '❌ 有问题'}</b></span>
      <button class="ghost-btn" id="fa-refresh">重新诊断</button>
      <button class="ghost-btn act-btn" data-act="firstaid_fix">一键修复（轻确认）</button>
      <button class="ghost-btn" id="fa-copy">复制诊断报告</button>
    </div>
    <div class="card"><h3>自检项</h3>
      ${r.checks.map(c => `<div class="fa-check">
        <span class="dot ${c.status === 'ok' || c.status === 'fixed' ? 'ok' : 'bad'}"></span>
        <b>${esc(c.name)}</b> <span class="tag ${c.status === 'ok' ? 'ok' : c.status === 'fixed' ? 'warn' : 'bad'}">${faStName(c.status)}</span>
        <div class="faint" style="margin:3px 0 0 14px">${esc(c.detail)}</div>
        ${c.action && c.status === 'fail' ? `<div class="faint" style="margin:2px 0 0 14px">↳ 处置建议：${esc(c.action)}</div>` : ''}
      </div>`).join('')}
    </div>
    ${r.fixes && r.fixes.length ? `<div class="card"><h3>本次已执行修复</h3>
      ${r.fixes.map(f => `<div class="l1-entry">🔧 ${esc(f)}</div>`).join('')}</div>` : ''}
    <div class="card"><h3>急救箱范围说明</h3>
      <div class="empty-ok">
        · 修复范围：端口/旧进程冲突 · 数据根与关键文件 · eco.db/executions.db 完整性（quick_check）· 检索 CLI 与索引 · 陈旧锁（&gt;2h）· 报告缺失提示<br>
        · 生产 cron 领地：<b>只诊断并给出修复命令（hermes cron run/edit），绝不动手</b><br>
        · 桌面双入口：「生态急救箱.cmd」可在 GUI 服务死亡时独立使用（自检 → 修复 → 重启服务 → 开窗）
      </div></div>`;
  $('#fa-refresh').onclick = () => route();
  $('#fa-copy').onclick = () => ecoCopy(JSON.stringify(r, null, 2), '诊断报告已复制');
  const fixBtn = $('.act-btn', v);
  if (fixBtn) fixBtn.onclick = () => runAction('firstaid_fix', {});
};

function faStName(s) {
  return { ok: '正常', fixed: '已修复', fail: '异常' }[s] || s;
}

/* 急救模式页：接口连续失败时强切（F4） */
function EcoFirstAidMode() {
  if (EcoApi) EcoApi.dead = true;
  const v = document.getElementById('view');
  S.view = 'firstaid';
  $$('#nav a').forEach(a => a.classList.toggle('on', a.dataset.view === 'firstaid'));
  const snap = EcoApi.snapshot ? EcoApi.snapshot('/api/overview') : null;
  v.innerHTML = `
    <div class="alarm" style="border-color:var(--bad);color:var(--bad)">🧰 <b>急救模式</b>——后端接口连续失败（服务可能已退出）。界面数据停留在最近一次快照。</div>
    <div class="vh">急救箱 · 断连兜底</div>
    <div class="card"><h3>恢复步骤</h3>
      <div class="empty-ok" style="text-align:left">
      1️⃣ 双击桌面「<b>生态急救箱.cmd</b>」——自检 + 修复 + 自动重启服务并开窗；<br>
      2️⃣ 或手动：python integrations/gui/eco_gui.py（观测舱目录）；<br>
      3️⃣ 服务恢复后点下方「重试连接」。
      </div>
      <div class="row" style="margin-top:10px">
        <button class="gate-go" id="fa-retry">重试连接</button>
        <button class="ghost-btn" id="fa-copy-snap">复制诊断信息</button>
      </div>
    </div>
    ${snap ? `<div class="card"><h3>上次成功快照 <small>${esc(snap.ts.replace('T', ' '))}（降级显示，非实时）</small></h3>
      <div class="empty-ok" style="text-align:left">数据根：${esc((snap.data && snap.data.root) || '—')} ·
      生态版本：${esc((snap.data && snap.data.version) || '—')}</div></div>` : ''}`;
  $('#fa-retry').onclick = async () => {
    try {
      await EcoApi.get('/api/healthz');
      EcoApi.failStreak = 0; EcoApi.dead = false;
      toast('服务已恢复', true);
      S.cache = {};
      route(); loadStatus(); Bell.refresh();
    } catch (e) { toast('仍无法连接服务——请用桌面急救箱修复', false); }
  };
  $('#fa-copy-snap').onclick = () => ecoCopy(JSON.stringify({ ts: new Date().toISOString(), snapshot: snap, ua: navigator.userAgent }, null, 2), '诊断信息已复制');
}
window.EcoFirstAidMode = EcoFirstAidMode;
