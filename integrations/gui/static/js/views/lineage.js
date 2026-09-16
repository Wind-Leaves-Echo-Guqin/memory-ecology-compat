/* ============================================================
   views/lineage.js — 血缘谱系 v0.3.3（T3：真正画出谱系图）
   · 真实谱系图：evolved_from（父→子，分叉）与 merged_into（子→父，汇聚）
     手写分层布局（按代际最长路径排层，零依赖）：
       演化边：亲代(左) → 子代(右)，紫
       合并边：子代(左) → 合并目标(右)，琥珀
       两类都向左→右读，方向箭头 + 流动光点 = 语义方向
   · 链路上光点流动（纯 CSS dashoffset，零 rAF）；悬停高亮该链并播一次流动
   · 新记录与上次渲染 diff，逐条"生长"出现（.ln-anim + 错峰延迟）
   · 文本链路故事卡保留（信息密度高，与图互为伴生视图）
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

  const evolveEdges = d.edges.filter(e => e.kind === 'evolved_from');
  const mergeEdges = d.edges.filter(e => e.kind === 'merged_into');

  v.innerHTML = `
    <div class="vh">血缘谱系 <small>谁从谁繁殖而来（evolved_from）· 谁合并进了谁（merged_into）</small></div>
    <div class="vsub">这是什么：技能之间的"家族关系"。繁殖 = 新技能由旧技能演化产生；合并 = 旧技能并入新技能后退役。
      点击任何名字可看它的详情。<a data-goto="starmap">→ 去星图看全景</a></div>
    <div class="row" style="margin-bottom:12px">
      <button class="ghost-btn" data-act="skill_breed">✨ 发起新的繁殖（孵化）</button>
      <span class="faint">孵化 = 声明一个新技能（名字+亲代+动机），先进入观察期，再由门③蒸馏转正</span>
    </div>

    <div class="card"><h3>谱系图 <small>横向分层：左=亲代/来源 · 右=子代/去向 · 紫=繁殖 · 琥珀=合并</small></h3>
      ${d.edges.length
        ? `<div class="ln-wrap">${lineageSvg(d.edges, infoByName)}<div id="ln-tip" class="ln-tip hidden"></div></div>`
        : Icons.empty('lineage', '还没有任何血缘记录',
            '这不是坏了——<b>孵化一个新技能</b>（上方按钮）后，这里就会出现第一条繁殖边；<br>去重治理把旧技能并入新技能时，会出现合并边（汇聚）。')}
    </div>

    <div class="card"><h3>繁殖链 <small>${evolveEdges.length} 条 · 每条 = 一段"谁演化为谁"的故事</small></h3>
      ${evolveEdges.length ? evolveEdges.map((e, i) => {
        const p = infoByName[e.parent], c = infoByName[e.child];
        return `<div class="chain-card" data-chain="${i}">
          <div class="chain-flow">
            <a class="chain-node" data-lname="${esc(e.parent)}" title="${esc(p ? p.desc : '')}">${esc(e.parent)}</a>
            <span class="chain-arrow ev">— 繁殖出 →</span>
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
      }).join('') : '<div class="empty-ok">还没有繁殖记录——孵化一个技能后这里会出现第一条链。</div>'}
    </div>

    <div class="card"><h3>合并流 <small>${mergeEdges.length} 条 · 旧技能并入新技能后退役</small></h3>
      ${mergeEdges.length ? mergeEdges.map(e => `
        <div class="chain-card"><div class="chain-flow">
          <a class="chain-node" data-lname="${esc(e.child)}">${esc(e.child)}</a>
          <span class="chain-arrow mg">— 合并入 →</span>
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
        : '<div class="empty-ok">0 个候选。孵化动作写入技能候选；生态捕获写入经验候选；门③产生画像候选。</div>'}
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
  // 谱系图交互（悬停高亮链路 + 点击详情）与"新记录生长"入场
  bindLineageGraph(v, d, infoByName, pathByName);
};

/* ═══════════════ 谱系图：分层布局 + SVG 渲染 ═══════════════ */
function lineageSvg(edges, infoByName) {
  const ns = 'http://www.w3.org/2000/svg';
  // ── 建图：from → to（演化：亲代→子代；合并：子代→合并目标） ──
  const nodes = {}, names = new Set();
  edges.forEach(e => { names.add(e.child); names.add(e.parent); });
  names.forEach(nm => nodes[nm] = { name: nm, g: 0, parents: [], children: [], kind: {} });
  edges.forEach(e => {
    const from = e.kind === 'evolved_from' ? e.parent : e.child;
    const to = e.kind === 'evolved_from' ? e.child : e.parent;
    nodes[from].children.push(to);
    nodes[to].parents.push(from);
    nodes[from].kind[to] = e.kind;
    nodes[to].kind[from] = e.kind;
  });
  // ── 代际（最长路径；环保护） ──
  const genOf = (nm, seen) => {
    const nd = nodes[nm];
    if (nd.g) return nd.g;
    seen = seen || {};
    if (seen[nm]) return 0;
    seen[nm] = 1;
    let g = 0;
    nd.parents.forEach(p => { g = Math.max(g, 1 + genOf(p, seen)); });
    nd.g = g;
    return g;
  };
  names.forEach(nm => genOf(nm));
  // ── 列内排序（子列按"父在列内的平均位置"排序 → 减少连线交叉；逐列处理，父列已定序） ──
  const cols = [];
  names.forEach(nm => { (cols[nodes[nm].g] = cols[nodes[nm].g] || []).push(nm); });
  const idxInCol = {};
  cols.forEach((col, gi) => {
    const avgPar = nm => {
      let s = 0, n = 0;
      nodes[nm].parents.forEach(p => { const t = idxInCol[p]; if (t) { s += t.i; n++; } });
      return n ? s / n : 0;
    };
    const indeg = nm => (infoByName[nm] ? infoByName[nm].indeg : 0);
    col.sort(gi === 0
      ? (a, b) => indeg(b) - indeg(a) || a.localeCompare(b)
      : (a, b) => avgPar(a) - avgPar(b) || a.localeCompare(b));
    col.forEach((nm, i) => { idxInCol[nm] = { g: gi, i: i }; });
  });
  // ── 坐标 ──
  const colW = 176, boxW = 150, boxH = 44, gapV = 22, padX = 26, padY = 34;
  const X = g => padX + g * colW;
  const Y = i => padY + i * (boxH + gapV);
  const W = padX + (cols.length - 1) * colW + boxW + padX;
  const maxRows = Math.max(1, ...cols.map(c => c.length));
  const H = padY + maxRows * (boxH + gapV) - gapV + padY;
  // ── 边 ──
  const defs = `<defs>
    <marker id="ln-arw-ev" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="4.6" markerHeight="4.6" orient="auto-start-reverse">
      <path d="M1.4 1.2 6.6 4 1.4 6.8Z" fill="#a98cf0"/></marker>
    <marker id="ln-arw-mg" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="4.6" markerHeight="4.6" orient="auto-start-reverse">
      <path d="M1.4 1.2 6.6 4 1.4 6.8Z" fill="#e0b23c"/></marker>
  </defs>`;
  let edgeSvg = '', flowSvg = '', nodeSvg = '';
  let ei = 0;
  edges.forEach(e => {
    const from = e.kind === 'evolved_from' ? e.parent : e.child;
    const to = e.kind === 'evolved_from' ? e.child : e.parent;
    const f = idxInCol[from], t = idxInCol[to];
    if (!f || !t) return;
    const x1 = X(f.g) + boxW, y1 = Y(f.i) + boxH / 2;
    const x2 = X(t.g), y2 = Y(t.i) + boxH / 2;
    const cp = Math.max(24, (x2 - x1) * 0.55);
    const d = 'M' + x1.toFixed(1) + ',' + y1.toFixed(1) +
      ' C' + (x1 + cp).toFixed(1) + ',' + y1.toFixed(1) + ' ' + (x2 - cp).toFixed(1) + ',' + y2.toFixed(1) + ' ' + x2.toFixed(1) + ',' + y2.toFixed(1);
    const ev = e.kind === 'evolved_from';
    const col = ev ? '#a98cf0' : '#e0b23c';
    edgeSvg += `<path class="ledge" data-ei="${ei}" data-from="${esc(from)}" data-to="${esc(to)}" data-kind="${e.kind}" d="${d}"
      stroke="${col}" stroke-width="1.6" opacity="${ev ? '.75' : '.8'}" marker-end="url(#ln-arw-${ev ? 'ev' : 'mg'})"/>`;
    flowSvg += `<path class="ledge-flow${ev ? '' : ' rev'}" data-ei="${ei}" data-from="${esc(from)}" data-to="${esc(to)}" d="${d}"
      stroke="${ev ? '#d8c6ff' : '#ffd97a'}" stroke-width="2.2"/>`;
    ei++;
  });
  // ── 节点 ──
  let ni = 0;
  names.forEach(nm => {
    const t = idxInCol[nm];
    if (!t) return;
    const x = X(t.g), y = Y(t.i);
    const s = infoByName[nm];
    const cc = s && s.cat ? catHue(s.cat) : null;
    nodeSvg += `<g class="lnode" data-lname="${esc(nm)}" transform="translate(${x},${y})">
      <g class="ln-anim" style="animation-delay:${Math.min(ni * 55, 700)}ms">
        ${cc ? `<rect class="lnbar" x="0" y="0" width="4" height="${boxH}" rx="2" fill="${cc}"/>` : ''}
        <rect class="lnbox" width="${boxW}" height="${boxH}" rx="9"/>
        <text class="lnname" x="${12 + (cc ? 0 : 6)}" y="18">${esc(nm.slice(0, 22))}</text>
        <text class="lnmeta" x="${12 + (cc ? 0 : 6)}" y="32">${s ? `v${esc(s.version)} · ${esc(s.cat)} · 被引 ${s.indeg}` : '（已归档）'}</text>
      </g></g>`;
    ni++;
  });
  return `<svg id="lineage-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet"
    style="min-width:${W}px; max-width:${Math.round(W * 1.45)}px; margin:0 auto">${defs}
    <g class="lnedges">${edgeSvg}</g><g class="lnflow">${flowSvg}</g>${nodeSvg}</svg>`;
}

