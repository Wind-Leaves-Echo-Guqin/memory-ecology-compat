/* ============================================================
   views/lineage.js — 血缘谱系 v0.3.2（重做）
   · 「链路故事」卡：每条血缘链一行叙事（A →（繁殖自）B →（合并入）C）
   · 边表格修复：数据直接来自 /api/lineage edges，每行可点开详情抽屉
   · 候选区 = 三源聚合（与孵化台同源），不再依赖单一 skills/.candidates
   · 节点/边点击 → 详情抽屉（frontmatter 证据 + 跳星图 + 孵化）
   ============================================================ */
'use strict';

RENDER.lineage = async function (v) {
  const d = await cached('lineage', 20000, () => EcoApi.get('/api/lineage'));
  const sk = await cached('skills', 20000, () => EcoApi.get('/api/skills'));
  const cand = await cached('candidates', 10000, () => EcoApi.get('/api/candidates'));
  const infoByName = {};
  sk.skills.forEach(s => { infoByName[s.name] = s; });
  const pathByName = {};
  sk.skills.forEach(s => { pathByName[s.name] = s.path; });

  // —— 链路归组：把边合成"链"（同一 parent 的 evolved 链为一组故事） ——
  const chains = [];
  const usedChild = {};
  d.edges.forEach(e => { if (e.kind === 'evolved_from' && !usedChild[e.child]) { usedChild[e.child] = 1; } });
  const evolveEdges = d.edges.filter(e => e.kind === 'evolved_from');
  const mergeEdges = d.edges.filter(e => e.kind === 'merged_into');
  // 链 = 按 parent 分组
  const byParent = {};
  evolveEdges.forEach(e => (byParent[e.parent] = byParent[e.parent] || []).push(e));

  v.innerHTML = `
    <div class="vh">血缘谱系 <small>谁从谁繁殖而来（evolved_from）· 谁合并进了谁（merged_into）</small></div>
    <div class="vsub">这是什么：技能之间的"家族关系"。繁殖 = 新技能由旧技能演化产生；合并 = 旧技能并入新技能后退役。
      点击任何名字可看它的详情。<a data-goto="starmap">→ 去星图看全景</a></div>
    <div class="row" style="margin-bottom:12px">
      <button class="ghost-btn" data-act="skill_breed">✨ 发起新的繁殖（孵化）</button>
      <span class="faint">孵化 = 声明一个新技能（名字+亲代+动机），先进入观察期，再由门③蒸馏转正</span>
    </div>

    <div class="card"><h3>繁殖链 <small>${evolveEdges.length} 条 · 每条 = 一段"谁演化为谁"的故事</small></h3>
      ${evolveEdges.length ? evolveEdges.map((e, i) => {
        const p = infoByName[e.parent], c = infoByName[e.child];
        return `<div class="chain-card" data-chain="${i}">
          <div class="chain-flow">
            <a class="chain-node" data-lname="${esc(e.parent)}" title="${esc(p ? p.desc : '')}">${esc(e.parent)}</a>
            <span class="chain-arrow">— 繁殖出 →</span>
            <a class="chain-node" data-lname="${esc(e.child)}" title="${esc(c ? c.desc : '')}">${esc(e.child)}</a>
          </div>
          <div class="chain-meta faint">
            ${p ? `亲代：v${esc(p.version)} · ${esc(p.cat)} · 被引${p.indeg}` : ''}${p && c ? ' ／ ' : ''}
            ${c ? `子代：v${esc(c.version)} · ${esc(c.cat)} · 被引${c.indeg}` : ''}
          </div>
          <div class="chain-ops">
            <a data-edge-detail="${esc('evolved_from')}|${esc(e.child)}|${esc(e.parent)}">为什么会有这条链？</a>
            ${c ? ` · <a data-openmd="${esc(c.path)}">看子代 SKILL.md</a>` : ''}
          </div>
        </div>`;
      }).join('') : '<div class="empty">还没有繁殖记录。这不是坏了——孵化一个新技能后，这里就会出现第一条链。<br>点上方「发起新的繁殖（孵化）」即可开始。</div>'}
    </div>

    <div class="card"><h3>合并流 <small>${mergeEdges.length} 条 · 旧技能并入新技能后退役</small></h3>
      ${mergeEdges.length ? mergeEdges.map(e => `
        <div class="chain-card"><div class="chain-flow">
          <a class="chain-node" data-lname="${esc(e.child)}">${esc(e.child)}</a>
          <span class="chain-arrow" style="color:var(--warn)">— 合并入 →</span>
          <a class="chain-node" data-lname="${esc(e.parent)}">${esc(e.parent)}</a>
        </div>
        <div class="chain-ops"><a data-edge-detail="merged_into|${esc(e.child)}|${esc(e.parent)}">合并详情</a></div>
      </div>`).join('') : '<div class="empty-ok">没有合并记录（healthy：合并通常发生在去重治理时）。</div>'}
    </div>

    <div class="card"><h3>全部边清单 <small>${d.edges.length} 条 · 点击行看详情</small></h3>
      ${d.edges.length ? `<table><tr><th>子代</th><th>关系</th><th>亲代/去向</th><th>详情</th></tr>
        ${d.edges.map(e => `<tr class="click" data-edge-detail="${esc(e.kind)}|${esc(e.child)}|${esc(e.parent)}">
          <td>${esc(e.child)}</td>
          <td><span class="tag ${e.kind === 'evolved_from' ? '' : 'warn'}">${e.kind === 'evolved_from' ? '繁殖自' : '合并入'}</span></td>
          <td>${esc(e.parent)}</td>
          <td class="faint">查看 →</td></tr>`).join('')}</table>`
        : '<span class="faint">无边</span>'}
    </div>

    <div class="card"><h3>候选区 <small>待孵化的"准技能"与准经验（与孵化台同源）</small></h3>
      ${(cand.items || []).length
        ? `<table><tr><th>候选</th><th>类型</th><th>时间</th><th>操作</th></tr>
        ${(cand.items || []).slice(0, 12).map(it => `<tr>
          <td><a data-cdetail="${esc(it.kind)}|${esc(it.name)}"><b>${esc(it.name)}</b></a></td>
          <td><span class="tag ${it.kind === 'skill' ? '' : it.kind === 'profile' ? 'warn' : 'ok'}">${({skill:'技能候选',profile:'画像候选',experience:'经验候选'})[it.kind] || it.kind}</span></td>
          <td class="faint">${esc((it.mtime || '').slice(0, 16).replace('T', ' '))}</td>
          <td>${it.kind === 'skill' ? `<button class="ghost-btn act-btn" data-breed="${esc(it.name)}">孵化</button>` : it.kind === 'experience' ? '<span class="faint">→ 孵化台采纳</span>' : '<span class="faint">观察期</span>'}</td>
        </tr>`).join('')}</table>${(cand.items || []).length > 12 ? `<a data-goto="candidates">→ 还有 ${(cand.items || []).length - 12} 条，去孵化台看全部</a>` : ''}`
        : '<div class="empty-ok">0 个候选。出现条件：孵化动作会写入技能候选；生态捕获会写入经验候选；门③会产生画像候选。<br>点上方「发起新的繁殖（孵化）」即可产生第一个技能候选。</div>'}
    </div>`;

  $$('[data-act]', v).forEach(b => b.onclick = () => openBreedForm());
  $$('[data-goto]', v).forEach(a => a.onclick = () => goto(a.dataset.goto));
  $$('[data-openmd]', v).forEach(a => a.onclick = () => openSkillMd(a.dataset.openmd));
  $$('[data-breed]', v).forEach(b => b.onclick = () => openBreedForm({ name: b.dataset.breed }));
  $$('[data-cdetail]', v).forEach(a => a.onclick = () => {
    const [kind, name] = a.dataset.cdetail.split('|');
    openCandidateDetail(kind, name);
  });
  $$('[data-lname]', v).forEach(a => a.onclick = () => openLineageNode(a.dataset.lname, infoByName, pathByName));
  $$('[data-edge-detail]', v).forEach(a => a.onclick = (ev) => {
    ev.stopPropagation();
    const [kind, child, parent] = a.dataset.edgeDetail.split('|');
    openEdgeDetail(kind, child, parent, infoByName, pathByName);
  });
};

