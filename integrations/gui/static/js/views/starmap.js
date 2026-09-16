/* ============================================================
   views/starmap.js — 技能星图 v0.3.3（T1 视觉重构）
   相对 v0.3.2 的改动（宿主点名"有点丑"）：
     ① 节点：径向渐变球体（高光偏左上）+ 类别色外环 + 低 alpha 光晕；
        半径按 √入度 映射（比线性更平滑），悬空引用/同名副本角标保留
     ② 边：二次贝塞尔（同源多边走不同弧度）；血缘边带箭头 + 流动光点
        （纯 CSS dashoffset 动画，零 rAF）；引用边为无向（数据本身无向，不臆造方向）
     ③ 分组：按 cat 的软分组——弱聚类引力 + 低 alpha 椭圆底 + 组名，能看出"哪一坨是一类"
     ④ 标签：贪心避让（下方 → 右侧 → 上方三候选位）+ 描边光晕提可读性；缩放分档渐进披露
     ⑤ 入场：螺旋爆散（沿用物理引擎）+ 节点错峰 scale 0→1 + 边两端向中间生长；
        收敛后自动 fit（占画布 70–80%），「复位视图」回到 fit 态
     ⑥ 悬停：涟漪扩散一次 + 1 跳邻域高亮（其余淡化）+ 关系清单浮出
     ⑦ 点击：聚焦模式（选中 + 邻域常亮，其余淡化）；点空白清除
     ⑧ 深空大屏 f：节点发光描边 + 边"近亮远暗"（按与图心的距离调 alpha）
   红线：数据先上屏、动效可打断、low 档静态、切走视图停仿真（SM.sim.stop）。
   ============================================================ */
'use strict';