/* 类别 → 固定色相（与星图同源金角取色，浅色框内用中等饱和度） */
function catHue(cat) {
  let h = 0;
  for (let i = 0; i < cat.length; i++) h = (h * 31 + cat.charCodeAt(i)) >>> 0;
  const hue = 150 + (h % 246);
  return 'hsl(' + hue + ',46%,52%)';
}

/* 谱系图交互绑定 + 新记录生长 */
function bindLineageGraph(v, d, infoByName, pathByName) {
  const svg = $('#lineage-svg', v);
  if (!svg) return;
  const tip = $('#ln-tip', v);
  const box = $('.ln-wrap', v);
  const highlight = (from, to, kind, on) => {
    svg.classList.toggle('emph', on);
    $$('.ledge', svg).forEach(p => {
      p.classList.toggle('hot', on && p.dataset.ei === String(edgeIdx(from, to, kind)));
      p.classList.toggle('dim', on && p.dataset.ei !== String(edgeIdx(from, to, kind)));
    });
    $$('.lnode', svg).forEach(g => g.classList.toggle('dim', on && g.dataset.lname !== from && g.dataset.lname !== to));
    $$('.ledge-flow', svg).forEach(p => p.classList.toggle('on', on && p.dataset.ei === String(edgeIdx(from, to, kind))));
  };
  const edgeIdx = (from, to, kind) => {
    // 与渲染顺序一致的索引（lineageSvg 里 edges.forEach 顺序）
    let i = 0;
    for (const e of d.edges) {
      if (e.kind === kind &&
        ((kind === 'evolved_from' && e.parent === from && e.child === to) ||
          (kind === 'merged_into' && e.child === from && e.parent === to))) return i;
      i++;
    }
    return -1;
  };
  const showTip = (x, y, html) => {
    if (!tip || !box) return;
    tip.innerHTML = html;
    tip.classList.remove('hidden');
    const r = box.getBoundingClientRect();
    tip.style.left = Math.min(x, r.width - 250) + 'px';
    tip.style.top = (y - 10) + 'px';
  };
  $$('.lnode', svg).forEach(g => {
    g.addEventListener('mouseenter', e => {
      const nm = g.dataset.lname;
      const s = infoByName[nm];
      svg.classList.toggle('emph', true);
      $$('.lnode', svg).forEach(o => o.classList.toggle('dim', o !== g));
      $$('.ledge', svg).forEach(p => {
        p.classList.toggle('hot', p.dataset.from === nm || p.dataset.to === nm);
        p.classList.toggle('dim', p.dataset.from !== nm && p.dataset.to !== nm);
      });
      if (tip && box) showTip(e.clientX - box.getBoundingClientRect().left, e.clientY - box.getBoundingClientRect().top,
        '<b>' + esc(nm) + '</b> <span class="faint">' + esc(s ? (s.evolved_from ? '繁殖自 ' + s.evolved_from : s.merged_into ? '已合并入 ' + s.merged_into : '第一代') : '已归档') + '</span>');
    });
    g.addEventListener('mouseleave', () => {
      svg.classList.remove('emph');
      $$('.lnode', svg).forEach(o => o.classList.remove('dim'));
      $$('.ledge', svg).forEach(p => p.classList.remove('hot', 'dim'));
      if (tip) tip.classList.add('hidden');
    });
    g.addEventListener('click', () => openLineageNode(g.dataset.lname, infoByName, pathByName));
  });
  $$('.ledge', svg).forEach(p => {
    p.addEventListener('mouseenter', e => {
      const from = p.dataset.from, to = p.dataset.to, kind = p.dataset.kind;
      highlight(from, to, kind, true);
      const rel = kind === 'evolved_from'
        ? '<b style="color:#a98cf0">' + esc(from) + '</b> 繁殖出 <b style="color:#a98cf0">' + esc(to) + '</b>'
        : '<b style="color:#e0b23c">' + esc(to) + '</b> 合并入 <b style="color:#e0b23c">' + esc(from) + '</b>';
      if (tip && box) showTip(e.clientX - box.getBoundingClientRect().left, e.clientY - box.getBoundingClientRect().top,
        rel + '<br><span class="faint">点击看详情</span>');
    });
    p.addEventListener('mouseleave', () => {
      svg.classList.remove('emph');
      $$('.lnode', svg).forEach(o => o.classList.remove('dim'));
      $$('.ledge', svg).forEach(x => x.classList.remove('hot', 'dim'));
      if (tip) tip.classList.add('hidden');
    });
    p.addEventListener('click', e => {
      e.stopPropagation();
      // from/to 是"流向"，详情抽屉要的是（子代, 亲代/去向）
      const kind = p.dataset.kind, from = p.dataset.from, to = p.dataset.to;
      const child = kind === 'evolved_from' ? to : from;
      const parent = kind === 'evolved_from' ? from : to;
      openEdgeDetail(kind, child, parent, infoByName, pathByName);
    });
  });
  /* 边悬停时"光在流"：CSS 动画本身持续流动；hot 时提亮并重播一次（重触发） */
}

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
