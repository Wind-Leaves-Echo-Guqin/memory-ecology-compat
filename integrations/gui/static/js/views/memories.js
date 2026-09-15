/* ============================================================
   views/memories.js — 记忆库（v0.3：沿用 v0.2 三层结构，接新 api 层）
   ============================================================ */
'use strict';

RENDER.memories = async function (v) {
  const [l1, l2, aux] = await Promise.all([
    cached('l1', 8000, () => EcoApi.get('/api/memories/l1')),
    cached('l2', 8000, () => EcoApi.get('/api/memories/l2')),
    cached('aux', 15000, () => EcoApi.get('/api/memories/aux')),
  ]);
  const F = S.memFilters;
  let items = l2.items.slice();
  if (F.type) items = items.filter(x => x.type === F.type);
  if (F.status) items = items.filter(x => x.status === F.status);
  const sorters = { last: (a, b) => (b.last || '').localeCompare(a.last || ''),
    occ: (a, b) => (b.occ || 0) - (a.occ || 0), first: (a, b) => (a.first || '').localeCompare(b.first || '') };
  items.sort(sorters[F.sort] || sorters.last);
  const mem = l1.memory, usr = l1.user;
  const wm = (m, id) => {
    const over = m.chars > m.quota, pct = Math.min(100, m.chars / m.quota * 100);
    return `<div class="water" style="margin-bottom:12px"><div class="bar">
      <div class="fill" style="width:${pct}%"></div>
      ${over ? `<div class="over" style="left:${100 - (m.chars / m.quota - 1) * 100}%; width:${(m.chars / m.quota - 1) * 100}%"></div>` : ''}
      </div><div class="legend"><span>${id} <b class="num">${m.chars}</b> 字 / ${m.quota}（去空白口径）</span>
      <span style="${over ? 'color:var(--bad);font-weight:600' : 'color:var(--ink3)'}">${over ? '已越线' : '未越线'} · ${m.entries.length} 条</span></div></div>`;
  };
  v.innerHTML = `
    <div class="vh">记忆库 <small>L1 常驻 + L2 详情层 · 只读浏览</small></div>
    <div class="mem-layout">
      <div>
        <div class="card"><h3>L1 常驻层</h3>
          <div class="l1-tabs pills"><button data-l1="memory" class="on">MEMORY.md</button>
          <button data-l1="user">USER.md</button></div>
          <div id="l1-water">${wm(mem, 'MEMORY')}</div>
          <div id="l1-list">${mem.entries.map(e => l1Entry(e)).join('')}</div>
        </div>
        <div class="card frozen"><h3>隔离区 <small>superseded 旧条目 · 永不物理删除</small></h3>
          ${aux.quarantine.length ? aux.quarantine.map(f => `<div class="l1-entry">${esc(f.name)}</div>`).join('')
            : '<div class="empty-ok">暂无实例 —— 0 条 superseded，这是健康状态（机制在，矛盾未发生）。</div>'}
        </div>
        <div class="card"><h3>待处理区 <small>提取产出 → 门①消费（.done 已排除）</small></h3>
          ${aux.pending.length ? aux.pending.map(f => `
            <div class="l1-entry"><b>${esc(f.name)}</b> <span class="faint">${esc(f.mtime.slice(0, 16))}</span>
            <div class="faint" style="font-size:11.5px;margin-top:3px">${esc(f.preview.slice(0, 120))}…</div></div>`).join('')
            : '<div class="empty-ok">空 —— 门①已消化完候选（目录有进有出）。</div>'}
          <h3 style="margin-top:14px">画像候选 <small>门③观察期</small></h3>
          ${aux.user_candidates.length ? aux.user_candidates.map(f => `<div class="l1-entry">${esc(f.name)}</div>`).join('')
            : '<div class="empty-ok">空 —— 尚无达观察期的稳定特质。</div>'}
        </div>
      </div>
      <div class="card"><h3>L2 详情层 <small>${l2.items.length} 条 · 点击行开详情抽屉</small></h3>
        <div class="filters">
          <select id="f-type"><option value="">type 全部</option>${['semantic', 'episodic', 'procedural', 'lesson']
            .map(t => `<option ${F.type === t ? 'selected' : ''}>${t}</option>`).join('')}</select>
          <select id="f-status"><option value="">status 全部</option>${['active', 'superseded']
            .map(t => `<option ${F.status === t ? 'selected' : ''}>${t}</option>`).join('')}</select>
          <select id="f-sort"><option value="last" ${F.sort === 'last' ? 'selected' : ''}>按 last_seen</option>
            <option value="occ" ${F.sort === 'occ' ? 'selected' : ''}>按 occurrences</option>
            <option value="first" ${F.sort === 'first' ? 'selected' : ''}>按 first_seen</option></select>
          <span class="faint">筛选后 ${items.length} 条</span>
        </div>
        ${items.length ? `<table><tr><th>条目</th><th>type</th><th>status</th><th>occ</th><th>首见</th><th>最近核验</th><th>双时态</th></tr>
          ${items.map(x => `<tr class="click" data-slug="${esc(x.slug)}">
            <td class="ellipsis" style="max-width:330px">${esc(x.slug)}
              ${x.origin === 'extrude-from-L1' ? '<span class="faint" title="由 L1 挤出沉淀">⇩挤出</span>' : ''}
              ${x.superseded_by ? '<span class="tag bad">被取代</span>' : ''}</td>
            <td><span class="tag">${esc(x.type)}</span></td>
            <td><span class="tag ${x.status === 'active' ? 'ok' : 'bad'}">${esc(x.status)}</span></td>
            <td class="num">${x.occ ?? '—'}</td><td>${esc(x.first || '—')}</td><td>${esc(x.verified || '—')}</td>
            <td class="faint" style="font-size:11px">${esc((x.valid_time || '—') + ' / ' + (x.transaction_time || '—').replace('T', ' ').slice(0, 16))}</td>
          </tr>`).join('')}</table>`
          : '<div class="empty">筛选结果为空 —— 放宽条件试试。</div>'}
      </div>
    </div>`;

  // L1 双 tab
  function l1Entry(e) {
    return `<div class="l1-entry">
    ${e.linked_slug ? `<span class="jumpup" data-goto-detail="${esc(e.linked_slug)}" title="L1↔L2 关联：查看详情层条目">⇧ 已沉淀</span>` : ''}
    ${esc(e.text)}</div>`;
  }
  function bindL1() {
    $$('[data-goto-detail]', $('#l1-list')).forEach(a =>
      a.onclick = () => gotoDetail(a.dataset.gotoDetail));
  }
  const showL1 = key => {
    const m = key === 'memory' ? mem : usr;
    $('#l1-water').innerHTML = wm(m, key === 'memory' ? 'MEMORY' : 'USER');
    $('#l1-list').innerHTML = m.entries.map(e => l1Entry(e)).join('');
    $$('.l1-tabs button').forEach(b => b.classList.toggle('on', b.dataset.l1 === key));
    bindL1();
  };
  bindL1();
  $$('.l1-tabs button', v).forEach(b => b.onclick = () => showL1(b.dataset.l1));

  $('#f-type').onchange = e => { F.type = e.target.value; route(); };
  $('#f-status').onchange = e => { F.status = e.target.value; route(); };
  $('#f-sort').onchange = e => { F.sort = e.target.value; route(); };
  $$('tr.click', v).forEach(tr => tr.onclick = () => gotoDetail(tr.dataset.slug));
  $$('[data-goto-detail]', v).forEach(a => { if (!a.onclick) a.onclick = () => gotoDetail(a.dataset.gotoDetail); });
};
