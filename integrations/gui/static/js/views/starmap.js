/* ============================================================
   views/starmap.js — 技能星图 v0.3（T6+T7）
   · 手写力导向物理仿真（viz/force.js）：斥力+弹簧+中心引力
   · 滚轮缩放（指针为中心）+ 四向拖拽平移 + 双击复位 + 节点拖拽定位
   · 标签渐进披露：缩放层级决定显隐；悬停常亮本节点名
   · 候选 = 虚线半透明"待孵化光点"；悬停高亮血缘传递闭包链，淡化其余
   · 收藏☆修复（polyfill CSS.escape 后 focusSkill 正常）
   ============================================================ */
'use strict';

/* 渲染上下文（跨函数共享） */
const SM = {
  sim: null, svg: null, gRoot: null, gEdges: null, gNodes: null,
  view: { x: 0, y: 0, k: 1 },     // 视图变换
  nodes: [], byId: {}, visible: [], d: null,
  hoverName: null, chain: null,   // 血缘链闭包（Set of name）
};

RENDER.starmap = async function (v) {
  const d = await cached('skills', 20000, () => EcoApi.get('/api/skills'));
  const T = S.starmap;
  const cats = Object.keys(d.cats).sort();
  let visible = d.skills.slice();
  if (T.cat) visible = visible.filter(s => s.cat === T.cat);
  const top20 = visible.slice().sort((a, b) => b.indeg - a.indeg).slice(0, 20).map(s => s.name);
  if (T.mode === 'skeleton')
    visible = visible.filter(s => top20.includes(s.name) || S.starred.includes(s.name)
      || s.evolved_from || s.merged_into || s.dup);
  SM.d = d; SM.visible = visible;

  // 候选光点数据（全量候选，不受分类筛选限制——孵化台语义）
  const candNames = d.candidates || [];

  v.innerHTML = `
    <div class="vh">技能星图 <small>${d.skills.length} 个 SKILL.md · 唯一 ${d.unique} · 候选 ${candNames.length} · 力导向布局（拖拽/缩放/平移）</small></div>
    <div class="starmap-tools">
      <div class="pills"><button data-sm="skeleton" class="${T.mode === 'skeleton' ? 'on' : ''}">骨架视野</button>
      <button data-sm="full" class="${T.mode === 'full' ? 'on' : ''}">全图</button>
      <button data-sm="list" class="${T.list ? 'on' : ''}">列表模式</button></div>
      <select id="sm-cat"><option value="">全部分类</option>${cats.map(c =>
        `<option ${T.cat === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>
      <label><input type="checkbox" id="ln-rel" ${T.lines.rel ? 'checked' : ''}> 引用线</label>
      <label><input type="checkbox" id="ln-blood" ${T.lines.blood ? 'checked' : ''}> 血缘线</label>
      <label><input type="checkbox" id="ln-dup" ${T.lines.dup ? 'checked' : ''}> 重复线</label>
      <button class="ghost-btn" id="sm-reset" title="双击空白也可复位">复位视图</button>
      <span class="faint">滚轮缩放 · 拖空白平移 · 拖节点定位 · 双击复位 · 悬停高亮血缘链 · 虚线光点=候选</span>
    </div>
    ${T.list ? listMode(d, visible) : `<div class="starmap-wrap">
      <svg id="starmap-svg" viewBox="0 0 1000 760"><g id="sm-root">
        <g id="sm-edges"></g><g id="sm-cands"></g><g id="sm-nodes"></g>
      </g></svg>
      <div class="starmap-hint faint">缩放 ${'1.0'}×</div>
      <div id="starmap-pop"></div></div>`}`;

  $$('[data-sm]', v).forEach(b => b.onclick = () => {
    const m = b.dataset.sm;
    if (m === 'list') T.list = !T.list; else { T.mode = m; T.list = false; }
    route();
  });
  const catSel = $('#sm-cat');
  if (catSel) catSel.onchange = e => { T.cat = e.target.value; route(); };
  [['ln-rel', 'rel'], ['ln-blood', 'blood'], ['ln-dup', 'dup']].forEach(([id, k]) => {
    const el = $('#' + id);
    if (el) el.onchange = e => { T.lines[k] = e.target.checked; if (!T.list) mountForce(); };
  });
  const resetBtn = $('#sm-reset');
  if (resetBtn) resetBtn.onclick = () => { SM.view = { x: 0, y: 0, k: 1 }; applyView(); };
  if (!T.list) mountForce();
  $$('[data-goto-skill]', v).forEach(a => a.onclick = () => gotoSkill(a.dataset.gotoSkill));
  $$('[data-openmd]', v).forEach(a => a.onclick = () => openSkillMd(a.dataset.openmd));
};

function listMode(d, visible) {
  const cats = Object.keys(d.cats).sort();
  return cats.map(c => {
    const rows = visible.filter(s => s.cat === c);
    if (!rows.length) return '';
    return `<div class="card"><h3>${esc(c)} <small>${rows.length} 个</small></h3>
      <table><tr><th>技能</th><th>version</th><th>status/fate</th><th>入度</th><th>related</th><th>备注</th></tr>
      ${rows.map(s => `<tr><td><a data-openmd="${esc(s.path)}">${esc(s.name)}</a></td>
        <td>${esc(s.version)}</td><td><span class="tag">${esc(s.status)}/${esc(s.fate)}</span></td>
        <td class="num">${s.indeg}</td><td class="faint">${esc(s.related.join(', ') || '—')}</td>
        <td class="faint">${s.dup ? '同名副本 ' : ''}${s.dangling.length ? '悬空引用' : ''}</td></tr>`).join('')}</table></div>`;
  }).join('');
}

/* ── 力导向挂载与渲染 ── */
function mountForce() {
  const svg = $('#starmap-svg');
  if (!svg) return;
  SM.svg = svg;
  SM.gRoot = $('#sm-root', svg);
  SM.gEdges = $('#sm-edges', svg);
  SM.gNodes = $('#sm-nodes', svg);
  const d = SM.d, visible = SM.visible;
  const T = S.starmap;

  // 关闭旧仿真
  if (SM.sim) SM.sim.stop();

  // ── 节点 ──
  const nodes = [], byId = {};
  visible.forEach(s => {
    const n = { id: s.name, skill: s, fixed: false,
      x: (Math.random() - 0.5) * 600, y: (Math.random() - 0.5) * 440, vx: 0, vy: 0,
      r: 5 + Math.min(12, s.indeg * 2.2),
      color: nodeColor(Object.keys(d.cats).sort().indexOf(s.cat), Math.max(1, Object.keys(d.cats).length)) };
    nodes.push(n); byId[s.name] = n;
  });
  // 候选光点
  (d.candidates || []).forEach(cn => {
    const n = { id: 'candidate:' + cn, cand: true, fixed: false,
      x: (Math.random() - 0.5) * 500, y: (Math.random() - 0.5) * 380, vx: 0, vy: 0, r: 7, color: 'var(--gate3)' };
    nodes.push(n); byId[n.id] = n;
  });
  SM.nodes = nodes; SM.byId = byId;

  // ── 边 ──
  const links = [];
  if (T.lines.rel)
    visible.forEach(s => s.related.forEach(r => {
      if (byId[r] && r > s.name) links.push({ a: byId[s.name], b: byId[r], k: 0.012, len: 92, kind: 'rel' });
    }));
  if (T.lines.blood)
    visible.forEach(s => {
      ['evolved_from', 'merged_into'].forEach(kk => {
        const p = s[kk];
        if (p && byId[p]) links.push({ a: byId[p], b: byId[s.name], k: 0.03, len: 110, kind: 'blood', bkind: kk });
      });
    });
  if (T.lines.dup) {
    const groups = {};
    visible.forEach(s => { if (s.dup) (groups[s.name] = groups[s.name] || []).push(s); });
    Object.entries(groups).forEach(([nm, g]) => {
      for (let i = 1; i < g.length; i++)
        if (byId[g[i].name] && byId[g[0].name]) links.push({ a: byId[g[i].name], b: byId[g[0].name], k: 0.05, len: 70, kind: 'dup' });
    });
  }

  // ── SVG 骨架 ──
  SM.gEdges.innerHTML = links.map((L, i) => {
    const stroke = L.kind === 'blood' ? 'var(--blood-line)' : L.kind === 'dup' ? 'var(--dup-line)' : 'var(--rel-line)';
    const dash = L.kind === 'dup' ? '5 4' : L.kind === 'blood' ? '' : '';
    const w = L.kind === 'rel' ? 1 : 1.8;
    return `<line id="sm-l${i}" data-kind="${L.kind}" ${dash ? `stroke-dasharray="${dash}"` : ''} stroke="${stroke}" stroke-width="${w}" opacity=".55"${L.kind === 'blood' ? ' marker-end="url(#arw)"' : ''}/>`;
  }).join('');
  // 候选光点
  $('#sm-cands', svg) && (SM.gCands = $('#sm-cands', svg));
  SM.gNodes.innerHTML = nodes.map(n => {
    if (n.cand) {
      return `<g class="node cand" data-name="${esc(n.id)}" opacity=".75">
        <circle r="${n.r}" fill="none" stroke="var(--gate3)" stroke-width="1.4" stroke-dasharray="3 3"/>
        <circle r="2.6" fill="var(--gate3)" fill-opacity=".8"/>
        <title>候选技能（待孵化）：${esc(n.id.replace('candidate:', ''))}</title></g>`;
    }
    const s = n.skill, starred = S.starred.includes(n.id);
    return `<g class="node" data-name="${esc(n.id)}">
      <circle r="${n.r}" fill="${n.color}" fill-opacity="${statusOp[s.status] ?? .5}"
        stroke="${starred ? 'var(--warn)' : 'var(--ink)'}" stroke-width="${starred ? 1.8 : .9}"/>
      ${s.dup ? `<circle r="${n.r + 3}" fill="none" stroke="var(--dup-line)" stroke-width="1.2"/>` : ''}
      ${s.dangling.length ? `<circle cx="${n.r * .85}" cy="${-n.r * .85}" r="2.6" fill="var(--bad)"/>` : ''}
      <text class="nlabel" text-anchor="middle" y="${n.r + 12}" opacity="0">${esc(n.id.slice(0, 18))}</text>
      </g>`;
  }).join('');

  // 图例（候选说明）
  const popWrap = $('#starmap-pop');
  if (popWrap && !$('.starmap-legend')) {
    const lg = document.createElement('div');
    lg.className = 'starmap-legend faint';
    lg.innerHTML = '⃝虚线光点 = 候选技能（待孵化，skills/.candidates）· 🔴点 = 悬空引用 · 🟡环 = 同名副本';
    popWrap.parentElement.appendChild(lg);
  }

  // ── 仿真 ──
  SM.sim = new ForceSim();
  SM.sim.setGraph(nodes, links);
  SM.sim.onTick = renderPositions;
  renderPositions();
  bindStarInteractions(svg, d);
}

function renderPositions() {
  if (!SM.svg) return;
  // 边
  const T = S.starmap;
  const links = SM.sim.links;
  links.forEach((L, i) => {
    const el = document.getElementById('sm-l' + i);
    if (!el) return;
    el.setAttribute('x1', L.a.x.toFixed(1)); el.setAttribute('y1', L.a.y.toFixed(1));
    el.setAttribute('x2', L.b.x.toFixed(1)); el.setAttribute('y2', L.b.y.toFixed(1));
    el.style.display = (L.kind === 'rel' && !T.lines.rel) ? 'none' : '';
  });
  // 节点 + 标签渐进披露（缩放层级）
  const k = SM.view.k;
  const labelLevel = k < 0.7 ? 2 : k < 1.2 ? 1 : 0;   // 0=全部 1=入度≥1/收藏/血缘 2=入度≥2/收藏
  SM.gNodes && $$('.node', SM.gNodes).forEach(g => {
    const n = SM.byId[g.dataset.name];
    if (!n) return;
    g.setAttribute('transform', `translate(${n.x.toFixed(1)},${n.y.toFixed(1)})`);
    const lab = $('.nlabel', g);
    if (lab) {
      let show = k >= 1.2;
      if (!show && labelLevel === 1) show = n.skill && (n.skill.indeg >= 1 || S.starred.includes(n.id) || n.skill.evolved_from || n.skill.merged_into);
      if (!show && labelLevel === 2) show = n.skill && (n.skill.indeg >= 2 || S.starred.includes(n.id));
      if (SM.hoverName === n.id) show = true;
      lab.setAttribute('opacity', show ? '1' : '0');
    }
    // 血缘链高亮/淡化
    if (SM.chain) {
      const inChain = SM.chain.has(n.id) || (n.cand && false);
      g.setAttribute('opacity', inChain ? '1' : '.15');
    } else {
      g.setAttribute('opacity', n.cand ? '.75' : '1');
    }
  });
  applyView();
}

function applyView() {
  if (SM.gRoot)
    SM.gRoot.setAttribute('transform', `translate(${SM.view.x},${SM.view.y}) scale(${SM.view.k})`);
  const hint = $('.starmap-hint');
  if (hint) hint.textContent = '缩放 ' + SM.view.k.toFixed(1) + '×';
}

/* 世界坐标 ↔ 屏幕坐标 */
function svgPoint(evt) {
  const rect = SM.svg.getBoundingClientRect();
  const vb = SM.svg.viewBox.baseVal;
  const sx = (evt.clientX - rect.left) / rect.width * vb.width;
  const sy = (evt.clientY - rect.top) / rect.height * vb.height;
  return { x: (sx - SM.view.x) / SM.view.k, y: (sy - SM.view.y) / SM.view.k };
}

function bindStarInteractions(svg, d) {
  // —— 滚轮缩放（以指针为中心）——
  svg.addEventListener('wheel', e => {
    e.preventDefault();
    const rect = svg.getBoundingClientRect();
    const vb = svg.viewBox.baseVal;
    const px = (e.clientX - rect.left) / rect.width * vb.width;
    const py = (e.clientY - rect.top) / rect.height * vb.height;
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    const nk = Math.max(0.3, Math.min(4, SM.view.k * factor));
    SM.view.x = px - (px - SM.view.x) * (nk / SM.view.k);
    SM.view.y = py - (py - SM.view.y) * (nk / SM.view.k);
    SM.view.k = nk;
    applyView(); renderPositions();
  }, { passive: false });

  // —— 平移 / 节点拖拽（pointer 事件，兼容旧内核 mouse 事件降级）——
  let drag = null;
  const down = e => {
    const g = e.target.closest ? e.target.closest('.node') : null;
    if (g) {
      const n = SM.byId[g.dataset.name];
      if (n && !n.cand) {
        drag = { node: n, moved: false };
        n.fixed = true;
        SM.sim.reheat();
        e.preventDefault();
        return;
      }
    }
    drag = { pan: true, sx: e.clientX, sy: e.clientY, ox: SM.view.x, oy: SM.view.y };
  };
  const move = e => {
    if (!drag) return;
    if (drag.pan) {
      const rect = svg.getBoundingClientRect();
      SM.view.x = drag.ox + (e.clientX - drag.sx) * (svg.viewBox.baseVal.width / rect.width);
      SM.view.y = drag.oy + (e.clientY - drag.sy) * (svg.viewBox.baseVal.height / rect.height);
      applyView();
    } else if (drag.node) {
      const p = svgPoint(e);
      drag.node.x = p.x; drag.node.y = p.y;
      drag.moved = true;
      SM.sim.reheat();
    }
  };
  const up = () => {
    if (drag && drag.node) {
      // 拖拽结束保持钉住（用户定位语义）；再点一次节点可取消钉住
      if (!drag.moved) drag.node.fixed = false;
    }
    drag = null;
  };
  if (window.PointerEvent) {
    svg.addEventListener('pointerdown', down);
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  } else {
    svg.addEventListener('mousedown', down);
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  }

  // —— 双击空白复位 ——
  svg.addEventListener('dblclick', e => {
    if (e.target.closest && e.target.closest('.node')) return;
    SM.view = { x: 0, y: 0, k: 1 };
    applyView(); renderPositions();
  });

  // —— 悬停：常亮名字 + 血缘链高亮 ——
  svg.addEventListener('mouseover', e => {
    const g = e.target.closest ? e.target.closest('.node') : null;
    if (!g) return;
    const name = g.dataset.name;
    if (g.classList.contains('cand')) {
      SM.hoverName = name;
      renderPositions();
      return;
    }
    SM.hoverName = name;
    SM.chain = bloodChain(name, d);
    renderPositions();
  });
  svg.addEventListener('mouseout', e => {
    const g = e.target.closest ? e.target.closest('.node') : null;
    if (!g) return;
    SM.hoverName = null; SM.chain = null;
    renderPositions();
  });

  // —— 点击：浮窗 ——
  svg.addEventListener('click', e => {
    const g = e.target.closest ? e.target.closest('.node') : null;
    const pop = $('#starmap-pop');
    if (!g) { if (pop) pop.innerHTML = ''; return; }
    if (g.classList.contains('cand')) {
      const nm = g.dataset.name.replace('candidate:', '');
      showCandPopover(nm, e.clientX, e.clientY);
      return;
    }
    const s = d.skills.find(x => x.name === g.dataset.name);
    if (!s) return;
    showPopover(s.name, s, e.clientX, e.clientY, d);
  });
}

/* 血缘传递闭包（双向 BFS：evolved_from 父链 + merged_into 子链） */
function bloodChain(name, d) {
  const set = new Set([name]);
  const byName = {};
  d.skills.forEach(s => { byName[s.name] = s; });
  // 祖先方向（沿 evolved_from/merged_into 向上）
  const up = [name];
  while (up.length) {
    const cur = byName[up.pop()];
    if (!cur) continue;
    ['evolved_from', 'merged_into'].forEach(kk => {
      const p = cur[kk];
      if (p && byName[p] && !set.has(p)) { set.add(p); up.push(p); }
    });
  }
  // 后代方向（谁 evolved_from/merged_into 指向我）
  const down = [name];
  while (down.length) {
    const cur = down.pop();
    d.skills.forEach(s => {
      if ((s.evolved_from === cur || s.merged_into === cur) && !set.has(s.name)) { set.add(s.name); down.push(s.name); }
    });
  }
  return set;
}

function showCandPopover(name, cx, cy) {
  const pop = $('#starmap-pop');
  pop.innerHTML = `<div class="popover" style="left:${Math.min(cx, innerWidth - 320)}px; top:${Math.min(cy, innerHeight - 240)}px">
    <h4>⃝ ${esc(name)} <span class="tag">候选 · 待孵化</span></h4>
    <div class="dim" style="font-size:11.5px">位于 skills/.candidates，观察期候选。可在候选孵化台发起孵化（走闸门，调 eco_breed）。</div>
    <div class="btns"><button data-goto="candidates">去候选孵化台</button></div></div>`;
  $$('[data-goto]', pop).forEach(b => b.onclick = () => goto(b.dataset.goto));
}

const statusOp = { active: .92, undeclared: .45, dormant: .3, frozen: .18, candidate: .8 };

function showPopover(name, s, cx, cy, d) {
  const pop = $('#starmap-pop');
  const starred = S.starred.includes(name);
  pop.innerHTML = `<div class="popover" style="left:${Math.min(cx, innerWidth - 320)}px; top:${Math.min(cy, innerHeight - 300)}px">
    <span class="pstar ${starred ? 'on' : ''}" title="收藏（加入默认骨架）">${starred ? '★' : '☆'}</span>
    <h4>${esc(name)}</h4>
    <div class="fm">
      <span>version</span><span>${esc(s.version)}</span>
      <span>status/fate</span><span>${esc(s.status)} / ${esc(s.fate)}</span>
      <span>domain/env</span><span>${esc(s.domain || '—')} / ${esc(s.environment || '—')}</span>
      <span>分类</span><span>${esc(s.cat)}</span>
      <span>入度</span><span>${s.indeg}（被引）</span>
      <span>血缘</span><span>${esc(s.evolved_from || s.merged_into || '—')}</span>
      <span>引用</span><span>${esc(s.related.join(', ') || '—')}</span>
      ${s.dangling.length ? `<span>悬空</span><span style="color:var(--bad)">${esc(s.dangling.join(', '))}</span>` : ''}
    </div>
    <div class="dim" style="font-size:11.5px">${esc(s.desc || '')}</div>
    <div class="btns">
      <button data-openmd="${esc(s.path)}">打开 SKILL.md</button>
      ${s.evolved_from || s.merged_into ? `<button data-goto-skill="${esc(s.evolved_from || s.merged_into)}">查看谱系对象</button>` : ''}
      ${s.related.filter(r => d.skills.some(x => x.name === r)).slice(0, 3).map(r =>
        `<button data-goto-skill="${esc(r)}">→ ${esc(r.slice(0, 12))}</button>`).join('')}
    </div></div>`;
  $('.pstar', pop).onclick = () => toggleStar(name);
  $$('[data-goto-skill]', pop).forEach(b => b.onclick = () => gotoSkill(b.dataset.gotoSkill));
  $$('[data-openmd]', pop).forEach(b => b.onclick = () => openSkillMd(b.dataset.openmd));
}
function toggleStar(name) {
  const i = S.starred.indexOf(name);
  if (i >= 0) S.starred.splice(i, 1); else S.starred.push(name);
  localStorage.setItem('eco_star', JSON.stringify(S.starred));
  route();
}
/* focusSkill（跨视图跳转定位）—— polyfill 后 CSS.escape 可用；顺带平移视图让目标居中 */
function focusSkill(name) {
  const svg = $('#starmap-svg');
  if (!svg) { toast(`「${name}」不在星图（试试全图模式或清分类筛选）`, true); return; }
  const sel = '.node[data-name="' + CSS.escape('candidate:' + name) + '"]';
  let g = $(sel, svg);
  if (!g) g = $('.node[data-name="' + CSS.escape(name) + '"]', svg);
  if (!g) { toast(`「${name}」不在当前视野（试试全图模式或清分类筛选）`, true); return; }
  const n = SM.byId[g.dataset.name];
  if (n) { SM.view.x = -n.x * SM.view.k + 500; SM.view.y = -n.y * SM.view.k + 380; applyView(); }
  const c = $('circle', g);
  if (c) {
    c.style.filter = 'drop-shadow(0 0 6px var(--accent))';
    c.setAttribute('stroke-width', '2.4');
    setTimeout(() => { c.style.filter = ''; }, 2600);
  }
  toast(`已定位：${name}`, true);
}
async function openSkillMd(path) {
  try {
    const r = await EcoApi.get(`/api/skills/md?path=${encodeURIComponent(path)}`);
    openDrawer(`<div class="vh">SKILL.md <small>${esc(path)}</small></div>
      <div class="md">${md(r.md.body)}</div>`);
  } catch (e) { toast('读取失败：' + e.message); }
}

function nodeColor(i, n) {
  const hue = 175 + (i / Math.max(1, n)) * 85;
  return `hsl(${hue.toFixed(0)},32%,52%)`;
}
