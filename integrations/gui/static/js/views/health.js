/* ============================================================
   views/health.js — 体检评测 v0.3（T10）
   · 顶部「生态版本 × 报告时间轴」对照卡
   · 报告页签：归档历史列表（可点开）+ 任选两份并排对比 + P0 红色告警样式 + 评分曲线
   ============================================================ */
'use strict';

RENDER.health = async function (v) {
  const d = await cached('health', 30000, () => EcoApi.get('/api/health'));
  const meta = await cached('meta', 15000, () => EcoApi.get('/api/meta'));
  const T = S.healthTab;
  const archives = d.archives || [];
  const curScore = (d.report.body.match(/总分[^\d]{0,6}(\d{1,3})/) || [])[1] || null;
  const mtime = (d.report.mtime || '').slice(0, 16).replace('T', ' ');
  const ageH = d.report.mtime ? ((Date.now() - new Date(d.report.mtime).getTime()) / 3600000).toFixed(1) : null;
  v.innerHTML = `
    <div class="vh">体检评测 <small>报告为只读渲染 —— 评分来自体检脚本，本页不做计算（数据忠实性）</small></div>
    <div class="card"><h3>生态版本 × 报告对照 <small>报告快照语义：生成后生态可能已变化</small></h3>
      <div class="row">
        <span class="chip">生态版本 <b>${esc(meta.version)}</b></span>
        <span class="chip">评分模型 <b>${esc(meta.score_model)}</b></span>
        <span class="chip">最新报告 <b>${esc(d.report.name || '—')}</b></span>
        <span class="chip">生成于 ${esc(mtime || '—')}</span>
        ${ageH != null ? `<span class="tag ${+ageH > 30 ? 'warn' : 'ok'}">距今 ${ageH} 小时</span>` : ''}
        <span class="chip">归档 <b>${archives.length}</b> 份</span>
      </div>
    </div>
    <div class="htabs pills">${[['report', '体检报告'], ['archive', '归档时间轴'], ['compare', '双报告对比'], ['history', '评分历史'], ['eval', '评测门禁'], ['backups', '备份岩层']]
      .map(([k, n]) => `<button data-ht="${k}" class="${T === k ? 'on' : ''}">${n}</button>`).join('')}</div>
    <div id="health-body"></div>`;
  const body = $('#health-body', v);
  if (T === 'report') {
    body.innerHTML = d.report.body
      ? `<div class="card md">${mdP0(d.report.body)}</div>
         <p class="faint">报告文件：${esc(d.report.name || '')} · 生成于 ${esc(mtime)} · 快照语义：生成后生态可能已变化 ·
         <a id="copy-report">复制报告全文</a></p>`
      : '<div class="empty">未找到体检报告（驾驶舱→立即体检 或 跑一次 python eco_health_check.py 即生成）。</div>';
    const cp = $('#copy-report');
    if (cp && d.report.body) cp.onclick = () => ecoCopy(d.report.body, '报告全文已复制');
  } else if (T === 'archive') {
    body.innerHTML = `<div class="card"><h3>归档时间轴 <small>按日归档（保留 30 份）+ 当前覆盖式文件 · 点击查看</small></h3>
      ${archives.length ? `<div class="archive-tl">${archives.map(a => `
        <div class="archive-item ${a.source}" data-rname="${esc(a.name)}">
          <i class="gdot" style="background:${a.source === 'current' ? 'var(--accent)' : 'var(--gate3)'}"></i>
          <b>${esc(a.day)}</b> <span class="faint">${esc(a.mtime.slice(0, 16).replace('T', ' '))}</span>
          <span class="tag">${a.source === 'current' ? '当前' : '归档'}</span>
        </div>`).join('')}</div>`
      : '<div class="empty">暂无归档——跑一次体检（驾驶舱「立即体检」）即按日落一份。</div>'}</div>`;
    $$('[data-rname]', body).forEach(el => el.onclick = async () => {
      try {
        const r = await EcoApi.get(`/api/health/report?name=${encodeURIComponent(el.dataset.rname)}`);
        openDrawer(`<div class="vh">报告 <small>${esc(el.dataset.rname)}</small></div>
          <div class="md">${mdP0(r.report.body)}</div>
          <div class="gate-btns" style="margin-top:10px"><button class="ghost-btn" id="cp2">复制全文</button></div>`);
        $('#cp2').onclick = () => ecoCopy(r.report.body, '已复制');
      } catch (e) { toast('读取失败：' + e.message); }
    });
  } else if (T === 'compare') {
    if (archives.length < 2) {
      body.innerHTML = '<div class="empty">对比至少需要两份报告（当前 ' + archives.length + ' 份）——归档积累后可用。</div>';
    } else {
      body.innerHTML = `<div class="card"><h3>双报告并排对比 <small>左右两列，同步滚动</small></h3>
        <div class="row" style="margin-bottom:10px">
          <select id="cmp-a">${archives.map((a, i) => `<option value="${esc(a.name)}" ${i === 1 ? 'selected' : ''}>${esc(a.name)}（${a.source === 'current' ? '当前' : '归档'}）</option>`).join('')}</select>
          <span class="faint">vs</span>
          <select id="cmp-b">${archives.map((a, i) => `<option value="${esc(a.name)}" ${i === 0 ? 'selected' : ''}>${esc(a.name)}（${a.source === 'current' ? '当前' : '归档'}）</option>`).join('')}</select>
        </div>
        <div class="cmp-wrap"><div id="cmp-a-body" class="md cmp-col"><div class="loading">读取中…</div></div>
        <div id="cmp-b-body" class="md cmp-col"><div class="loading">读取中…</div></div></div></div>`;
      const load = async (sel, target) => {
        const name = $(sel).value;
        const r = await EcoApi.get(`/api/health/report?name=${encodeURIComponent(name)}`);
        $(target).innerHTML = mdP0(r.report.body) || '(空)';
      };
      const reload = () => { Promise.all([load('#cmp-a', '#cmp-a-body'), load('#cmp-b', '#cmp-b-body')]).catch(e => toast('读取失败：' + e.message)); };
      $('#cmp-a').onchange = reload; $('#cmp-b').onchange = reload;
      reload();
    }
  } else if (T === 'history') {
    const h = d.history || [];
    body.innerHTML = `<div class="card"><h3>评分历史 <small>按模型分段，不跨版本连线</small></h3>
      ${trendSvg(h)}
      <table style="margin-top:10px"><tr><th>时间</th><th>评分</th><th>模型</th></tr>
      ${h.slice().reverse().map(x => `<tr><td>${esc(x.time)}</td><td class="num">${x.score}</td><td>${esc(x.model)}</td></tr>`).join('')}</table></div>`;
  } else if (T === 'eval') {
    body.innerHTML = d.eval_latest
      ? `<div class="card"><h3>评测报告 <small>LongMemEval 五维 + INJ/GOLD 门禁 · INSUFFICIENT 诚实单列</small></h3>
         <div class="md">${md(d.eval_latest.body)}</div></div>
         ${d.evals.length > 1 ? `<div class="card"><h3>历史评测</h3>${d.evals.map(e =>
           `<div class="eval-item" data-eval="${esc(e.name)}">${esc(e.name)}</div>`).join('')}</div>` : ''}`
      : '<div class="empty">尚无评测报告（跑一次 python eco_eval.py --gate 生成）。</div>';
  } else {
    body.innerHTML = `<div class="card"><h3>备份岩层 <small>备份链=地质沉积层，越深越旧（目录实时统计）</small></h3>
      ${d.backups.length ? `<div class="strata">${d.backups.slice(0, 16).map(b =>
        `<span>${esc(b.date)}</span><span class="num">${b.n}</span>
         <div><div class="sbar" style="width:${Math.min(100, b.n * 6)}%"></div>
         <span class="faint" style="font-size:11px">${esc(b.files.slice(0, 2).join(' · '))}${b.files.length > 2 ? ' …' : ''}</span></div>`).join('')}</div>`
        : '<div class="empty-ok">无备份文件 —— 各门写前自动备份，出现即说明治理动作发生过。</div>'}</div>`;
  }
  $$('[data-ht]', v).forEach(b => b.onclick = () => { S.healthTab = b.dataset.ht; route(); });
  $$('[data-eval]', body).forEach(el => el.onclick = () => {
    const ev = d.evals.find(e => e.name === el.dataset.eval);
    if (ev) openDrawer(`<div class="vh">评测报告 <small>${esc(ev.name)}</small></div><div class="md">${md(ev.body)}</div>`);
  });
  // 评分环：变化时闪一次（上次分数缓存在 localStorage；差异明显才闪，避免每次进入都闪）
  const ring = $('.ring', v);
  if (ring) {
    const last = +(localStorage.getItem('eco_last_score') || 0);
    if (last && Math.abs(last - curScore) >= 3) {
      ring.classList.add('flash');
      setTimeout(() => ring.classList.remove('flash'), 1000);
    }
    if (curScore) { try { localStorage.setItem('eco_last_score', String(curScore)); } catch (e) { } }
  }
};

/* Markdown 渲染 + P0 行红色告警条样式 */
function mdP0(src) {
  const html = md(src);
  const div = document.createElement('div');
  div.innerHTML = html;
  // 表格单元格与列表项中含 P0 的行加告警样式
  $$('td, li', div).forEach(el => {
    if (/\bP0\b/.test(el.textContent) && el.textContent.trim().length > 6) {
      el.classList.add('p0-row');
      if (el.tagName === 'TD' && el.textContent.trim().startsWith('P0')) el.classList.add('p0-cell');
    }
  });
  $$('p', div).forEach(el => {
    if (/^.{0,4}P0[::）)]?/.test(el.textContent.trim()) && el.textContent.trim().length > 8) el.classList.add('p0-row');
  });
  return div.innerHTML;
}
