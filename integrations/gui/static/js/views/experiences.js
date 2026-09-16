/* ============================================================
   views/experiences.js — 经验笔记本（沿用 v0.2 结构 + 复制增强）
   ============================================================ */
'use strict';

/* v0.3.2 中英切换 + 中文说明（类型/状态完全直译无意义，这里给"人话"） */
const EXP_TYPE_ZH = {
  error: '错误案例', pattern: '可复用模式', negative: '无效做法',
  success: '成功经验', link: '外部关联',
};
const EXP_TYPE_DESC = {
  error: '踩过的坑与正确解法', pattern: '可复用的通用套路', negative: '验证过无效的做法（避免再犯）',
  success: '已验证有效的做法', link: '与外部资源/系统的关联',
};
const EXP_STATUS_ZH = { draft: '草稿', verified: '已验证' };

RENDER.experiences = async function (v) {
  const F = S.expFilters;
  S.expZh = localStorage.getItem('exp_zh') !== '0';   // 默认中文
  const zh = S.expZh;
  const tName = t => zh ? (EXP_TYPE_ZH[t] || t) : t;
  const sName = s => zh ? (EXP_STATUS_ZH[s] || s) : s;
  const q = `type=${encodeURIComponent(F.type)}&status=${encodeURIComponent(F.status)}&page=${F.page}`;
  const d = await cached(`exp-${q}-${F.sort}`, 8000, async () => {
    const r = await EcoApi.get('/api/experiences?' + q);
    if (F.sort === 'last_hit') r.items.sort((a, b) => (b.last_hit || '').localeCompare(a.last_hit || ''));
    return r;
  });
  const tc = { error: 'var(--exp-error)', pattern: 'var(--exp-pattern)', negative: 'var(--exp-negative)',
    success: 'var(--exp-success)', link: 'var(--exp-link)' };
  // 经验卡（错峰入场动画放模板里；空态用专属插画）
  const expCards = d.items.length
    ? '<div class="exps">' + d.items.map((x, i) => `
      <div class="exp a-stagger" style="animation-delay:${Math.min(i * 32, 480)}ms" data-exp="${esc(x.id)}">
        <div class="etags">
          <span class="etyp" style="background:${tc[x.type] || 'var(--ink3)'}">${esc(tName(x.type))}</span>
          <span class="est ${x.status === 'verified' ? 'gold' : ''}">${esc(sName(x.status))}${x.status === 'verified' ? ' ★' : ''}</span>
          ${x.distilled_to ? `<span class="est">已蒸馏 → 技能 ${esc(x.distilled_to)}</span>` : ''}
          ${x.provenance && x.provenance.indexOf('session') >= 0 ? `<span class="faint" title="来源会话（可溯源）">⌘ ${esc(x.provenance.slice(0, 24))}</span>` : ''}
        </div>
        <h4>${esc(x.title)}</h4>
        ${x.symptom ? `<div class="sym">${esc(x.symptom.slice(0, 90))}</div>` : ''}
        <div class="meta"><span>建 ${esc(x.created)}</span><span>hit ${esc(x.last_hit || '—')}</span></div>
      </div>`).join('') + '</div>'
    : Icons.empty('memories', '这个筛选组合下没有经验条目', '换一个类型/状态筛选，或去生态里制造一些——经验来自真实踩坑与复盘。');
  v.innerHTML = `
    <div class="vh">经验笔记本 <small>${d.total} 条 · 命中≥2 自动草稿→已验证（金标成长位）</small></div>
    <div class="vsub">这是什么：Hermes 运行中踩坑/总结出的经验条目，报错时会自动检索匹配（注入回路在跑 <span class="dot ok"></span> · dsh 适配器未实装 <span class="dot off"></span>）。
      <a id="exp-zh-toggle">${zh ? '显示英文原名' : '显示中文'}</a></div>
    <div class="card"><h3>候选区 <small>宿主前缀日期文件 · 可在候选孵化台采纳</small></h3>
      ${d.pending.length ? d.pending.map(p => `<span class="chip" title="${esc(p.preview)}">${esc(p.name)} <span class="faint">${esc(p.mtime.slice(5, 10))}</span></span>`).join('')
        : '<span class="faint">空</span>'}
      ${d.pending.length ? '<a data-goto="candidates">→ 去孵化台采纳</a>' : ''}
    </div>
    <div class="filters">
      <div class="pills">${['', 'error', 'pattern', 'negative', 'success', 'link'].map(t =>
        `<button data-ftype="${t}" class="${F.type === t ? 'on' : ''}">${t ? tName(t) : (zh ? '全部类型' : 'ALL')}</button>`).join('')}</div>
      <div class="pills">${['', 'draft', 'verified'].map(t =>
        `<button data-fstatus="${t}" class="${F.status === t ? 'on' : ''}">${t ? sName(t) : (zh ? '全部状态' : 'ALL')}</button>`).join('')}</div>
      <select id="exp-sort"><option value="created" ${F.sort === 'created' ? 'selected' : ''}>按创建</option>
        <option value="last_hit" ${F.sort === 'last_hit' ? 'selected' : ''}>按 last_hit</option></select>
      <span class="faint">${d.total} 条 · 第 ${d.page}/${d.pages} 页</span>
    </div>
    ${zh ? `<div class="vsub">类型速览：${['error','pattern','negative','success','link'].map(t => `<b style="color:${tc[t]}">${EXP_TYPE_ZH[t]}</b>=${EXP_TYPE_DESC[t]}`).join(' · ')}</div>` : ''}
    ${expCards}
    ${d.pages > 1 ? `<div class="row" style="margin-top:14px;justify-content:center">
      ${d.page > 1 ? '<button class="ghost-btn" id="pg-prev">‹ 上一页</button>' : ''}
      <span class="faint">${d.page} / ${d.pages}</span>
      ${d.page < d.pages ? '<button class="ghost-btn" id="pg-next">下一页 ›</button>' : ''}</div>` : ''}`;

  $('#exp-zh-toggle').onclick = () => {
    localStorage.setItem('exp_zh', zh ? '0' : '1');
    route();
  };
  $$('[data-ftype]', v).forEach(b => b.onclick = () => { F.type = b.dataset.ftype; F.page = 1; route(); });
  $$('[data-fstatus]', v).forEach(b => b.onclick = () => { F.status = b.dataset.fstatus; F.page = 1; route(); });
  $('#exp-sort').onchange = e => { F.sort = e.target.value; route(); };
  const prev = $('#pg-prev'), next = $('#pg-next');
  if (prev) prev.onclick = () => { F.page--; route(); };
  if (next) next.onclick = () => { F.page++; route(); };
  $$('[data-exp]', v).forEach(c => c.onclick = () => openExpDrawer(c.dataset.exp));
  $$('[data-goto]', v).forEach(a => a.onclick = () => goto(a.dataset.goto));
};