/* 节点详情抽屉 */
function openLineageNode(name, infoByName, pathByName) {
  const s = infoByName[name];
  if (!s) { toast('「' + name + '」不在现役技能库（可能已归档）', true); return; }
  openDrawer(`<div class="vh">${esc(name)} <small>v${esc(s.version)} · ${esc(s.cat)}</small></div>
    <div class="fmtable">
      <span>一句话</span><span>${esc(s.desc || '—')}</span>
      <span>status/fate</span><span>${esc(s.status)} / ${esc(s.fate)}</span>
      <span>被引</span><span>${s.indeg} 次</span>
      <span>血缘</span><span>${esc(s.evolved_from ? '繁殖自 ' + s.evolved_from : s.merged_into ? '已合并入 ' + s.merged_into : '—（第一代）')}</span>
      <span>来源文件</span><span class="mono ellipsis" title="${esc(s.path)}">${esc(s.path)}</span>
    </div>
    <div class="row">
      <button class="ghost-btn" id="ln-goto">在星图中定位</button>
      <button class="ghost-btn" id="ln-md">打开 SKILL.md</button>
    </div>`);
  $('#ln-goto').onclick = () => { closeDrawer(); gotoSkill(name); };
  $('#ln-md').onclick = () => openSkillMd(s.path);
}

