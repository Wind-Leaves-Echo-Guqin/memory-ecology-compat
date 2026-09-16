/* ============================================================
   views/timeline.js — 时间线 v0.3（T9 四形态）
   图表模式：泳道图 + 按日直方图 + 演化回放；文本模式 = v0.2 文本行。
   图/文数据同源（/api/logs），条数一致。
   ============================================================ */
'use strict';

RENDER.timeline = async function (v) {
  const [d, l2] = await Promise.all([
    cached('logs', 8000, () => EcoApi.get('/api/logs')),
    cached('slugs', 30000, async () => (await EcoApi.get('/api/memories/l2')).items.map(x => x.slug)),
  ]);
  const F = S.timelineFilter;
  const gateName = Charts.gateName, gateCol = Charts.gateCol;
  const actCls = a => a.startsWith('ADD') ? 'ADD' : a.startsWith('UPDATE') ? 'UPDATE' : a.startsWith('CONFLICT') ? 'ADD'
    : a.startsWith('extrude') ? 'extrude' : a === 'promote' ? 'promote' : 'other';
  const actColor = { ADD: 'var(--gate1)', UPDATE: 'var(--gate3)', extrude: 'var(--gate2)',
    promote: 'var(--gate4)', other: 'var(--ink3)' };
  let rows = d.rows;
  if (F) rows = rows.filter(r => r.table === F);
  const chartRows = rows.slice().sort((a, b) => a.ts.localeCompare(b.ts)).reverse();
  const days = {};
  rows.forEach(r => (days[r.ts.slice(0, 10)] = days[r.ts.slice(0, 10)] || []).push(r));
  const mode = S.timelineMode || 'chart';
  const sub = (S.timelineChart || 'swim');

  v.innerHTML = `
    <div class="vh">时间线 <small>eco.db 四表合并 · 泳道/直方图/回放/文本 四形态 · 图文同源（${d.rows.length} 条）</small></div>
    <div class="filters">
      <div class="pills">${['', ...Object.keys(gateName)].map(t =>
        `<button data-tf="${t}" class="${F === t ? 'on' : ''}">${t ? gateName[t] : '全部'}</button>`).join('')}</div>
      <div class="pills">
        <button data-tmode="chart" class="${mode === 'chart' ? 'on' : ''}">图表模式</button>
        <button data-tmode="text" class="${mode === 'text' ? 'on' : ''}">文本模式</button></div>
      ${mode === 'chart' ? `<div class="pills">
        <button data-tsub="swim" class="${sub === 'swim' ? 'on' : ''}">泳道</button>
        <button data-tsub="hist" class="${sub === 'hist' ? 'on' : ''}">按日直方图</button>
        <button data-tsub="replay" class="${sub === 'replay' ? 'on' : ''}">演化回放</button></div>` : ''}
      <span class="faint">累计：${Object.entries(d.counts).map(([k, n]) =>
        (gateName[k.replace('_log', '')] || k) + ' ' + n).join(' · ')}</span>
    </div>
    <div id="tl-body"></div>`;

  const body = $('#tl-body', v);
  if (mode === 'text') {
    body.innerHTML = `<div class="card">
    ${Object.keys(days).length ? Object.entries(days).map(([day, rs]) => `
      <div class="tl-day">${day}<span class="dayfile" data-day="${day}">展开当日人读日报 →</span></div>
      <div id="dayfile-${day}" class="hidden"></div>
      ${rs.map(r => `<div class="tl-row">
        <span class="ts">${esc(r.ts.slice(11, 19))}</span>
        <i class="gdot" style="background:${gateCol[r.table]};width:7px;height:7px;border-radius:50%"></i>
        <span class="act" style="background:${actColor[actCls(r.action)]}">${esc(r.action)}</span>
        <span class="tgt">${esc(r.target)}</span>
        <span class="note">${esc(r.note)}</span>
        ${l2.includes(r.target) || Array.from(l2).some(s => r.target.startsWith(s) || s.startsWith(r.target.slice(0, 20)))
          ? `<span class="jump" data-goto-detail="${esc(r.target)}">查看实体 →</span>` : ''}
      </div>`).join('')}`).join('')
      : '<div class="empty">暂无日志记录</div>'}
    </div>`;
    $$('[data-day]', body).forEach(b => b.onclick = async () => {
      const box = $('#dayfile-' + b.dataset.day);
      if (!box.classList.contains('hidden')) { box.classList.add('hidden'); return; }
      box.classList.remove('hidden');
      try {
        const r = await EcoApi.get(`/api/logs/day?date=${encodeURIComponent(b.dataset.day)}`);
        box.innerHTML = r.day ? `<div class="md" style="border-left:2px solid var(--line);padding-left:14px;margin:8px 0">${md(r.day.body)}</div>`
          : '<div class="faint" style="padding:6px 10px">当日无人读日报文件。</div>';
      } catch (e) { box.innerHTML = `<div class="faint">日报读取失败：${esc(e.message)}</div>`; }
    });
  } else if (sub === 'swim') {
    body.innerHTML = `<div class="card"><h3>四门泳道 <small>事件色块按门着色 · 悬停看详情 · 点击跳转</small></h3>
      <div id="swim-box">${Charts.swimlane(chartRows, r => {
        if (l2.includes(r.target)) gotoDetail(r.target);
        else toast(r.target ? `目标「${r.target}」不是 L2 实体` : '无目标实体', true);
      })}</div></div>`;
    Charts.bindSwim($('#swim-box', body));
  } else if (sub === 'hist') {
    body.innerHTML = `<div class="card"><h3>按日直方图 <small>每天各门动作数（堆叠柱，悬停看明细）</small></h3>
      ${Charts.histogram(chartRows)}</div>`;
  } else {
    body.innerHTML = `<div class="card"><h3>演化回放 <small>逐事件点亮 · 播放/暂停/倍速/按日快进</small></h3>
      <div id="replay-box"></div>
      <div class="replay-track"><div id="replay-cursor" style="width:0%"></div></div>
      <div class="faint" style="margin-top:6px">进度线：回放推进时下移（时间顺序 ${esc(chartRows.length ? chartRows[0].ts.slice(0, 10) : '')} → ${esc(chartRows.length ? chartRows[chartRows.length - 1].ts.slice(0, 10) : '')}）</div>
    </div>`;
    Charts.replay(chartRows.slice().reverse(), $('#replay-box', body), (r, frac) => {
      $('#replay-cursor', body).style.width = (frac * 100).toFixed(1) + '%';
    });
  }

  $$('[data-tf]', v).forEach(b => b.onclick = () => { S.timelineFilter = b.dataset.tf; route(); });
  $$('[data-tmode]', v).forEach(b => b.onclick = () => { S.timelineMode = b.dataset.tmode; route(); });
  $$('[data-tsub]', v).forEach(b => b.onclick = () => { S.timelineChart = b.dataset.tsub; route(); });
  $$('[data-goto-detail]', v).forEach(a => a.onclick = () => gotoDetail(a.dataset.gotoDetail));
  if (mode === 'chart') {
    // 图表下方补"最近 3 日明细"（收掉大片空白；与图表同源数据）
    const dayKeys = Object.keys(days).sort().reverse().slice(0, 3);
    const gname = k => gateName[k] || k;
    body.insertAdjacentHTML('beforeend', `<div class="tl-side">${dayKeys.map(day => {
      const rs = days[day];
      const byGate = {};
      rs.forEach(r => byGate[r.table] = (byGate[r.table] || 0) + 1);
      const acts = {};
      rs.forEach(r => acts[r.action] = (acts[r.action] || 0) + 1);
      const top = Object.entries(acts).sort((a, b) => b[1] - a[1]).slice(0, 3);
      return `<div class="card"><h3>${esc(day)} <small>${rs.length} 个动作</small></h3>
        <div class="row" style="margin-bottom:6px">${Object.entries(byGate).map(([k, n]) =>
          `<span class="tag">${esc(gname(k))} × ${n}</span>`).join('')}</div>
        <div class="faint" style="font-size:11.5px">高频动作：${top.map(([a, n]) => esc(a) + ' ×' + n).join(' · ') || '—'}</div>
      </div>`;
    }).join('') || ''}</div>`);
  }
};
