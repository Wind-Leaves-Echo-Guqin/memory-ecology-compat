/* ============================================================
   views/candidates.js — 候选孵化台（T8）
   聚合三源：技能候选（.candidates）+ 画像候选（user_candidates）+ 经验候选（pending）
   操作：技能候选→孵化（eco_breed 表单）；经验候选→采纳（走闸门）；画像候选→只读（门③观察期）
   ============================================================ */
'use strict';

RENDER.candidates = async function (v) {
  const d = await cached('candidates', 10000, () => EcoApi.get('/api/candidates'));
  const byKind = d.by_kind || {};
  const items = d.items || [];
  v.innerHTML = `
    <div class="vh">候选孵化台 <small>三源聚合：技能（.candidates）+ 画像（user_candidates）+ 经验（pending）</small></div>
    <div class="vsub">候选 = 各门/提取管道产出、等待被消费的半成品。每个动作都走确认闸门，执行方为生产 CLI。</div>
    <div class="row" style="margin-bottom:12px">
      <span class="chip">技能候选 <b>${byKind.skill || 0}</b> <span class="faint">→ 孵化（eco_breed）</span></span>
      <span class="chip">画像候选 <b>${byKind.profile || 0}</b> <span class="faint">→ 门③观察期（只读）</span></span>
      <span class="chip">经验候选 <b>${byKind.experience || 0}</b> <span class="faint">→ 采纳（eco_note_adopt）</span></span>
    </div>
    ${items.length ? `<div class="card"><table>
      <tr><th>候选</th><th>来源</th><th>时间</th><th>预览</th><th>操作</th></tr>
      ${items.map(it => `<tr>
        <td><a data-cdetail="${esc(it.kind)}|${esc(it.name)}"><b>${esc(it.name)}</b></a></td>
        <td><span class="tag ${it.kind === 'skill' ? '' : it.kind === 'profile' ? 'warn' : 'ok'}">${esc(kindName(it.kind))}</span></td>
        <td class="faint">${esc((it.mtime || '').slice(0, 16).replace('T', ' '))}</td>
        <td class="faint ellipsis" style="max-width:380px" title="${esc(it.desc)}">${esc(it.desc)}</td>
        <td>${it.kind === 'skill'
          ? `<button class="ghost-btn act-btn" data-breed="${esc(it.name)}">孵化</button>`
          : it.kind === 'experience'
          ? `<button class="ghost-btn act-btn" data-adopt="1">采纳</button>`
          : '<span class="faint" title="门③语义：观察期内只读，期满自动升降级">观察期 · 只读</span>'}</td>
      </tr>`).join('')}</table></div>`
      : `<div class="empty">暂无任何候选（三源皆空）。<br>
        出现条件：①技能候选 = 孵化动作（eco_breed）写入 skills/.candidates；<br>
        ②画像候选 = 门③蒸馏到达观察期的稳定特质；<br>
        ③经验候选 = 生态捕获（eco_note）产出写入 experiences/pending。<br>
        空态是诚实状态——机制在，实例零。</div>`}
    ${byKind.experience ? `<div class="card"><h3>批量采纳经验候选</h3>
      <p class="faint" style="margin-bottom:8px">对全部 pending 候选执行一次 eco_note_adopt（外源自动降级 draft，候选源保留不删除）。</p>
      <button class="ghost-btn act-btn" data-act="note_adopt">预览并采纳…</button></div>` : ''}`;

  $$('[data-act]', v).forEach(b => b.onclick = () => runAction(b.dataset.act, {}));
  $$('[data-breed]', v).forEach(b => b.onclick = () => openBreedForm({ name: b.dataset.breed }));
  $$('[data-adopt]', v).forEach(b => b.onclick = () => runAction('note_adopt', {}));
  $$('[data-cdetail]', v).forEach(a => a.onclick = () => {
    const [kind, name] = a.dataset.cdetail.split('|');
    openCandidateDetail(kind, name);
  });
};

/* 候选详情抽屉：全文 + frontmatter + 就地操作（决策不离开本页） */
async function openCandidateDetail(kind, name) {
  try {
    const r = await EcoApi.get(`/api/candidates/detail?kind=${encodeURIComponent(kind)}&name=${encodeURIComponent(name)}`);
    const d = r.detail;
    const kindDesc = {
      skill: '技能候选（skills/.candidates）——孵化后进入观察期，再由门③蒸馏转正。',
      profile: '画像候选（memories/user_candidates）——门③观察期语义，期满自动升降级，GUI 只读。',
      experience: '经验候选（experiences/pending）——采纳后成为正式经验条目；外源内容自动降级 draft。',
    }[kind] || '';
    const actionBtn = kind === 'skill'
      ? `<button class="gate-go" id="cd-breed">孵化这个候选</button>`
      : kind === 'experience'
      ? `<button class="gate-go" id="cd-adopt">采纳全部经验候选（走闸门）</button>`
      : '<span class="faint">观察期内只读（门③期满自动处理）</span>';
    openDrawer(`<div class="vh">${esc(name)} <small>${esc(kindName(kind))}</small></div>
      <div class="empty-ok">${esc(kindDesc)}</div>
      ${Object.keys(d.frontmatter || {}).length ? `<div class="fmtable">${Object.entries(d.frontmatter).map(([k, vv]) =>
        `<span>${esc(k)}</span><span>${esc(vv)}</span>`).join('')}</div>` : ''}
      <div class="md" style="background:var(--surface2);border:1px solid var(--line);border-radius:9px;padding:12px 14px">${md(d.body || '(空)')}</div>
      <div class="row" style="margin-top:12px">
        ${actionBtn}
        <button class="ghost-btn" id="cd-copy">复制全文</button>
        <span class="faint mono ellipsis" style="max-width:220px" title="${esc(d.path)}">${esc(d.path)}</span>
      </div>`);
    $('#cd-copy').onclick = () => ecoCopy(d.body || '', '全文已复制');
    const bb = $('#cd-breed');
    if (bb) bb.onclick = () => openBreedForm({ name });
    const ab = $('#cd-adopt');
    if (ab) ab.onclick = () => runAction('note_adopt', {});
  } catch (e) { toast('详情读取失败：' + e.message, false); }
}

function kindName(k) {
  return { skill: '技能候选', profile: '画像候选', experience: '经验候选' }[k] || k;
}