/* 边详情抽屉：讲清"为什么会有这条边" */
function openEdgeDetail(kind, child, parent, infoByName, pathByName) {
  const c = infoByName[child], p = infoByName[parent];
  const story = kind === 'evolved_from'
    ? `「${esc(parent)}」在演化中产生了新版本/变体，登记为「${esc(child)}」。子代 SKILL.md 的 frontmatter 里写有 evolved_from: ${esc(parent)}（这就是本边的原始证据）。`
    : `「${esc(child)}」被合并进「${esc(parent)}」后退役（frontmatter 写有 merged_into: ${esc(parent)}）。通常发生在去重治理：功能重叠时保留一方。`;
  openDrawer(`<div class="vh">血缘边详情</div>
    <div class="empty-ok" style="line-height:2">${story}</div>
    <div class="fmtable">
      <span>关系</span><span>${kind === 'evolved_from' ? '繁殖（evolved_from）' : '合并（merged_into）'}</span>
      <span>子代</span><span>${esc(child)}${c ? ` · v${esc(c.version)} · ${esc(c.cat)}` : '（不在现役库）'}</span>
      <span>亲代/去向</span><span>${esc(parent)}${p ? ` · v${esc(p.version)} · ${esc(p.cat)}` : '（不在现役库）'}</span>
      <span>子代文件</span><span class="mono ellipsis" title="${esc(pathByName[child] || '')}">${esc(pathByName[child] || '—')}</span>
      <span>亲代文件</span><span class="mono ellipsis" title="${esc(pathByName[parent] || '')}">${esc(pathByName[parent] || '—')}</span>
    </div>
    <div class="row">
      ${c ? `<button class="ghost-btn" id="ed-c">定位子代</button>` : ''}
      ${p ? `<button class="ghost-btn" id="ed-p">定位亲代</button>` : ''}
    </div>`);
  const bc = $('#ed-c'); if (bc) bc.onclick = () => { closeDrawer(); gotoSkill(child); };
  const bp = $('#ed-p'); if (bp) bp.onclick = () => { closeDrawer(); gotoSkill(parent); };
}

/* 孵化表单（eco_breed：名字/亲代/动机）→ 走闸门 */
async function openBreedForm(prefill) {
  const sk = await cached('skills', 20000, () => EcoApi.get('/api/skills'));
  const names = sk.skills.slice().sort((a, b) => b.indeg - a.indeg).map(s => s.name);
  openDrawer(`<div class="vh">技能孵化 <small>三步：填表 → 预览 → 确认执行（全程走确认闸门）</small></div>
    <div class="fmtable" style="grid-template-columns:90px 1fr">
      <span>名字</span><span><input id="bf-name" class="bf-input" placeholder="新技能名（英文短横线，如 my-new-skill）" value="${esc((prefill && prefill.name) || '')}"></span>
      <span>亲代</span><span><input id="bf-sources" class="bf-input" placeholder="它从谁演化而来（逗号分隔，如 plan,docx）" value="${esc((prefill && prefill.sources) || '')}"></span>
      <span>动机</span><span><input id="bf-mot" class="bf-input" placeholder="为什么要孵化它（一句话，会写进记录）" value="${esc((prefill && prefill.motivation) || '')}"></span>
    </div>
    <div class="empty-ok">不确定亲代写谁？被引最多的技能（生态核心）：${names.slice(0, 6).map(esc).join(' · ')}</div>
    <div class="gate-btns" style="margin-top:10px">
      <button class="ghost-btn" id="bf-cancel">取消</button>
      <button class="gate-go" id="bf-go">下一步（预览，不写入）</button>
    </div>`);
  $('#bf-cancel').onclick = closeDrawer;
  $('#bf-go').onclick = () => {
    const name = $('#bf-name').value.trim();
    const sources = $('#bf-sources').value.trim();
    const motivation = $('#bf-mot').value.trim();
    if (!name || !sources || !motivation) { toast('三项都填一下（名字/亲代/动机）', false); return; }
    runAction('skill_breed', {}, { name, sources, motivation });
  };
}