/* 渲染上下文（跨函数共享） */
const SM = {
  sim: null, svg: null, gRoot: null, gEdges: null, gGroups: null, gNodes: null,
  view: { x: 0, y: 0, k: 1 },     // 视图变换
  nodes: [], byId: {}, links: [], visible: [], d: null,
  hoverName: null, focusName: null, chain: null, hood: null,
  fit: null,                       // 自动适配得到的视图（复位目标）
  catColors: [], catCi: {},
  labels: {}, labelTick: 0, autoFitPending: false, userMoved: false,
  entrance: null, follow: null, light: true,
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
  SM.hoverName = null; SM.focusName = null; SM.chain = null; SM.hood = null;
  SM.fit = null; SM.userMoved = false; SM.entrance = null; SM.light = true;
  SM.catColors = catPalette(cats.length);
  // 类别 → 索引（2D/3D 两个渲染器都要用，所以在这里就建好；之前只在 mountForce 里建，
  // 导致 3D 视图所有节点都拿不到自己的类别色）
  SM.catCi = {};
  cats.forEach((c, i) => { SM.catCi[c] = i; });

  const candNames = d.candidates || [];
  const nRel = visible.reduce((a, s) => a + s.related.filter(r => visible.some(x => x.name === r)).length, 0);
  const nBlood = visible.filter(s => (s.evolved_from && visible.some(x => x.name === s.evolved_from))
    || (s.merged_into && visible.some(x => x.name === s.merged_into))).length;
  const form = T.form === '3d' ? '3d' : 'flat';

  v.innerHTML = `
    <div class="vh">技能星图 <small>${d.skills.length} 个 SKILL.md · 唯一 ${d.unique} · 候选 ${candNames.length} · 当前视野 ${visible.length} 节点 / ${nRel} 引用边 / ${nBlood} 血缘边</small></div>
    <div class="starmap-tools">
      <div class="pills"><button data-sf="3d" class="${form === '3d' ? 'on' : ''}" title="WebGL 玻璃球体 + 真实透射折射 + 环境反射 + 血缘光流">3D 星空</button>
      <button data-sf="flat" class="${form === 'flat' ? 'on' : ''}" title="2D 力导向平铺视图（可拖拽定位、缩放适配）">平铺视图</button></div>
      <div class="pills"><button data-sm="skeleton" class="${T.mode === 'skeleton' ? 'on' : ''}">骨架视野</button>
      <button data-sm="full" class="${T.mode === 'full' ? 'on' : ''}">全图</button>
      <button data-sm="list" class="${T.list ? 'on' : ''}">列表模式</button></div>
      <select id="sm-cat"><option value="">全部分类</option>${cats.map(c =>
        `<option ${T.cat === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>
      <span class="sm-toggle"><label><input type="checkbox" id="ln-rel" ${T.lines.rel ? 'checked' : ''}> 引用线</label>
      <label><input type="checkbox" id="ln-blood" ${T.lines.blood ? 'checked' : ''}> 血缘线</label>
      <label><input type="checkbox" id="ln-dup" ${T.lines.dup ? 'checked' : ''}> 重复线</label></span>
      ${form === 'flat' ? `
        <button class="ghost-btn" id="sm-fit" title="自动适配：让图占满画布（收敛后也会自动做一次）">自适应</button>
        <button class="ghost-btn" id="sm-reset" title="回到自适应视图（双击空白同效）">复位视图</button>
        <span class="faint">滚轮缩放 · 拖空白平移 · 拖节点定位 · 悬停看邻域 · 单击聚焦</span>` : `
        <div class="pills"><button data-gl="sphere">球体星云</button><button data-gl="flow">流式纵列</button><button data-gl="grid">立方晶格</button></div>
        <label class="sm-toggle"><input type="checkbox" id="gl-rotate" ${GL3D.rotate ? 'checked' : ''}> 自转</label>
        <button class="ghost-btn" id="gl-reset">视角复位</button>
        <span class="faint">拖拽旋转 · 滚轮推拉 · 悬停看邻域 · 单击选中 · 玻璃=技能（透射折射）· 光点=血缘流向</span>`}
    </div>
    <div id="sm-stage"></div>`;

  $$('[data-sf]', v).forEach(b => b.onclick = () => {
    if (T.form === b.dataset.sf) return;
    T.form = b.dataset.sf;
    try { localStorage.setItem('eco_starmap_form', T.form); } catch (e) { }
    if (T.form === 'flat') { try { GL3D.stop(); } catch (e) { } }
    route();
  });
  $$('[data-sm]', v).forEach(b => b.onclick = () => {
    const m = b.dataset.sm;
    if (m === 'list') T.list = !T.list; else { T.mode = m; T.list = false; }
    route();
  });
  const catSel = $('#sm-cat');
  if (catSel) catSel.onchange = e => { T.cat = e.target.value; route(); };
  [['ln-rel', 'rel'], ['ln-blood', 'blood'], ['ln-dup', 'dup']].forEach(([id, k]) => {
    const el = $('#' + id);
    if (el) el.onchange = e => {
      T.lines[k] = e.target.checked;
      if (form === 'flat') toggleEdgeKind(k, e.target.checked);
      else mount3D();
    };
  });
  const resetBtn = $('#sm-reset');
  if (resetBtn) resetBtn.onclick = () => applyFit(true);
  const fitBtn = $('#sm-fit');
  if (fitBtn) fitBtn.onclick = () => { computeFit(); applyFit(true); };
  const rotBox = $('#gl-rotate');
  if (rotBox) rotBox.onchange = e => GL3D.setRotate(e.target.checked);
  const glReset = $('#gl-reset');
  if (glReset) glReset.onclick = () => {
    if (GL3D.group) { GL3D.group.rotation.set(0, 0, 0); }
    if (GL3D.camera) GL3D.camera.position.set(0, 40, 1000);
    GL3D._paused = 0;
    if (!Anim.on()) GL3D.renderOnce();
  };
  $$('[data-gl]', v).forEach(b => b.onclick = () => {
    GL3D.mode = b.dataset.gl;
    $$('[data-gl]', v).forEach(x => x.classList.toggle('on', x === b));
    mount3D(true);
  });

  const stage = $('#sm-stage', v);
  if (T.list) {
    stage.innerHTML = listMode(d, visible);
  } else if (form === '3d') {
    stage.innerHTML = `<div class="starmap-wrap gl3d-wrap" id="sm-wrap">
      <div id="gl3d-host"></div>
      <div class="starmap-hint faint" id="gl3d-hint">正在初始化 3D 星空（本地 Three.js，无 CDN）…</div>
      <div id="sm-relcap" class="sm-relcap hidden"></div>
      <div id="starmap-pop"></div>
      <div class="starmap-legend faint">
        <span class="lgd"><i class="lglass"></i>玻璃球 = 技能（大小 = 被引次数）</span>
        <span class="lgd"><i class="lrel"></i>引用（无向）</span>
        <span class="lgd"><i class="lblood"></i>血缘（有向 · 光点流向 = 演化/合并方向）</span>
        <span class="lgd"><i class="ldup"></i>同名副本</span>
      </div></div>`;
    await mount3D();
  } else {
    stage.innerHTML = `<div class="starmap-wrap" id="sm-wrap">
      <svg id="starmap-svg" viewBox="0 0 1000 760" preserveAspectRatio="xMidYMid meet">
        <g id="sm-space"></g>
        <g id="sm-root">
        <g id="sm-groups"></g>
        <g id="sm-g-edges"><g data-ekind="rel"></g><g data-ekind="blood"></g><g data-ekind="dup"></g></g>
        <g id="sm-travel"></g>
        <g id="sm-nodes"></g>
      </g></svg>
      <div class="starmap-hint faint">缩放 1.0×</div>
      <div id="sm-relcap" class="sm-relcap hidden"></div>
      <div id="starmap-pop"></div></div>`;
    mountForce();
  }
  $$('[data-goto-skill]', v).forEach(a => a.onclick = () => gotoSkill(a.dataset.gotoSkill));
  $$('[data-openmd]', v).forEach(a => a.onclick = () => openSkillMd(a.dataset.openmd));
  // 低帧率自动降级：关掉流动光点（次要动效），并把粒子减半（Particles 自有兜底）
  if (typeof Anim !== 'undefined' && Anim.onDegrade)
    Anim.onDegrade(function () { SM.light = false; stripFlow(); if (GL3D.ready) GL3D.degrade(); });
};

/* 卸载平铺视图的引用（切到 3D 时调用）：留下悬空引用会让换肤/换档时的整帧重绘操作到
   已经不存在的 DOM 与"没有坐标"的节点上（曾因此在 3D 模式下换色板直接抛错）。 */
function teardownFlat() {
  if (SM.sim) SM.sim.stop();
  SM.sim = null; SM.svg = null; SM.gRoot = null; SM.gEdges = null;
  SM.gTravel = null; SM.gGroups = null; SM.gNodes = null; SM.gBlobs = null;
  SM.live = false;
}

/* ── 3D 星空挂载（WebGL 玻璃球 + 简单光追；失败/不可用自动回退平铺视图） ── */
async function mount3D(remount) {
  const hostEl = $('#gl3d-host');
  const hint = $('#gl3d-hint');
  if (!hostEl) return;
  teardownFlat();
  if (!GL3D.supported()) {
    if (hint) hint.textContent = '当前窗口不支持 WebGL —— 已回退平铺视图（功能不残缺）';
    toast('当前窗口不支持 WebGL，已切回平铺视图', true);
    S.starmap.form = 'flat';
    try { localStorage.setItem('eco_starmap_form', 'flat'); } catch (e) { }
    route();
    return;
  }
  const ok = await GL3D.load();
  if (!ok) {
    if (hint) hint.textContent = 'Three.js 本地库加载失败 —— 已回退平铺视图';
    toast('3D 库加载失败，已切回平铺视图', false);
    S.starmap.form = 'flat';
    try { localStorage.setItem('eco_starmap_form', 'flat'); } catch (e) { }
    route();
    return;
  }
  if (!GL3D.ready || GL3D.host !== hostEl) {
    GL3D.dispose();
    GL3D.host = hostEl;
    GL3D.build();
    if (!GL3D._bound) {
      GL3D.bind(on3DPick, on3DHover, on3DLeave);
      GL3D._bound = true;
    }
  }
  const spec = graphSpec();
  SM.nodes = spec.nodes; SM.links = spec.links; SM.byId = {};
  spec.nodes.forEach(n => { SM.byId[n.id] = n; });
  GL3D.mount(spec, Object.keys(SM.d.cats).sort(), SM.catColors);
  GL3D.resize();
  GL3D.group.rotation.y = GL3D.mode === 'flow' ? 0.35 : 0;
  if (Anim.on() && !Anim.degraded) GL3D.start(); else { GL3D.rotate = false; GL3D.renderOnce(); }
  $$('[data-gl]').forEach(x => x.classList.toggle('on', x.dataset.gl === GL3D.mode));
  if (hint) hint.textContent = '玻璃 = 技能 · 透射折射 + 环境反射 + 血缘光流 · 拖拽旋转 / 滚轮推拉 / 悬停看邻域';
  void remount;
}

/* 3D 悬停：复用平铺视图的"邻域 + 关系清单"语义 */
function on3DHover(nd, e) {
  if (!nd) { on3DLeave(); return; }
  SM.hoverName = nd.id;
  SM.hood = neighborsOf(nd.id);
  SM.relList = relationsOf(nd.id);
  GL3D.hood = SM.hood;
  updateRelcap(e);
  const pop = $('#starmap-pop');
  if (pop && !GL3D.sel) pop.innerHTML = '';
}
function on3DLeave() {
  SM.hoverName = null; SM.hood = null; SM.relList = null;
  GL3D.hood = null;
  updateRelcap();
}
function on3DPick(nd, e) {
  if (!nd) { GL3D.sel = null; on3DLeave(); return; }
  GL3D.sel = nd.id;
  SM.focusName = nd.id; SM.hood = neighborsOf(nd.id); SM.relList = relationsOf(nd.id);
  GL3D.hood = SM.hood;
  updateRelcap(e);
  if (nd.cand) { showCandPopover(nd.id.replace('candidate:', ''), e.clientX, e.clientY); return; }
  const s = SM.d.skills.find(x => x.name === nd.id);
  if (s) showPopover(s.name, s, e.clientX, e.clientY, SM.d);
}

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

/* ── 类别配色：金角螺旋取色（相邻类别色相拉开）
   星图是"深空观测窗"（独立于浅色外框的暗底面板）。节点采用**哑光玻璃/液态玻璃**质感：
   不是塑料球体（不要强暗缘 + 高光白点 + 硬描边），而是——
   低饱和、近白顶光的半透明球体 + 高光弧 + 底部折射亮缘，背景星云透过球体可见。 ── */
function catPalette(n) {
  const pal = document.documentElement.dataset.palette || 'a';
  const cfg = pal === 'c' ? { h0: 136, span: 214 } : pal === 'f' ? { h0: 150, span: 252 } : { h0: 150, span: 246 };
  const out = [];
  for (let i = 0; i < Math.max(1, n); i++) {
    const h = cfg.h0 + ((i * 137.508) % cfg.span);
    const g = (dl, ds) => 'hsl(' + h.toFixed(0) + ',' + Math.max(18, Math.min(78, 52 + (ds || 0))) + '%,' +
      Math.max(24, Math.min(97, 76 + dl)) + '%)';
    out.push({
      hi: g(20, -26),    // 球体顶部：近白（玻璃的漫反射面）
      mid: g(9, -14),    // 过渡
      base: g(-8, 0),    // 本色（玻璃色相）
      lo: g(-22, 8),     // 底部：色相更深（光折射后露出的体色）
      glow: g(-6, 6),    // 外柔光
      ring: g(4, 12),    // 边缘光
    });
  }
  return out;
}

/* 星野背景：静态星尘（3 组错峰闪烁）+ 星云 + 暗角 + 视差层
   尺寸按当前 viewBox 生成：viewBox 随容器纵横比走，星野才不会留出"信箱式"空白带 */
function spaceLayer(w, h) {
  const cx1 = w * .32, cy1 = h * .30, rx1 = w * .48, ry1 = h * .46;
  const cx2 = w * .72, cy2 = h * .72, rx2 = w * .44, ry2 = h * .42;
  let out = '<g class="par p0"><ellipse cx="' + cx1 + '" cy="' + cy1 + '" rx="' + rx1 + '" ry="' + ry1 + '" fill="url(#sm-neb1)"/></g>';
  out += '<g class="par p1">';
  for (let g = 0; g < 3; g++) {
    out += '<g class="dust d' + g + '">';
    for (let i = 0; i < 46; i++) {
      const x = (Math.random() * w).toFixed(0), y = (Math.random() * h).toFixed(0);
      const r = (Math.random() * 1.05 + .35).toFixed(2);
      const o = (Math.random() * .48 + .16).toFixed(2);
      out += '<circle cx="' + x + '" cy="' + y + '" r="' + r + '" fill="#d3e6f7" opacity="' + o + '"/>';
    }
    out += '</g>';
  }
  out += '</g>';
  out += '<g class="par p2"><ellipse cx="' + cx2 + '" cy="' + cy2 + '" rx="' + rx2 + '" ry="' + ry2 + '" fill="url(#sm-neb2)"/></g>';
  // 暗角：把注意力收到画面中心
  out += '<rect x="0" y="0" width="' + w + '" height="' + h + '" fill="url(#sm-vig)"/>';
  return out;
}

/* viewBox 随容器纵横比自适应（宽高比不一致时 `meet` 会留出空白带，星图会"浮"在中间） */
function syncViewBox() {
  const svg = SM.svg;
  if (!svg) return;
  const cw = svg.clientWidth || 1000, ch = svg.clientHeight || 700;
  const vbW = 1000, vbH = Math.max(320, Math.round(vbW * ch / Math.max(1, cw)));
  SM.vb = { w: vbW, h: vbH };
  if (svg.getAttribute('viewBox') !== '0 0 ' + vbW + ' ' + vbH)
    svg.setAttribute('viewBox', '0 0 ' + vbW + ' ' + vbH);
  if (SM.sim) { SM.sim.width = vbW; SM.sim.height = vbH; }
  const spaceEl = svg.querySelector('#sm-space');
  if (spaceEl) spaceEl.innerHTML = spaceLayer(vbW, vbH);
}

/* ── 图结构（2D 平铺视图与 3D 星空视图共用的唯一真相：
      哪些节点、哪些边、边是什么关系——两个渲染器都从这里取，避免语义漂移） ── */
function graphSpec() {
  const d = SM.d, visible = SM.visible, T = S.starmap;
  const byId = {};
  const nodes = visible.map(s => {
    const n = {
      id: s.name, skill: s, cat: s.cat,
      ci: SM.catCi[s.cat] != null ? SM.catCi[s.cat] : 0, cand: false,
      r: 6 + Math.min(13, Math.sqrt(Math.max(0, s.indeg)) * 3.4),
    };
    byId[n.id] = n;
    return n;
  });
  (d.candidates || []).forEach(cn => {
    const n = { id: 'candidate:' + cn, cand: true, cat: '候选', ci: -1, r: 8, skill: { name: cn, indeg: 0, status: 'candidate', related: [], dangling: [], cat: '候选' } };
    nodes.push(n); byId[n.id] = n;
  });
  const links = [];
  if (T.lines.rel)
    visible.forEach(s => s.related.forEach(r => {
      if (byId[r] && r > s.name) links.push({ a: byId[s.name], b: byId[r], kind: 'rel' });
    }));
  if (T.lines.blood)
    visible.forEach(s => ['evolved_from', 'merged_into'].forEach(kk => {
      const p = s[kk];
      if (!p || !byId[p]) return;
      const L = { a: byId[p], b: byId[s.name], kind: 'blood', bkind: kk };
      // 演化：亲代 → 子代；合并：子代 → 亲代（光点流向 = 真实语义方向）
      if (kk === 'evolved_from') { L.src = byId[p]; L.dst = byId[s.name]; }
      else { L.src = byId[s.name]; L.dst = byId[p]; }
      links.push(L);
    }));
  if (T.lines.dup) {
    const groups = {};
    visible.forEach(s => { if (s.dup) (groups[s.name] = groups[s.name] || []).push(s); });
    Object.entries(groups).forEach(([nm, g]) => {
      for (let i = 1; i < g.length; i++)
        if (byId[g[i].name] && byId[g[0].name]) {
          const L = { a: byId[g[i].name], b: byId[g[0].name], kind: 'dup' };
          L.src = L.a; L.dst = L.b;
          links.push(L);
        }
    });
  }
  return { nodes: nodes, links: links };
}

/* ── 力导向挂载与渲染 ── */
function mountForce() {
  const svg = $('#starmap-svg');
  if (!svg) return;
  SM.svg = svg;
  SM.gRoot = $('#sm-root', svg);
  SM.gEdges = $('#sm-g-edges', svg);
  SM.gTravel = $('#sm-travel', svg);
  SM.gGroups = $('#sm-groups', svg);
  SM.gNodes = $('#sm-nodes', svg);
  const d = SM.d, visible = SM.visible, T = S.starmap;
  const cats = Object.keys(d.cats).sort();
  SM.live = true;
  SM.linkCount = 0;
  SM.gBlobs = null; SM._hotE = null; SM._retNode = null; SM._entT && clearTimeout(SM._entT);

  // ── 节点（从共享图结构取，补上坐标与颜色） ──
  const spec = graphSpec();
  const nodes = spec.nodes, byId = {};
  nodes.forEach(n => {
    n.fixed = false;
    n.x = (Math.random() - 0.5) * 600; n.y = (Math.random() - 0.5) * 440; n.vx = 0; n.vy = 0;
    n.color = n.ci >= 0 && SM.catColors[n.ci] ? SM.catColors[n.ci].base : 'var(--accent)';
    n.s = 1;
    byId[n.id] = n;
  });
  SM.nodes = nodes; SM.byId = byId;
  const links = spec.links;
  SM.links = links;

  // ── defs：每类别一个"玻璃球体"渐变（近白顶 → 低饱和本色 → 略深底缘，半透明）
  const defs = [];
  for (let i = 0; i < cats.length; i++) {
    const c = SM.catColors[i] || { hi: '#f2f6fa', mid: '#dbe6f0', base: '#9db8d2', lo: '#5f7d9c', glow: '#8fb8dc' };
    defs.push('<radialGradient id="smg' + i + '" cx="36%" cy="30%" r="80%">' +
      '<stop offset="0%" stop-color="' + c.hi + '" stop-opacity=".96"/>' +
      '<stop offset="26%" stop-color="' + c.mid + '" stop-opacity=".86"/>' +
      '<stop offset="60%" stop-color="' + c.base + '" stop-opacity=".72"/>' +
      '<stop offset="100%" stop-color="' + c.lo + '" stop-opacity=".56"/></radialGradient>');
    defs.push('<radialGradient id="smh' + i + '">' +
      '<stop offset="0%" stop-color="' + c.glow + '" stop-opacity=".34"/>' +
      '<stop offset="46%" stop-color="' + c.glow + '" stop-opacity=".12"/>' +
      '<stop offset="100%" stop-color="' + c.glow + '" stop-opacity="0"/></radialGradient>');
  }
  // 底部折射亮缘（共享 def：渐变描边只在圆的下半圈发亮——液态玻璃"光被边缘弯折"的一笔）
  defs.push('<linearGradient id="sm-refr" x1="0" y1="0" x2="0" y2="1">' +
    '<stop offset="50%" stop-color="#eaf6ff" stop-opacity="0"/>' +
    '<stop offset="76%" stop-color="#eaf6ff" stop-opacity=".34"/>' +
    '<stop offset="100%" stop-color="#eaf6ff" stop-opacity=".82"/></linearGradient>');
  // 顶部高光（细线型光带，不是塑料亮斑）
  defs.push('<linearGradient id="sm-hl" x1="0" y1="0" x2="1" y2="0">' +
    '<stop offset="0%" stop-color="#fff" stop-opacity="0"/>' +
    '<stop offset="46%" stop-color="#fff" stop-opacity=".9"/>' +
    '<stop offset="100%" stop-color="#fff" stop-opacity="0"/></linearGradient>');
  // 环境遮蔽（球体下方的软暗影：挡住后面的星云 → 读作"悬在空间里"）
  defs.push('<radialGradient id="sm-ao">' +
    '<stop offset="0%" stop-color="#03060b" stop-opacity=".5"/>' +
    '<stop offset="62%" stop-color="#03060b" stop-opacity=".22"/>' +
    '<stop offset="100%" stop-color="#03060b" stop-opacity="0"/></radialGradient>');
  // 焦散光池（玻璃会聚光 → 球体下方一小片亮斑；只给枢纽节点，保持画面简约）
  defs.push('<radialGradient id="sm-caustic">' +
    '<stop offset="0%" stop-color="#cfe8ff" stop-opacity=".22"/>' +
    '<stop offset="55%" stop-color="#9fd0f0" stop-opacity=".08"/>' +
    '<stop offset="100%" stop-color="#9fd0f0" stop-opacity="0"/></radialGradient>');
  // 星云
  defs.push('<radialGradient id="sm-neb1">' +
    '<stop offset="0%" stop-color="#35b0e0" stop-opacity=".15"/>' +
    '<stop offset="52%" stop-color="#3a6ea8" stop-opacity=".065"/>' +
    '<stop offset="100%" stop-color="#080b12" stop-opacity="0"/></radialGradient>');
  defs.push('<radialGradient id="sm-neb2">' +
    '<stop offset="0%" stop-color="#8f6ce0" stop-opacity=".13"/>' +
    '<stop offset="58%" stop-color="#5a4a9a" stop-opacity=".05"/>' +
    '<stop offset="100%" stop-color="#080b12" stop-opacity="0"/></radialGradient>');
  // 暗角（radialGradient 的末段压暗，比 rect+box-shadow 更贴 SVG 坐标系）
  defs.push('<radialGradient id="sm-vig" cx="50%" cy="46%" r="72%">' +
    '<stop offset="58%" stop-color="#05070c" stop-opacity="0"/>' +
    '<stop offset="100%" stop-color="#05070c" stop-opacity=".55"/></radialGradient>');
  const mk = (id, col, dash) => '<marker id="' + id + '" viewBox="0 0 8 8" refX="7" refY="4" ' +
    'markerWidth="5" markerHeight="5" orient="auto-start-reverse">' +
    (dash ? '<path d="M1 1.6 6.4 4 1 6.4" fill="none" stroke="' + col + '" stroke-width="1.3" stroke-linecap="round"/>'
      : '<path d="M1.4 1.2 6.6 4 1.4 6.8Z" fill="' + col + '"/>') + '</marker>';
  defs.push(mk('sm-arw-blood', '#a98cf0'));
  defs.push(mk('sm-arw-dup', '#e0b23c', true));
  let defsEl = svg.querySelector('defs');
  if (!defsEl) { defsEl = document.createElementNS('http://www.w3.org/2000/svg', 'defs'); svg.insertBefore(defsEl, svg.firstChild); }
  defsEl.innerHTML = defs.join('');
  // 星野氛围层（视窗级，不参与缩放平移）；并让 viewBox 贴合容器纵横比
  syncViewBox();

  // ── 边元素：按类型分组（同源多边用不同弧度分散，避免完全重合） ──
  const pairSeen = {};
  links.forEach(L => {
    const key = [L.a.id, L.b.id].sort().join('\u0001');
    const idx = pairSeen[key] || 0;
    pairSeen[key] = idx + 1;
    L.curve = 0.10 + (idx % 2 ? -1 : 1) * Math.ceil(idx / 2) * 0.14;
    L.k = L.kind === 'rel' ? 0.010 : L.kind === 'dup' ? 0.06 : 0.05;
    L.len = L.kind === 'rel' ? 96 : L.kind === 'dup' ? 74 : 104;
    const g = $('[data-ekind="' + L.kind + '"]', SM.gEdges);
    const stroke = L.kind === 'blood' ? '#a98cf0' : L.kind === 'dup' ? '#e0b23c' : '#9dbdd8';
    const w = L.kind === 'rel' ? 1 : 1.7;
    const ns = 'http://www.w3.org/2000/svg';
    // 血缘边先铺一层宽而淡的辉光（发光感来自"宽淡底 + 细亮芯"两层，单层细线总是发灰）
    if (L.kind === 'blood') {
      const gel = document.createElementNS(ns, 'path');
      gel.setAttribute('class', 'eglow');
      gel.setAttribute('stroke', stroke);
      gel.setAttribute('stroke-width', '7');
      gel.setAttribute('opacity', '.14');
      gel.setAttribute('fill', 'none');
      g.appendChild(gel);
      L.gel = gel;
    }
    const p = document.createElementNS(ns, 'path');
    p.setAttribute('class', 'epath');
    p.setAttribute('data-kind', L.kind);
    if (L.kind === 'dup') p.setAttribute('stroke-dasharray', '5 3.5');
    p.setAttribute('stroke', stroke);
    p.setAttribute('stroke-width', w);
    p.setAttribute('opacity', L.kind === 'rel' ? '.3' : '.85');
    p.setAttribute('fill', 'none');
    if (L.kind !== 'rel') p.setAttribute('marker-end', 'url(#sm-arw-' + L.kind + ')');
    g.appendChild(p);
    L.el = p;
    // 血缘边：叠一条流动虚线（纯 CSS dashoffset 动画；low 档/降级后不生成）
    if (L.kind === 'blood') addFlowTo(L);
  });

  // ── 分组底（按 cat 的软分组：椭圆范围 + 组名；len>=3 才画，避免 1 个技能也围一圈） ──
  const ns = 'http://www.w3.org/2000/svg';
  SM.groupMeta = {};
  cats.forEach((c, i) => {
    const mem = nodes.filter(n => n.cat === c);
    if (!mem.length) return;
    SM.groupMeta[c] = { ci: i, mem: mem };
  });
  (d.candidates || []).length && (SM.groupMeta['候选'] = { ci: -1, mem: nodes.filter(n => n.cand) });

  // ── 节点元素（哑光玻璃球体：AO 环境遮蔽 + 柔光 + 玻璃体 + 折射亮缘 + 双光高光 + 焦散光池/透镜光带） ──
  SM.gNodes.innerHTML = nodes.map(n => {
    if (n.cand) {
      return `<g class="node cand${Anim.on() ? ' breathe' : ''}" data-name="${esc(n.id)}">
        <circle class="halo" r="${(n.r * 3).toFixed(1)}" fill="url(#smh0)"/>
        <circle class="ring" r="${(n.r + 3).toFixed(1)}" fill="none" stroke="#c3aef0" stroke-width="1.2" stroke-dasharray="3 3" opacity=".7"/>
        <circle class="ball" r="${n.r}" fill="none" stroke="#c3aef0" stroke-width="1.3" stroke-dasharray="3 3"/>
        <circle r="${(n.r * .32).toFixed(1)}" fill="#e8dcff" fill-opacity=".9"/>
        <text class="nlabel" text-anchor="middle" y="${(n.r + 12).toFixed(1)}" opacity="0">${esc(n.id.replace('candidate:', ''))}</text>
        <title>候选技能（待孵化）：${esc(n.id.replace('candidate:', ''))}</title></g>`;
    }
    const s = n.skill, starred = S.starred.includes(n.id);
    const cc = SM.catColors[n.ci] || { ring: '#9db8d2' };
    const op = statusOp[s.status] != null ? statusOp[s.status] : .85;
    const r = n.r;
    const hl = `M ${(-.54 * r).toFixed(1)} ${(-.25 * r).toFixed(1)} A ${(.62 * r).toFixed(1)} ${(.62 * r).toFixed(1)} 0 0 1 ${(.54 * r).toFixed(1)} ${(-.25 * r).toFixed(1)}`;
    const hub = s.indeg >= 3;
    return `<g class="node" data-name="${esc(n.id)}">
      <ellipse class="ao" cx="0" cy="${(r * .42).toFixed(1)}" rx="${(r * 1.28).toFixed(1)}" ry="${(r * .86).toFixed(1)}" fill="url(#sm-ao)"/>
      ${hub ? `<ellipse class="caustic" cx="0" cy="${(r * 1.1).toFixed(1)}" rx="${(r * 1.5).toFixed(1)}" ry="${(r * .62).toFixed(1)}" fill="url(#sm-caustic)"/>` : ''}
      <circle class="halo" r="${(r * 2.6).toFixed(1)}" fill="url(#smh${n.ci})"/>
      ${hub ? `<circle class="bloom" r="${(r * 4.6).toFixed(1)}" fill="url(#smh${n.ci})" opacity=".5"/>` : ''}
      <circle class="ring" r="${(r + 2.8).toFixed(1)}" fill="none" stroke="${starred ? '#f3c245' : cc.ring}"
        stroke-width="${starred ? 1.5 : 1.05}" opacity="${starred ? '.8' : '.26'}"/>
      <circle class="ball" r="${r}" fill="url(#smg${n.ci})" opacity="${op}"/>
      <circle class="refr" r="${r}" fill="none" stroke="url(#sm-refr)" stroke-width="${Math.max(1.1, r * .16).toFixed(2)}" opacity="${op}"/>
      <path class="hlw" d="${hl}" fill="none" stroke="url(#sm-hl)" stroke-width="${(r * .34).toFixed(2)}" stroke-linecap="round" opacity=".2"/>
      <path class="hlc" d="${hl}" fill="none" stroke="url(#sm-hl)" stroke-width="${Math.max(.8, r * .1).toFixed(2)}" stroke-linecap="round" opacity=".62"/>
      ${s.dup ? `<circle class="dup" r="${(r + 5.6).toFixed(1)}" fill="none" stroke="#e0b23c" stroke-width="1.1" stroke-dasharray="4 3"/>` : ''}
      ${s.dangling.length ? `<circle class="dang" cx="${(r * .78).toFixed(1)}" cy="${(-r * .78).toFixed(1)}" r="2.7" fill="#f87171" stroke="#0a0e16" stroke-width=".7"/>` : ''}
      <text class="nlabel" text-anchor="middle" y="${(r + 12).toFixed(1)}" opacity="0">${esc(n.id.slice(0, 22))}</text>
      </g>`;
  }).join('');
  // 缓存元素引用 + 入场"光点爆开"所需的 halo/ball 句柄
  $$('.node', SM.gNodes).forEach(g => {
    const n = SM.byId[g.dataset.name];
    if (n) { n.el = g; n.haloEl = $('.halo', g); n.ballEl = $('.ball', g); n.ringEl = $('.ring', g); }
  });

  // ── 图例（改版：说明边的方向语义，避免误读） ──
  const wrap = $('#sm-wrap');
  if (wrap) {
    const lg = document.createElement('div');
    lg.className = 'starmap-legend faint';
    lg.innerHTML =
      '<span class="lgd"><i class="lrel"></i>引用（无向：数据本身不带方向）</span>' +
      '<span class="lgd"><i class="lblood"></i>血缘（有向 · 光点流向=演化/合并方向）</span>' +
      '<span class="lgd"><i class="ldup"></i>同名副本</span>' +
      '<span class="lgd"><i class="lcand"></i>候选（待孵化）</span>' +
      '<span class="lgd"><i class="ldang"></i>悬空引用</span>';
    wrap.appendChild(lg);
  }

  // ── 仿真 ──
  const vb = svg.viewBox.baseVal;
  SM.sim = new ForceSim({
    width: vb.width, height: vb.height,
    charge: -380, linkDistance: 96, collisionPad: 12,
    alphaDecay: 0.018,
    instant: !Anim.on(),
  });
  SM.sim.setGraph(nodes, links);
  // 分组引力：往本类别质心轻拉（软分组；强度随 alpha 退火，收敛时不拉扯）
  SM.sim.addForce(groupForce);
  SM.sim.onTick = function () { renderPositions(); Anim.reportFrame(); };
  SM.sim.onEnd = function () {
    if (SM.autoFitPending) { SM.autoFitPending = false; computeFit(); applyFit(true); }
    else renderPositions();
  };
  SM.autoFitPending = true;
  // 入场：节点错峰 scale 0→1（12ms/个）+ 边从两端向中间生长
  if (Anim.on()) startEntrance();
  else { nodes.forEach(n => { n.s = 1; }); }
  renderPositions();
  bindStarInteractions(svg, d);
  toggleEdgeKind('rel', T.lines.rel); toggleEdgeKind('blood', T.lines.blood); toggleEdgeKind('dup', T.lines.dup);
}

/* 分组引力：每个类别算质心，成员被轻微拉向质心（D3 cluster 同思路） */
function groupForce(ns, a) {
  const meta = SM.groupMeta;
  if (!meta) return;
  const agg = {};
  for (let i = 0; i < ns.length; i++) {
    const n = ns[i], key = n.cand ? '\u0002cand' : n.cat;
    const g = agg[key] || (agg[key] = { x: 0, y: 0, n: 0 });
    g.x += n.x; g.y += n.y; g.n++;
  }
  for (const k in agg) { agg[k].x /= agg[k].n; agg[k].y /= agg[k].n; }
  const str = 0.30 * a;          // 强到足以把同类收成一簇，弱于碰撞力不产生穿透
  for (let i = 0; i < ns.length; i++) {
    const n = ns[i];
    if (n.fixed) continue;
    const g = agg[n.cand ? '\u0002cand' : n.cat];
    if (!g) continue;
    n.vx += (g.x - n.x) * str;
    n.vy += (g.y - n.y) * str;
  }
}

/* ── 入场动画：错峰缩放 + 边生长（寄生在仿真 tick 上，不新增 rAF） ── */
function startEntrance() {
  const nodes = SM.nodes;
  // 螺旋爆散顺序 = 距中心的半径顺序（从内到外绽放）
  const vb = SM.svg.viewBox.baseVal;
  const cx = vb.width / 2, cy = vb.height / 2;
  const order = nodes.map((n, i) => [Math.hypot(n.x - cx, n.y - cy), i]).sort((a, b) => a[0] - b[0]);
  const rank = {};
  order.forEach(([, i], k) => { rank[nodes[i].id] = k; });
  SM.entrance = { t0: performance.now(), step: 12, dur: 460, rank: rank, done: false };
  nodes.forEach(n => { n.s = 0; });
  // 边生长：dash 从两端向中间（用 pathLength 归一，避免逐边算长度）
  SM.links.forEach(L => {
    if (!L.el) return;
    L.el.setAttribute('pathLength', '1');
    L.el.style.strokeDasharray = L.kind === 'dup' ? '5 3.5' : '1';
    L.el.style.strokeDashoffset = L.kind === 'dup' ? '0' : '1';
    L.grow = { t0: performance.now() + 220, dur: 420 };
  });
  // 兜底：仿真若提前结束（拖拽/重热），保证入场一定收尾
  clearTimeout(SM._entT);
  SM._entT = setTimeout(finishEntrance, 2600);
}
function finishEntrance() {
  if (!SM.entrance) return;
  SM.nodes.forEach(n => {
    n.s = 1;
    if (n.haloEl) { n.haloEl.setAttribute('r', (n.r * 2.6).toFixed(1)); n.haloEl.style.opacity = ''; }
  });
  SM.links.forEach(L => {
    if (!L.el) return;
    L.grow = null;
    L.el.style.strokeDasharray = L.kind === 'dup' ? '5 3.5' : '';
    L.el.style.strokeDashoffset = '';
    L.el.removeAttribute('pathLength');
  });
  SM.entrance = null;
  renderPositions();
}

/* ── 每帧：写几何（唯一的热点；避免 DOM 查询，元素引用直接挂在对象上） ── */
function renderPositions() {
  if (!SM.svg || !SM.sim || !SM.gNodes || !SM.nodes.length) return;
  const T = S.starmap;
  const now = performance.now();
  const ent = SM.entrance;
  if (ent) {
    SM.nodes.forEach(n => {
      const d = now - ent.t0 - (ent.rank[n.id] || 0) * ent.step;
      const p = d <= 0 ? 0 : d >= ent.dur ? 1 : Anim.easeOutBack(Math.min(1, d / ent.dur));
      n.s = p;
      // 光点爆开：halo 先放大增亮（光点点燃），再收缩到常态——比单纯 scale 更有"点火"感
      if (n.haloEl) {
        const hp = Math.max(0, Math.min(1, d / (ent.dur + 320)));
        if (hp < 1) {
          n.haloEl.setAttribute('r', (n.r * (2.6 + 1.9 * (1 - hp))).toFixed(1));
          n.haloEl.style.opacity = String(Math.min(1, .7 + .5 * (1 - hp)));
        } else {
          n.haloEl.setAttribute('r', (n.r * 2.6).toFixed(1));
          n.haloEl.style.opacity = '';
        }
      }
    });
    for (let i = 0; i < SM.links.length; i++) {
      const L = SM.links[i];
      if (!L.el || !L.grow) continue;
      const p = (now - L.grow.t0) / L.grow.dur;
      if (p >= 1) { L.el.style.strokeDasharray = L.kind === 'dup' ? '5 3.5' : ''; L.el.style.strokeDashoffset = ''; L.el.removeAttribute('pathLength'); L.grow = null; }
      else if (p > 0) L.el.style.strokeDashoffset = (L.kind === 'dup' ? 0 : (1 - p)).toFixed(3);
    }
  }
  // 图心（供深空板"近亮远暗"）
  let gx = 0, gy = 0;
  for (let i = 0; i < SM.nodes.length; i++) { gx += SM.nodes[i].x; gy += SM.nodes[i].y; }
  const nn = SM.nodes.length || 1; gx /= nn; gy /= nn;
  let radius = 1;
  for (let i = 0; i < SM.nodes.length; i++) radius = Math.max(radius, Math.hypot(SM.nodes[i].x - gx, SM.nodes[i].y - gy));
  // 边（暗底发光：细线低 alpha；"近亮远暗"作为深度线索，与中心距离成反比）
  const ls = SM.links;
  for (let i = 0; i < ls.length; i++) {
    const L = ls[i];
    if (!L.el) continue;
    const d = curvePath(L);
    L.el.setAttribute('d', d);
    if (L.fel) L.fel.setAttribute('d', d);
    if (L.gel) L.gel.setAttribute('d', d);
    const mx = (L.a.x + L.b.x) / 2, my = (L.a.y + L.b.y) / 2;
    const f = 1 - Math.min(1, Math.hypot(mx - gx, my - gy) / radius);
    const base = L.kind === 'rel' ? 0.16 + 0.42 * f : L.kind === 'dup' ? 0.4 + 0.45 * f : 0.5 + 0.45 * f;
    L.el.setAttribute('opacity', base.toFixed(2));
  }
  // 悬停光流：光从本节点沿连线流向邻域（只更新几何，动画由 CSS 负责）
  if (SM._travel) {
    for (let i = 0; i < SM._travel.length; i++) {
      const t = SM._travel[i];
      t.el.setAttribute('d', curvePath(t.L, t.rev));
    }
  }
  // 节点
  const gs = SM.gNodes.children;
  for (let i = 0; i < gs.length; i++) {
    const g = gs[i];
    const n = SM.byId[g.dataset.name];
    if (!n) continue;
    g.setAttribute('transform', 'translate(' + n.x.toFixed(1) + ',' + n.y.toFixed(1) + ')' +
      (n.s !== 1 ? ' scale(' + n.s.toFixed(3) + ')' : ''));
  }
  // 分组底（每帧写，13 个椭圆的开销可忽略；随节点平滑跟随）
  const meta = SM.groupMeta || {};
  const gCont = SM.gGroups;
  if (gCont) {
    if (!SM.gBlobs) SM.gBlobs = {};
    SM.blobBoxes = [];
    for (const key in meta) {
      const m = meta[key];
      if (m.mem.length < 3 && key !== '候选') {
        if (SM.gBlobs[key]) { SM.gBlobs[key].el.style.display = 'none'; SM.gBlobs[key].tx.style.display = 'none'; }
        continue;
      }
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      m.mem.forEach(n => { x0 = Math.min(x0, n.x); y0 = Math.min(y0, n.y); x1 = Math.max(x1, n.x); y1 = Math.max(y1, n.y); });
      const cxm = (x0 + x1) / 2, cym = (y0 + y1) / 2;
      const rx = (x1 - x0) / 2 + 34, ry = (y1 - y0) / 2 + 30;
      let bl = SM.gBlobs[key];
      if (!bl) {
        const ns2 = 'http://www.w3.org/2000/svg';
        const el = document.createElementNS(ns2, 'ellipse');
        el.setAttribute('class', 'gblob');
        // 软分组云：径向渐变（中心微微发亮 → 边缘透明），比纯色椭圆更像"星云范围"
        el.setAttribute('fill', m.ci >= 0 ? 'url(#smh' + m.ci + ')' : 'url(#sm-neb2)');
        el.setAttribute('stroke', 'rgba(150,190,230,.13)');
        el.setAttribute('stroke-dasharray', '3 7');
        el.setAttribute('stroke-width', '1');
        const tx = document.createElementNS(ns2, 'text');
        tx.setAttribute('class', 'glabel');
        tx.setAttribute('text-anchor', 'middle');
        gCont.appendChild(el); gCont.appendChild(tx);
        bl = SM.gBlobs[key] = { el: el, tx: tx };
      }
      bl.el.style.display = ''; bl.tx.style.display = '';
      bl.el.setAttribute('cx', cxm.toFixed(1)); bl.el.setAttribute('cy', cym.toFixed(1));
      bl.el.setAttribute('rx', rx.toFixed(1)); bl.el.setAttribute('ry', ry.toFixed(1));
      // 组名画在云团外侧上方：放里面必然压到成员标签
      const label = key + ' · ' + m.mem.length;
      const ty = cym - ry - 7;
      bl.tx.setAttribute('x', cxm.toFixed(1)); bl.tx.setAttribute('y', ty.toFixed(1));
      bl.tx.textContent = label;
      const lw = textW(label);
      SM.blobBoxes.push([cxm - lw / 2 - 3, ty - 10, cxm + lw / 2 + 3, ty + 4]);
    }
  }
  layoutLabels(false);
  applyEmphasis();
  syncReticle();
  updateRelcap();
}

/* 二次贝塞尔路径（同源多边用 L.curve 分散弧度；rev=true 时反向，供"光从本节点流向邻域"用） */
function curvePath(L, rev) {
  const a = rev ? L.b : L.a, b = rev ? L.a : L.b;
  const ax = a.x, ay = a.y, bx = b.x, by = b.y;
  const dx = bx - ax, dy = by - ay;
  const mx = (ax + bx) / 2, my = (ay + by) / 2;
  const c = L.curve || 0;
  const cx = mx + dy * c * (rev ? -1 : 1), cy = my - dx * c * (rev ? -1 : 1);
  return 'M' + ax.toFixed(1) + ',' + ay.toFixed(1) + 'Q' + cx.toFixed(1) + ',' + cy.toFixed(1) +
    ' ' + bx.toFixed(1) + ',' + by.toFixed(1);
}

/* ── 标签：贪心避让（下方 → 右侧 → 上方），按缩放分档披露 ── */
function labelTier(k, total) {
  if (total <= 40) return 0;                 // 视野小：全标
  if (k >= 1.7) return 0;
  if (k >= 0.95) return 1;
  return 2;
}
function wantLabel(n, tier) {
  if (n.cand) return true;
  const s = n.skill || {};
  if (tier === 0) return true;
  if (tier === 1) return s.indeg >= 1 || S.starred.includes(n.id) || s.evolved_from || s.merged_into;
  return s.indeg >= 2 || S.starred.includes(n.id) || s.evolved_from || s.merged_into;
}
function textW(s) {
  s = String(s || '');
  let w = 8;
  for (let i = 0; i < s.length; i++) w += s.charCodeAt(i) > 255 ? 10.6 : 5.85;
  return w;
}
function layoutLabels(force) {
  const now = performance.now();
  if (!force && now - SM.labelTick < 240) return;
  SM.labelTick = now;
  const k = SM.view.k;
  const tier = labelTier(k, SM.nodes.length);
  const cand = [];
  for (let i = 0; i < SM.nodes.length; i++) {
    const n = SM.nodes[i];
    if (wantLabel(n, tier) || SM.hoverName === n.id || SM.focusName === n.id) cand.push(n);
  }
  cand.sort((a, b) => (b.skill ? b.skill.indeg : 99) - (a.skill ? a.skill.indeg : 99));
  const boxes = [];
  const out = {};
  const vbW = SM.vb ? SM.vb.w : 1000, vbH = SM.vb ? SM.vb.h : 760;
  // 组名占位（先登记，节点标签就不会压到分组标题上）
  if (SM.blobBoxes) SM.blobBoxes.forEach(b => boxes.push(b));
  // 只在"当前可见窗口"内放标签（否则缩放到局部时，远处的标签会被放到视野外成为浮字）
  const vis = {
    x0: -SM.view.x / k + 2, x1: (vbW - SM.view.x) / k - 2,
    y0: -SM.view.y / k + 6, y1: (vbH - SM.view.y) / k - 2,
  };
  // 标签盒：存储的是"基线 y"，盒范围 = [y-9, y+3]（字形高度）
  const box4 = o => [o.x, o.y - 9, o.x + o.w, o.y + 3];
  for (let ci = 0; ci < cand.length; ci++) {
    const n = cand[ci];
    const label = n.cand ? n.id.replace('candidate:', '') : n.id.slice(0, 22);
    const w = Math.min(190, textW(label));
    // 三个候选位（y 为文字基线）：下方居中 / 右侧居中 / 上方居中
    const opts = [
      { x: n.x - w / 2, y: n.y + n.r + 13, anchor: 'middle' },
      { x: n.x + n.r + 6, y: n.y + 4, anchor: 'start' },
      { x: n.x - w / 2, y: n.y - n.r - 4, anchor: 'middle' },
    ];
    opts.forEach(o => { o.w = w; });
    let placed = null;
    for (let oi = 0; oi < opts.length && !placed; oi++) {
      const o = opts[oi];
      if (o.x < vis.x0 || o.x + w > vis.x1 || o.y - 9 < vis.y0 || o.y + 3 > vis.y1) continue;
      const bb = box4(o);
      let hit = false;
      for (let bi = 0; bi < boxes.length; bi++) {
        const b = boxes[bi];
        if (bb[0] < b[2] && bb[2] > b[0] && bb[1] < b[3] && bb[3] > b[1]) { hit = true; break; }
      }
      if (hit) continue;
      // 与任何节点圆（含自己）相撞则换位
      const ccx = o.x + (o.anchor === 'start' ? w / 2 : w / 2), ccy = o.y - 3;
      for (let ni = 0; ni < SM.nodes.length; ni++) {
        const m = SM.nodes[ni];
        if (Math.abs(m.x - ccx) > w / 2 + m.r + 2) continue;
        if (Math.abs(m.y - ccy) > 6 + m.r + 2) continue;
        if (Math.hypot(m.x - ccx, m.y - ccy) < m.r + 6) { hit = true; break; }
      }
      if (!hit) placed = o;
    }
    if (!placed) { out[n.id] = null; continue; }
    boxes.push(box4(placed));
    out[n.id] = { x: placed.x, y: placed.y, anchor: placed.anchor };
  }
  SM.labels = out;
}

/* ── 强调状态（悬停/聚焦/淡化）：只在状态变化时写 DOM，不随 tick 全量刷 ── */
function applyEmphasis() {
  if (!SM.svg || !SM.gNodes || !SM.nodes.length) return;
  const hood = SM.hood, chain = SM.chain;
  const active = SM.hoverName || SM.focusName;
  SM.svg.classList.toggle('emph', !!active);
  // 关联边高亮（CSS 类优先于每帧写的 opacity 属性，无需逐帧处理）
  if (SM._hotE) SM._hotE.forEach(L => { L.el.classList.remove('hot'); if (L.fel) L.fel.classList.remove('hot'); });
  SM._hotE = [];
  const gs = SM.gNodes.children;
  for (let i = 0; i < gs.length; i++) {
    const g = gs[i];
    const name = g.dataset.name;
    const n = SM.byId[name];
    if (!n) continue;
    if (!active) { g.classList.remove('dim', 'hot', 'nb'); }
    else if (name === active) { g.classList.remove('dim', 'nb'); g.classList.add('hot'); }
    else if (hood && hood.has(name)) { g.classList.remove('dim'); g.classList.add('nb'); }
    else if (chain && chain.has(name)) { g.classList.remove('dim', 'hot'); g.classList.add('nb'); }
    else { g.classList.remove('hot', 'nb'); g.classList.add('dim'); }
    // 标签位置 + 显隐
    const lab = g.querySelector('.nlabel');
    if (lab) {
      const p = SM.labels[name];
      const forced = name === active;
      if (p && (forced || !active || hood && hood.has(name) || chain && chain.has(name))) {
        lab.setAttribute('opacity', forced ? '1' : '0.92');
        // 标签是节点 <g> 的子元素：x/y 必须给"相对节点"的局部坐标（世界坐标会叠加两次位移）
        lab.setAttribute('x', (p.x - n.x).toFixed(1));
        lab.setAttribute('y', (p.y - n.y).toFixed(1));
        lab.setAttribute('text-anchor', p.anchor);
      } else {
        lab.setAttribute('opacity', '0');
      }
    }
  }
}

/* 悬停关系清单（浮出在节点旁，说明"这条边是什么关系"）
   e 可选：3D 视图没有 SVG 坐标系，直接用指针位置定位 */
function updateRelcap(e) {
  const box = $('#sm-relcap');
  if (!box) return;
  const name = SM.hoverName || SM.focusName;
  if (!name || !SM.relList || !SM.relList.length) { box.classList.add('hidden'); return; }
  box.classList.remove('hidden');
  box.innerHTML = '<b>' + esc(name) + '</b>' + SM.relList.slice(0, 6).map(r =>
    '<span><i class="rk ' + r.cls + '"></i>' + esc(r.txt) + '</span>').join('') +
    (SM.relList.length > 6 ? '<span class="faint">…共 ' + SM.relList.length + ' 条关系</span>' : '');
  const wrap = $('#sm-wrap');
  if (!wrap) return;
  const w = wrap.clientWidth, hgt = wrap.clientHeight;
  let px, py;
  if (e && e.clientX != null) {
    const wr = wrap.getBoundingClientRect();
    px = e.clientX - wr.left + 18; py = e.clientY - wr.top - 12;
  } else if (SM.svg && SM.byId[name]) {
    const p = worldToScreen(SM.byId[name]);
    px = p.x + 18; py = p.y - 10;
  } else return;
  box.style.left = Math.max(6, Math.min(px, w - 232)) + 'px';
  box.style.top = Math.max(6, Math.min(py, hgt - 124)) + 'px';
}
function worldToScreen(n) {
  if (!n || !SM.svg) return { x: 0, y: 0 };
  const vb = SM.svg.viewBox.baseVal;
  const svg = SM.svg.getBoundingClientRect();
  const wrap = $('#sm-wrap').getBoundingClientRect();
  const sx = vb.width / svg.width, sy = vb.height / svg.height;
  return {
    x: (n.x * SM.view.k + SM.view.x) / sx + (svg.left - wrap.left),
    y: (n.y * SM.view.k + SM.view.y) / sy + (svg.top - wrap.top),
  };
}

/* ── 视图变换 ── */
function applyView() {
  if (SM.gRoot)
    SM.gRoot.setAttribute('transform', 'translate(' + SM.view.x.toFixed(1) + ',' + SM.view.y.toFixed(1) + ') scale(' + SM.view.k.toFixed(3) + ')');
  const hint = $('.starmap-hint');
  if (hint) hint.textContent = '缩放 ' + SM.view.k.toFixed(2) + '×' + (SM.linkCount ? ' · ' + SM.linkCount + ' 边' : '');
  layoutLabels(true);
  applyEmphasis();
  updateRelcap();
}

/* 自动适配：按 bbox 让图占画布 ~76%（T1-5）
   PAD 要把"分组椭圆外扩 + 标签行高"一并算进去，否则四周会被画布边缘裁掉 */
function computeFit() {
  const ns = SM.nodes;
  if (!ns.length || !SM.svg) return;
  const PAD = 34;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  ns.forEach(n => {
    x0 = Math.min(x0, n.x - n.r); y0 = Math.min(y0, n.y - n.r);
    x1 = Math.max(x1, n.x + n.r); y1 = Math.max(y1, n.y + n.r);
  });
  const vb = SM.svg.viewBox.baseVal;
  const w = Math.max(40, x1 - x0) + PAD * 2, h = Math.max(40, y1 - y0) + PAD * 2;
  let k = Math.min(vb.width / w, vb.height / h) * 0.95;
  k = Anim.clamp(k, 0.35, 3.0);
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
  SM.fit = { k: k, x: vb.width / 2 - cx * k, y: vb.height / 2 - cy * k };
}
function applyFit(animate) {
  if (!SM.fit) computeFit();
  if (!SM.fit) return;
  const to = SM.fit;
  if (!animate || !Anim.on()) {
    SM.view = { x: to.x, y: to.y, k: to.k };
    applyView(); renderPositions();
    return;
  }
  tweenView(to.x, to.y, to.k, 520);
}
/* 视图补间（短命 rAF，自终止；不常驻） */
function tweenView(tx, ty, tk, dur) {
  if (SM.follow) cancelAnimationFrame(SM.follow.raf);
  const f = SM.follow = { raf: 0 };
  const x0 = SM.view.x, y0 = SM.view.y, k0 = SM.view.k, t0 = performance.now();
  function step(t) {
    if (SM.follow !== f) return;
    const p = Math.min(1, (t - t0) / dur);
    const e = Anim.easeInOutCubic(p);
    SM.view.x = Anim.lerp(x0, tx, e); SM.view.y = Anim.lerp(y0, ty, e); SM.view.k = Anim.lerp(k0, tk, e);
    applyView();
    if (p < 1) f.raf = requestAnimationFrame(step);
    else { SM.follow = null; SM.view = { x: tx, y: ty, k: tk }; applyView(); }
  }
  f.raf = requestAnimationFrame(step);
}

/* 世界坐标 ↔ 屏幕坐标 */
function svgPoint(evt) {
  const rect = SM.svg.getBoundingClientRect();
  const vb = SM.svg.viewBox.baseVal;
  const sx = (evt.clientX - rect.left) / rect.width * vb.width;
  const sy = (evt.clientY - rect.top) / rect.height * vb.height;
  return { x: (sx - SM.view.x) / SM.view.k, y: (sy - SM.view.y) / SM.view.k };
}

/* 边类型显隐（不改物理布局，只改可见性——避免切换开关时布局跳动） */
function toggleEdgeKind(kind, on) {
  if (!SM.gEdges) return;
  const g = $('[data-ekind="' + kind + '"]', SM.gEdges);
  if (g) g.style.display = on ? '' : 'none';
  SM.linkCount = SM.links.filter(L => S.starmap.lines[L.kind]).length;
  applyView();
}
/* 降级：撤掉流动光点 */
function stripFlow() {
  SM.links.forEach(L => { if (L.fel) { L.fel.remove(); L.fel = null; } });
}
/* 血缘边的流动光点（光点流向 = 语义方向；纯 CSS 动画，无 rAF） */
function addFlowTo(L) {
  if (L.fel || L.kind !== 'blood' || !Anim.full() || !SM.light) return;
  const g = $('[data-ekind="blood"]', SM.gEdges);
  if (!g) return;
  const f = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  f.setAttribute('class', 'eflow' + (L.bkind === 'merged_into' ? ' rev' : ''));
  f.setAttribute('stroke', '#c8b6ff');
  f.setAttribute('fill', 'none');
  f.setAttribute('stroke-width', '2.4');
  f.setAttribute('opacity', '.95');
  g.appendChild(f);
  L.fel = f;
}
/* 动画强度切换时：重估 instant / 流动光点（不必重建整图，保住用户拖出来的布局） */
SM.applyAnimLevel = function () {
  if (!SM.sim) return;
  SM.light = !(typeof Anim !== 'undefined' && Anim.degraded);
  SM.sim.instant = !Anim.on();
  stripFlow();
  SM.links.forEach(addFlowTo);
  renderPositions();
};

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
    SM.userMoved = true;
    applyView();
  }, { passive: false });

  // —— 平移 / 节点拖拽（pointer 事件，兼容旧内核 mouse 事件降级）——
  let drag = null;
  const down = e => {
    const g = e.target.closest ? e.target.closest('.node') : null;
    if (g) {
      const n = SM.byId[g.dataset.name];
      if (n && !n.cand) {
        drag = { node: n, moved: false };
        SM.sim.dragStart(n);
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
      SM.userMoved = true;
      applyView();
    } else if (drag.node) {
      const p = svgPoint(e);
      SM.sim.dragMove(drag.node, p.x, p.y);
      drag.moved = true;
    }
  };
  const up = () => {
    if (drag && drag.node) {
      if (drag.moved) { SM.sim.dragEnd(drag.node); SM.userMoved = true; }
      else drag.node.fixed = false;
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

  // —— 双击空白：回到自适应视图 ——
  svg.addEventListener('dblclick', e => {
    if (e.target.closest && e.target.closest('.node')) return;
    SM.userMoved = false;
    applyFit(true);
  });

  // —— 悬停：涟漪 + 1 跳邻域 + 关系清单 ——
  svg.addEventListener('mouseover', e => {
    const g = e.target.closest ? e.target.closest('.node') : null;
    if (!g) return;
    const name = g.dataset.name;
    if (SM.hoverName === name) return;
    SM.hoverName = name;
    SM.hood = neighborsOf(name);
    SM.chain = g.classList.contains('cand') ? null : bloodChain(name, d);
    SM.relList = relationsOf(name);
    ripple(g);
    travelOn(name);
    layoutLabels(true);
    applyEmphasis();
    updateRelcap();
  });
  svg.addEventListener('mouseout', e => {
    const g = e.target.closest ? e.target.closest('.node') : null;
    if (!g) return;
    SM.hoverName = null; SM.chain = null; SM.hood = null; SM.relList = null;
    travelOff();
    renderPositions();
  });

  // —— 点击：聚焦模式（选中 + 邻域常亮）或浮窗 ——
  svg.addEventListener('click', e => {
    const g = e.target.closest ? e.target.closest('.node') : null;
    const pop = $('#starmap-pop');
    if (!g) {
      if (pop) pop.innerHTML = '';
      if (SM.focusName) { SM.focusName = null; SM.relList = null; travelOff(); renderPositions(); }
      return;
    }
    const name = g.dataset.name;
    SM.focusName = SM.focusName === name ? null : name;
    SM.hood = SM.focusName ? neighborsOf(SM.focusName) : null;
    if (SM.focusName) { SM.relList = relationsOf(SM.focusName); travelOn(SM.focusName); }
    else { SM.relList = null; travelOff(); }
    if (g.classList.contains('cand')) {
      const nm = name.replace('candidate:', '');
      showCandPopover(nm, e.clientX, e.clientY);
      renderPositions();
      return;
    }
    const s = d.skills.find(x => x.name === name);
    if (!s) return;
    showPopover(s.name, s, e.clientX, e.clientY, d);
    renderPositions();
  });

  // —— 换色板后重算渐变（深空板发光更强） ——
  window.addEventListener('resize', () => {
    clearTimeout(SM._rz);
    SM._rz = setTimeout(() => {
      const before = SM.vb ? SM.vb.h : 0;
      syncViewBox();
      if (SM.vb && SM.vb.h !== before) { computeFit(); if (!SM.userMoved) applyFit(false); }
      layoutLabels(true); updateRelcap();
    }, 140);
  });

  // —— 星野视差：指针轻微带动星云/星尘（近层动得多、远层动得少）——
  const pars = $$('.par', svg);
  if (pars.length && Anim.full()) {
    svg.addEventListener('mousemove', e => {
      const r = svg.getBoundingClientRect();
      if (!r.width) return;
      const dx = (e.clientX - r.left) / r.width - .5, dy = (e.clientY - r.top) / r.height - .5;
      pars.forEach((g, i) => {
        const amp = 5 + i * 6;
        g.setAttribute('transform', 'translate(' + (dx * amp).toFixed(1) + ',' + (dy * amp).toFixed(1) + ')');
      });
    });
  }
}

/* 悬停时"光从本节点流向邻域"：临时流动覆盖层（切走/移出即清空） */
function travelOn(name) {
  travelOff();
  if (!Anim.full() || !SM.light || !SM.gTravel) return;
  const inc = SM.links.filter(L => L.a.id === name || L.b.id === name).slice(0, 10);
  SM._travel = inc.map(L => {
    const el = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    el.setAttribute('class', 'htravel');
    el.setAttribute('stroke', '#dcf1ff');
    el.setAttribute('fill', 'none');
    el.setAttribute('stroke-width', '1.7');
    el.setAttribute('opacity', '.9');
    SM.gTravel.appendChild(el);
    return { L: L, el: el, rev: L.a.id !== name };   // 光总从悬停节点出发
  });
}
function travelOff() { if (SM.gTravel) SM.gTravel.innerHTML = ''; SM._travel = null; }

/* 聚焦准星（选中节点外的旋转虚线环；只在聚焦态存在，切换时增删） */
function syncReticle() {
  if (!SM.gNodes) return;
  if (SM._retNode === SM.focusName) return;
  const old = SM.gNodes.querySelector('.reticle');
  if (old && old.parentNode) old.parentNode.removeChild(old);
  SM._retNode = null;
  if (!SM.focusName || !Anim.on()) return;
  const g = SM.gNodes.querySelector('.node[data-name="' + CSS.escape(SM.focusName) + '"]');
  const n = SM.byId[SM.focusName];
  if (!g || !n) return;
  const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
  c.setAttribute('class', 'reticle');
  c.setAttribute('r', (n.r + 9).toFixed(1));
  c.setAttribute('fill', 'none');
  c.setAttribute('stroke', '#9fe8ff');
  c.setAttribute('stroke-width', '1.1');
  c.setAttribute('stroke-dasharray', '3 4');
  g.appendChild(c);
  SM._retNode = SM.focusName;
}

/* 悬停涟漪：节点内插一个扩散圆环，动画结束自删（full 档且未降级才播） */
function ripple(g) {
  if (!Anim.full() || !SM.light) return;
  const c = g.querySelector('.ball');
  if (!c) return;
  const r = parseFloat(c.getAttribute('r')) || 8;
  const ns = 'http://www.w3.org/2000/svg';
  const el = document.createElementNS(ns, 'circle');
  el.setAttribute('class', 'ripple');
  el.setAttribute('r', r.toFixed(1));
  el.setAttribute('fill', 'none');
  el.setAttribute('stroke', 'var(--accent)');
  el.setAttribute('stroke-width', '1.4');
  g.insertBefore(el, g.firstChild);
  setTimeout(() => { if (el.parentNode) el.parentNode.removeChild(el); }, 900);
}

/* 1 跳邻域（含自身） */
function neighborsOf(name) {
  const set = new Set([name]);
  SM.links.forEach(L => {
    if (L.a.id === name) set.add(L.b.id);
    else if (L.b.id === name) set.add(L.a.id);
  });
  return set;
}
/* 关系清单（这条节点与谁、以何种关系相连） */
function relationsOf(name) {
  const out = [];
  const other = id => (SM.byId[id] && SM.byId[id].cand ? id.replace('candidate:', '') + '（候选）' : id);
  SM.links.forEach(L => {
    if (L.a.id !== name && L.b.id !== name) return;
    if (L.kind === 'rel') out.push({ cls: 'rel', txt: '⇄ 引用 ' + other(L.a.id === name ? L.b.id : L.a.id) });
    else if (L.kind === 'dup') out.push({ cls: 'dup', txt: '≡ 同名副本 ' + other(L.a.id === name ? L.b.id : L.a.id) });
    else if (L.bkind === 'evolved_from')
      out.push({ cls: 'blood', txt: (L.a.id === name ? '⇢ 繁殖出 ' + other(L.b.id) : '⇠ 繁殖自 ' + other(L.a.id)) });
    else
      out.push({ cls: 'blood', txt: (L.b.id === name ? '⇒ 合并入 ' + other(L.a.id) : '⇐ 合并自 ' + other(L.b.id)) });
  });
  // 稳定排序：血缘 > 副本 > 引用，同类按名字
  const rank = { blood: 0, dup: 1, rel: 2 };
  out.sort((a, b) => rank[a.cls] - rank[b.cls] || a.txt.localeCompare(b.txt));
  // 引用过多时截断（清单只给"看得懂"的信息量）
  const rels = out.filter(o => o.cls === 'rel');
  const keep = out.filter(o => o.cls !== 'rel');
  return keep.concat(rels.slice(0, Math.max(0, 6 - keep.length)));
}

/* 血缘传递闭包（双向 BFS：evolved_from 父链 + merged_into 子链） */
function bloodChain(name, d) {
  const set = new Set([name]);
  const byName = {};
  d.skills.forEach(s => { byName[s.name] = s; });
  const up = [name];
  while (up.length) {
    const cur = byName[up.pop()];
    if (!cur) continue;
    ['evolved_from', 'merged_into'].forEach(kk => {
      const p = cur[kk];
      if (p && byName[p] && !set.has(p)) { set.add(p); up.push(p); }
    });
  }
  const down = [name];
  while (down.length) {
    const cur = down.pop();
    d.skills.forEach(s => {
      if ((s.evolved_from === cur || s.merged_into === cur) && !set.has(s.name)) { set.add(s.name); down.push(s.name); }
    });
  }
  return set;
}

/* 状态 → 球体不透明度：状态差异要"看得出来"，但不能糊成一片（v0.3.2 是整体 .5 无差别） */
const statusOp = { active: .98, undeclared: .86, dormant: .64, frozen: .42, candidate: .82 };

function showCandPopover(name, cx, cy) {
  const pop = $('#starmap-pop');
  pop.innerHTML = `<div class="popover" style="left:${Math.min(cx, innerWidth - 320)}px; top:${Math.min(cy, innerHeight - 240)}px">
    <h4>${Icons.svg('candidates', 14)} ${esc(name)} <span class="tag">候选 · 待孵化</span></h4>
    <div class="dim" style="font-size:11.5px">位于 skills/.candidates，观察期候选。可在候选孵化台发起孵化（走闸门，调 eco_breed）。</div>
    <div class="btns"><button data-goto="candidates">去候选孵化台</button></div></div>`;
  $$('[data-goto]', pop).forEach(b => b.onclick = () => goto(b.dataset.goto));
}

function showPopover(name, s, cx, cy, d) {
  const pop = $('#starmap-pop');
  const starred = S.starred.includes(name);
  const hood = SM.hood || new Set([name]);
  const nb = SM.links.filter(L => L.a.id === name || L.b.id === name).length;
  pop.innerHTML = `<div class="popover" style="left:${Math.min(cx, innerWidth - 320)}px; top:${Math.min(cy, innerHeight - 300)}px">
    <span class="pstar ${starred ? 'on' : ''}" title="收藏（加入默认骨架）">${starred ? '★' : '☆'}</span>
    <h4>${esc(name)}</h4>
    <div class="fm">
      <span>version</span><span>${esc(s.version)}</span>
      <span>status/fate</span><span>${esc(s.status)} / ${esc(s.fate)}</span>
      <span>domain/env</span><span>${esc(s.domain || '—')} / ${esc(s.environment || '—')}</span>
      <span>分类</span><span>${esc(s.cat)}</span>
      <span>入度</span><span>${s.indeg}（被引）· 视野内 ${nb} 条关系</span>
      <span>血缘</span><span>${esc(s.evolved_from ? '繁殖自 ' + s.evolved_from : s.merged_into ? '已合并入 ' + s.merged_into : '—')}</span>
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
  void hood;
}
function toggleStar(name) {
  const i = S.starred.indexOf(name);
  if (i >= 0) S.starred.splice(i, 1); else S.starred.push(name);
  localStorage.setItem('eco_star', JSON.stringify(S.starred));
  route();
}
/* focusSkill（跨视图跳转定位）—— 平移视图让目标居中并聚焦高亮 */
function focusSkill(name) {
  const svg = $('#starmap-svg');
  if (!svg) { toast(`「${name}」不在星图（试试全图模式或清分类筛选）`, true); return; }
  const sel = '.node[data-name="' + CSS.escape('candidate:' + name) + '"]';
  let g = $(sel, svg);
  if (!g) g = $('.node[data-name="' + CSS.escape(name) + '"]', svg);
  if (!g) { toast(`「${name}」不在当前视野（试试全图模式或清分类筛选）`, true); return; }
  const n = SM.byId[g.dataset.name];
  if (n) {
    const vb = svg.viewBox.baseVal;
    SM.view.x = vb.width / 2 - n.x * SM.view.k;
    SM.view.y = vb.height / 2 - n.y * SM.view.k;
    SM.focusName = g.dataset.name;
    SM.hood = neighborsOf(SM.focusName);
    SM.relList = relationsOf(SM.focusName);
    SM.userMoved = true;
    applyView();
    ripple(g);
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

/* 换色板时重算颜色（三套色板的色域/饱和度不同）
   注意：只有"平铺视图"处于挂载状态时才需要（3D 由 GL3D.recolor 处理），
   否则换肤会因为操作已经不存在的 SVG/仿真而报错。 */
SM.recolor = function () {
  if (!SM.svg || !SM.sim || !SM.gNodes || !SM.d) return;
  const cats = Object.keys(SM.d.cats).sort();
  SM.catColors = catPalette(cats.length);
  SM.nodes.forEach(n => { if (n.ci >= 0 && SM.catColors[n.ci]) n.color = SM.catColors[n.ci].base; });
  const defsEl = SM.svg.querySelector('defs');
  if (!defsEl) return;
  cats.forEach((c, i) => {
    const cc = SM.catColors[i];
    if (!cc) return;
    let g = defsEl.querySelector('#smg' + i);
    if (g) {
      const st = g.querySelectorAll('stop');
      ['hi', 'mid', 'base', 'lo'].forEach((k, n) => { if (st[n]) st[n].setAttribute('stop-color', cc[k]); });
    }
    g = defsEl.querySelector('#smh' + i);
    if (g) {
      const st = g.querySelectorAll('stop');
      if (st[0]) st[0].setAttribute('stop-color', cc.glow);
      if (st[1]) st[1].setAttribute('stop-color', cc.glow);
    }
  });
  // 类别环颜色（只改未收藏节点；收藏的环是金黄标记）
  SM.nodes.forEach((n, k) => {
    if (n.ci < 0) return;
    const g = SM.gNodes.children[k];
    if (!g) return;
    const ring = g.querySelector('.ring');
    if (ring && !S.starred.includes(n.id) && SM.catColors[n.ci]) ring.setAttribute('stroke', SM.catColors[n.ci].ring);
  });
  renderPositions();
};
