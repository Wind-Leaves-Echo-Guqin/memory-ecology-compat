/* ============================================================
   core.js — v0.3 主控：状态/路由/抽屉/检索/色板/字号/引导/启动
   （由 v0.2 app.js 重构拆出；视图逻辑在 views/*.js）
   ============================================================ */
'use strict';

/* ── 状态 ── */
const S = {
  view: 'dashboard',
  palette: localStorage.getItem('eco_palette') || 'a',
  fontStep: +(localStorage.getItem('eco_font_step') || 1),   // 0/1/2/3 → 12/13/14/15
  starred: JSON.parse(localStorage.getItem('eco_star') || '[]'),
  searchMode: 'note',
  animLevel: localStorage.getItem('eco_anim_level') || 'full',   // low | medium | full
  pollInterval: +(localStorage.getItem('eco_poll_interval') || 30), // 秒；0=关
  desktop: false,
  patrolStamp: null,
  focus: null,
  expFilters: { type: '', status: '', sort: 'created', page: 1 },
  memFilters: { type: '', status: '', sort: 'last' },
  timelineFilter: '',
  timelineMode: 'chart',
  timelineChart: 'swim',
  healthTab: 'report',
  starmap: { form: localStorage.getItem('eco_starmap_form') || '3d',
    mode: 'skeleton', list: false, cat: '', lines: { rel: true, blood: true, dup: true } },
  cache: {},
};

/* ── 基础工具 ── */
const $ = (sel, el) => (el || document).querySelector(sel);
const $$ = (sel, el) => Array.from((el || document).querySelectorAll(sel));
const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function cached(key, ttl, fn) {
  const hit = S.cache[key];
  if (hit && Date.now() - hit[0] < ttl) return hit[1];
  const data = await fn();
  S.cache[key] = [Date.now(), data];
  return data;
}

function toast(msg, ok) {
  const t = $('#toast');
  t.textContent = msg;
  t.className = ok ? 'ok' : '';
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.add('hidden'), 3200);
}

/* ── 极简 Markdown 渲染 ── */
function md(src) {
  const lines = String(src || '').replace(/\r/g, '').split('\n');
  let out = '', i = 0, para = [];
  const flush = () => {
    if (para.length) { out += `<p>${inline(para.join(' '))}</p>`; para = []; }
  };
  const inline = s => esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1');
  while (i < lines.length) {
    const L = lines[i];
    if (/^\|/.test(L)) {
      flush();
      const rows = [];
      while (i < lines.length && /^\|/.test(lines[i])) rows.push(lines[i++]);
      if (rows.length >= 2 && /^[\s|:-]+$/.test(rows[1])) rows.splice(1, 1);
      let tbl = '<table>';
      rows.forEach((r, k) => {
        const cells = r.replace(/^\||\|$/g, '').split('|').map(c => inline(c.trim()));
        tbl += '<tr>' + cells.map(c => (k === 0 ? `<th>${c}</th>` : `<td>${c}</td>`)).join('') + '</tr>';
      });
      out += tbl + '</table>';
      continue;
    }
    const hm = L.match(/^(#{1,4})\s+(.*)/);
    if (hm) { flush(); out += `<h${hm[1].length}>${inline(hm[2])}</h${hm[1].length}>`; ++i; continue; }
    if (/^(-{3,}|\*{3,})\s*$/.test(L)) { flush(); out += '<hr>'; ++i; continue; }
    if (/^[-*]\s+/.test(L)) {
      flush();
      let items = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i]))
        items.push('<li>' + inline(lines[i++].replace(/^[-*]\s+/, '')) + '</li>');
      out += `<ul>${items.join('')}</ul>`;
      continue;
    }
    if (/^>/.test(L)) { flush(); out += `<p class="dim">${inline(L.replace(/^>\s?/, ''))}</p>`; ++i; continue; }
    if (!L.trim()) { flush(); ++i; continue; }
    para.push(L); ++i;
  }
  flush();
  return out;
}

/* ── 抽屉 ── */
function openDrawer(html) {
  $('#drawer-body').innerHTML = html;
  $('#drawer').classList.remove('hidden');
  $('#drawer-mask').classList.remove('hidden');
}
function closeDrawer() {
  $('#drawer').classList.add('hidden');
  $('#drawer-mask').classList.add('hidden');
}

/* ── 跳转 ── */
function goto(view, focus) {
  S.focus = focus || null;
  location.hash = '#/' + view;
  if (('#/' + view) === location.hash) route();  // 同视图重复跳转也刷新
}
async function gotoDetail(slug) {
  goto('memories', { kind: 'detail', slug });
  if (S.view === 'memories') await openDetailDrawer(slug);
}
async function openDetailDrawer(slug) {
  try {
    const d = (await EcoApi.get(`/api/memories/detail?slug=${encodeURIComponent(slug)}`)).detail;
    if (!d) return toast('未找到该条目');
    const fm = d.fm || {};
    const rows = [['type', fm.type], ['status', fm.status], ['occurrences', fm.occurrences],
      ['session_count', fm.session_count], ['first_seen', fm.first_seen], ['last_seen', fm.last_seen],
      ['valid_time（事件钟）', fm.valid_time], ['transaction_time（事务钟）', fm.transaction_time],
      ['last_verified', fm.last_verified], ['origin', fm.origin],
      ['superseded_by', fm.superseded_by]];
    openDrawer(`
      <div class="vh">L2 详情 <small>${esc(slug)}</small></div>
      <div class="fmtable">${rows.map(([k, v]) => `<span>${esc(k)}</span><span>${esc(v || '—')}</span>`).join('')}</div>
      ${fm.superseded_by ? `<p>已被替换 → <a data-goto-detail="${esc(fm.superseded_by)}">${esc(fm.superseded_by)}</a></p>` : ''}
      <div class="md">${esc(d.body || '(空)')}</div>`);
  } catch (e) { toast('读取失败：' + e.message); }
}
async function gotoExp(id) {
  goto('experiences', { kind: 'exp', id });
  if (S.view === 'experiences') await openExpDrawer(id);
}
async function openExpDrawer(id) {
  try {
    const it = (await EcoApi.get(`/api/experiences/detail?id=${encodeURIComponent(id)}`)).item;
    const tc = { error: 'var(--exp-error)', pattern: 'var(--exp-pattern)', negative: 'var(--exp-negative)',
      success: 'var(--exp-success)', link: 'var(--exp-link)' };
    const zh = localStorage.getItem('exp_zh') !== '0';
    const T = { error: '错误案例', pattern: '可复用模式', negative: '无效做法', success: '成功经验', link: '外部关联' };
    openDrawer(`
      <div class="vh">${esc(it.title)}</div>
      <div class="row" style="margin:8px 0 14px">
        <span class="etyp" style="background:${tc[it.type] || 'var(--ink3)'}">${zh ? (T[it.type] || it.type) : it.type}</span>
        <span class="est ${it.status === 'verified' ? 'gold' : ''}">${zh ? (it.status === 'verified' ? '已验证' : '草稿') : it.status}${it.status !== 'verified' ? ' · 命中≥2 自动转正' : ''}</span>
        ${it.distilled_to ? `<a data-goto-skill="${esc(it.distilled_to)}">已蒸馏为 → ${esc(it.distilled_to)}</a>` : ''}
      </div>
      <div class="fmtable">
        <span>id</span><span class="mono">${esc(it.id)}</span>
        <span>创建</span><span>${esc(it.created)}</span>
        <span>最近命中</span><span>${esc(it.last_hit || '—')}</span>
        <span>来源（provenance）</span><span class="mono">${esc(it.provenance || '—')}</span>
        <span>关联</span><span>${it.distilled_to
          ? `本条经验已沉淀为技能「${esc(it.distilled_to)}」（点击跳星图）`
          : it.status === 'verified' ? '暂未蒸馏成技能（命中再涨会被蒸馏门看中）' : '草稿期：命中≥2 自动转已验证'}</span>
      </div>
      ${['symptom', 'cause', 'action', 'evidence', 'boundary'].map(k => it.sections[k] ? `
        <div class="sect ${k}"><div class="sect-h">${({symptom:'症状',cause:'原因',action:'正确做法',evidence:'证据',boundary:'边界'}[k] || k.toUpperCase())}</div>
        <div class="sect-b ${k === 'evidence' ? 'mono' : ''}">${esc(it.sections[k])}</div></div>` : '').join('')}`);
  } catch (e) { toast('读取失败：' + e.message); }
}
function gotoSkill(name) {
  goto('starmap', { kind: 'skill', name });
  if (S.view === 'starmap') focusSkill(name);
}

/* ── 路由（RENDER 注册表在 polyfill.js 声明，视图模块向其挂载） ── */
function prefersReduced() {
  try { return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches; }
  catch (e) { return false; }
}
let _routeSeq = 0;
async function route() {
  const view = (location.hash.replace(/^#\//, '') || 'dashboard');
  const seq = ++_routeSeq;
  S.view = view;
  $$('#nav a').forEach(a => a.classList.toggle('on', a.dataset.view === view));
  const v = $('#view');
  // 切走时停掉星图仿真循环（否则每次进入星图都会叠加一个 rAF 循环）
  try { if (typeof SM === 'object' && SM && SM.sim) SM.sim.stop(); } catch (e) {}
  // 血缘谱系的光点流动协调器（若在用）也要停
  try { if (typeof Lin !== 'undefined' && Lin && Lin.stop) Lin.stop(); } catch (e) {}
  // 3D 星空循环必须停（否则切走视图后 WebGL 还在后台打帧）
  try { if (typeof GL3D !== 'undefined' && GL3D && GL3D.stop) GL3D.stop(); } catch (e) {}
  const animate = S.animLevel !== 'low' && !prefersReduced();
  // 退出：旧内容淡出上移（内容先上屏，动效只作过渡，可被后续导航打断）
  if (animate) {
    v.classList.add('view-out');
    await sleep(120);
    if (seq !== _routeSeq) { v.classList.remove('view-out'); return; }   // 已被新导航接管
    v.classList.remove('view-out');
  }
  v.classList.remove('view-anim');
  v.innerHTML = '<div class="loading">正在读取生态快照…</div>';
  try {
    await (RENDER[view] || RENDER.dashboard)(v);
    if (seq !== _routeSeq) return;
    if (animate) {
      // 双帧提交：确保"无动画类"状态先落屏，再加动画类，消除整块重绘闪烁
      await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    }
    v.classList.add('view-anim');
    animateMetrics(v, view);
  } catch (e) {
    if (seq !== _routeSeq) return;
    v.innerHTML = `<div class="empty">读取失败：${esc(e.message)}<br>fail-open：检索/接口失败不影响其余视图，可点右上角 ⟳ 重试</div>`;
  }
  if (S.focus) {
    const f = S.focus; S.focus = null;
    await sleep(150);
    if (seq !== _routeSeq) return;
    if (f.kind === 'detail') await openDetailDrawer(f.slug);
    else if (f.kind === 'exp') await openExpDrawer(f.id);
    else if (f.kind === 'skill') focusSkill(f.name);
  }
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
window.addEventListener('hashchange', route);

/* ── 检索（三模式 + 复制 + 历史 + 命中关键词高亮） ── */
const SEARCH_HIST_MAX = 20;
/* 命中高亮：先转义再包 <mark>（顺序不能反——否则会把自己插的标签再转义一次） */
function hl(text, terms) {
  let out = esc(String(text == null ? '' : text));
  const list = (terms || []).filter(t => t && String(t).trim().length >= 2)
    .map(t => String(t).trim()).sort((a, b) => b.length - a.length).slice(0, 8);
  list.forEach(t => {
    const re = new RegExp('(' + t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
    out = out.replace(re, '<mark class="hl">$1</mark>');
  });
  return out;
}
async function doSearch(qArg) {
  const inp = $('#search-input');
  const q = (qArg != null ? String(qArg) : inp.value).trim();
  if (!q) return;
  if (qArg != null) inp.value = q;
  SearchHist.push(q);
  const panel = $('#search-panel');
  panel.classList.remove('hidden');
  panel.innerHTML = '<div class="faint" style="padding:8px">检索中…（subprocess 调用只读 CLI）</div>';
  try {
    const r = await EcoApi.get(`/api/search?kind=${S.searchMode}&q=${encodeURIComponent(q)}`);
    if (!r.ok) throw new Error(r.error || '失败');
    let html = '';
    const resultText = JSON.stringify(r, null, 2);
    html += '<div class="sr-copy"><a id="search-copy">复制结果</a></div>';
    // 高亮词：整句 + 分词（≥2 字） + 报错模式下的异常类名
    const terms = [q].concat(q.split(/[\s,;，；、()（）\[\]{}<>"'`]+/));
    if (S.searchMode === 'error') terms.push(...(r.exc || []));
    if (S.searchMode === 'error')
      html += `<div class="sr-head">根因分层 · 异常类：${esc((r.exc || []).join(', ') || '—')}</div>`;
    if (r.hits && r.hits.length) {
      r.hits.forEach((hh, i) => {
        const id = hh.id || '';
        html += `<div class="sr-hit" style="animation-delay:${Math.min(i * 26, 320)}ms" ${id.startsWith('exp-') ? `data-exp="${esc(id)}"` : id ? `data-skill="${esc(hh.name || id)}"` : ''}>
          <div class="sr-line">
            ${hh.type ? `<span class="tag">${esc(hh.type)}</span>` : ''}
            ${hh.status ? `<span class="tag">${esc(hh.status)}</span>` : ''}
            <span class="sr-t">${hl(hh.trigger || hh.name || hh.id, terms)}</span>
            ${hh.indeg != null ? `<span class="faint">被引${hh.indeg}</span>` : ''}
          </div>
          ${hh.evidence ? `<div class="sr-d mono">${hl(hh.evidence.slice(0, 140), terms)}</div>` : ''}
          ${hh.desc ? `<div class="sr-d">${hl(hh.desc.slice(0, 140), terms)}</div>` : ''}
          ${hh.sections ? `<div class="sr-d faint">${hl(hh.sections.slice(0, 120), terms)}</div>` : ''}
        </div>`;
      });
    } else if (r.raw) {
      html += `<pre class="mono" style="white-space:pre-wrap;font-size:11.5px;color:var(--ink2)">${esc(r.raw.slice(0, 2000))}</pre>`;
    } else {
      html += Icons.empty('search', '没有命中', '试试更短的关键词，或切换检索模式（经验 / 报错 / 生态）。');
    }
    panel.innerHTML = html;
    const cp = $('#search-copy');
    if (cp) cp.onclick = () => ecoCopy(resultText, '检索结果已复制');
    $$('[data-exp]', panel).forEach(el => el.onclick = () => { closeSearch(); gotoExp(el.dataset.exp); });
    $$('[data-skill]', panel).forEach(el => el.onclick = () => { closeSearch(); gotoSkill(el.dataset.skill); });
  } catch (e) {
    panel.innerHTML = `<div class="alarm" style="margin:0">检索失败（fail-open，不影响其他功能）：${esc(e.message)}</div>`;
  }
}
function closeSearch() { $('#search-panel').classList.add('hidden'); }

/* ── 搜索历史（T7/A3）：localStorage 持久化，最多 20 条，最新在前 ── */
const SearchHist = {
  key: 'eco_search_hist',
  max: SEARCH_HIST_MAX,
  list() {
    try { return JSON.parse(localStorage.getItem(this.key) || '[]'); } catch (e) { return []; }
  },
  push(q) {
    q = String(q || '').trim();
    if (!q) return;
    const ls = this.list().filter(x => x !== q);
    ls.unshift(q);
    try { localStorage.setItem(this.key, JSON.stringify(ls.slice(0, this.max))); } catch (e) {}
  },
  clear() { try { localStorage.removeItem(this.key); } catch (e) {} },
  /* 空焦点时展示最近 5 条；有输入则不打扰 */
  show() {
    const ls = this.list().slice(0, 5);
    if (!ls.length) return;
    const panel = $('#search-panel');
    panel.innerHTML = '<div class="sr-head">最近检索 <a id="hist-clear" style="float:right">清空</a></div>' +
      ls.map((q, i) => `<div class="sr-hit" data-hist="${i}"><span class="sr-t">${esc(q)}</span></div>`).join('');
    panel.classList.remove('hidden');
    $$('[data-hist]', panel).forEach(el => el.onclick = () => { doSearch(ls[+el.dataset.hist]); });
    const c = $('#hist-clear');
    if (c) c.onclick = (e) => { e.stopPropagation(); this.clear(); closeSearch(); toast('搜索历史已清空', true); };
  },
  /* ↑ 回溯：把上一条历史填进输入框（不自动执行，便于二次编辑） */
  recall(dir) {
    const ls = this.list();
    if (!ls.length) return false;
    this._i = this._i == null ? -1 : this._i;
    this._i += dir;
    if (this._i < 0) this._i = ls.length - 1;
    if (this._i >= ls.length) this._i = 0;
    const inp = $('#search-input');
    inp.value = ls[this._i];
    return true;
  },
};

/* ── 一键导出（T7/B1）：json 下载 / md 与 text 走剪贴板 ── */
function exportView(format) {
  const rows = collectViewData();
  if (!rows.length) { toast('当前视图暂无可导出的数据', false); return; }
  const stamp = new Date().toLocaleString('sv-SE').slice(0, 16).replace(/[ :]/g, '-');
  const name = VIEW_TITLE[S.view] || S.view;
  if (format === 'json') {
    const payload = { view: S.view, view_name: name, exported: new Date().toISOString(), data: rows };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `eco-${S.view}-${stamp}.json`;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    toast('已导出 JSON 文件', true);
    return;
  }
  if (format === 'md') {
    let mdText = `# 记忆生态 · ${name}\n\n导出时间：${new Date().toLocaleString()}　${($('#sb-snapshot') ? $('#sb-snapshot').textContent : '数据截至 —').replace(/^数据截至\s*/, '数据截至 ')}\n\n`;
    mdText += '| 字段 | 值 |\n|---|---|\n' + rows.map(r => `| ${r.k} | ${String(r.v).replace(/\|/g, '\\|')} |`).join('\n');
    ecoCopy(mdText, '已复制 Markdown 到剪贴板');
    return;
  }
  const text = `记忆生态 · ${name}（${new Date().toLocaleString()}）\n` +
    rows.map(r => `${r.k}：${r.v}`).join('\n');
  ecoCopy(text, '已复制纯文本到剪贴板');
}

/* 采集当前视图可见的关键数据（卡片标题 + 指标/表格行），供导出使用 */
function collectViewData() {
  const out = [];
  const v = $('#view');
  if (!v) return out;
  const head = $('.vh', v);
  if (head) out.push({ k: '视图', v: head.textContent.trim().split('\n')[0] });
  $$('.card', v).forEach(card => {
    const h = $('h3', card);
    const title = h ? h.textContent.trim().split('\n')[0] : '卡片';
    $$('.chip, .ring-info, .water .legend > *', card).forEach(el => {
      const t = el.textContent.replace(/\s+/g, ' ').trim();
      if (t) out.push({ k: title + ' · ' + t.split(' ')[0], v: t });
    });
    $$('tbody tr', card).forEach((tr, i) => {
      if (i > 60) return;
      const cells = $$('td', tr).map(td => td.textContent.replace(/\s+/g, ' ').trim());
      if (cells.length) out.push({ k: title + ' #' + (i + 1), v: cells.join(' | ') });
    });
  });
  return out;
}

/* ── 诊断包（T7/B3）：一键复制可读诊断段（不含敏感路径外内容） ── */
async function copyDiagnostic() {
  const lines = [];
  const meta = await EcoApi.get('/api/meta').catch(() => null);
  const ov = await EcoApi.get('/api/overview').catch(() => null);
  lines.push('记忆生态 · 观测舱诊断包');
  lines.push('生成时间：' + new Date().toLocaleString());
  lines.push('界面版本：' + (document.querySelector('.brand-sub') ? document.querySelector('.brand-sub').textContent : '—') +
    ' · 运行环境：' + (S.desktop ? '桌面窗口(pywebview)' : '浏览器'));
  if (meta) {
    lines.push('生态版本：' + meta.version + ' · 评分模型：' + meta.score_model);
    lines.push('数据根：' + meta.root + '（存在=' + meta.root_exists + '）');
    lines.push('脚本目录：' + (meta.scripts_dir || '—'));
    lines.push('检索 CLI：' + (meta.search_cli_ready ? '就绪' : '未找到'));
    lines.push('数据快照：' + meta.snapshot + ' · 单写入方今日运行：' + (meta.writer_today_ran ? '是' : '否'));
    const locks = Object.keys(meta.locks || {});
    lines.push('锁文件：' + (locks.length ? locks.map(k => k + '(' + (meta.locks[k].age_hours != null ? meta.locks[k].age_hours.toFixed(1) + 'h' : '?') + ')').join(', ') : '无'));
  }
  if (ov) {
    const wm = ov.watermark || {};
    lines.push('记忆水位：' + wm.chars + ' / ' + wm.quota + (wm.chars > wm.quota ? '（越线）' : '（正常）'));
    const cron = ov.cron || {};
    lines.push('cron：运行 ' + (cron.total != null ? cron.total : '?') + ' · 近7天失败 ' + ((cron.recent_fails || []).length));
    (cron.recent_fails || []).slice(0, 3).forEach(f => lines.push('  ⚠ ' + f.job + ' ×' + f.n));
  }
  try {
    const log = await EcoApi.get('/api/action_log');
    (log.entries || log.log || []).slice(0, 3).forEach(e =>
      lines.push('动作：' + (e.ts || e.time || '') + ' ' + (e.action || '') + ' ' + (e.ok === false ? '失败' : '成功')));
  } catch (e) { /* fail-open */ }
  const bad = (Bell.items || []).filter(i => i.sev === 'bad');
  lines.push('当前告警：' + (Bell.items || []).length + ' 条（其中严重 ' + bad.length + ' 条）');
  (Bell.items || []).slice(0, 5).forEach(i => lines.push('  • ' + i.text));
  ecoCopy(lines.join('\n'), '诊断信息已复制（可粘贴给维护者）');
}

/* ── 数字动态度量（T11）：旧值→新值计数递增，600ms easeOutCubic ── */
function animateValue(el, from, to, duration, format) {
  if (!el) return;
  const fmt = format || (v => String(Math.round(v)));
  if (S.animLevel === 'low' || prefersReduced() || !isFinite(from) || !isFinite(to) || from === to) {
    el.textContent = fmt(to);
    return;
  }
  const t0 = performance.now();
  const dur = duration || 600;
  function tick(t) {
    const p = Math.min(1, (t - t0) / dur);
    const e = 1 - Math.pow(1 - p, 3);
    el.textContent = fmt(from + (to - from) * e);
    if (p < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

/* 视图渲染后扫描数值位（评分环 / 计数徽章），与上次同位置比较后做递增动画 */
const _metricCache = {};
function animateMetrics(root, viewKey) {
  if (S.animLevel === 'low' || prefersReduced()) return;
  $$('.ring-info b, .chip b', root).forEach((el, i) => {
    const raw = el.textContent.trim();
    if (!/^-?\d+(\.\d+)?$/.test(raw)) return;      // 含单位（如 10/20）不做计数动画
    const key = viewKey + '#' + i;
    const to = parseFloat(raw);
    const prev = _metricCache[key];
    _metricCache[key] = to;
    if (prev == null || prev === to) return;        // 首次渲染直接显示，不倒数
    animateValue(el, prev, to, 600);
  });
}

/* ── 自动轮询（T13）：只在浏览器端；有变化才重渲染；无变化不闪 ── */
let _pollTimer = 0;
function startPoll() {
  stopPoll();
  const sec = S.pollInterval;
  if (!sec || S.desktop) return;          // 桌面端保持手动刷新（省资源）
  _pollTimer = setInterval(pollOnce, Math.max(5, sec) * 1000);
}
function stopPoll() { if (_pollTimer) clearInterval(_pollTimer); _pollTimer = 0; }
function setPollInterval(sec) {
  S.pollInterval = sec;
  localStorage.setItem('eco_poll_interval', String(sec));
  startPoll();
  toast(sec ? ('自动轮询：每 ' + sec + ' 秒') : '自动轮询已关闭', true);
}
/* 浮层打开时不重渲染（避免打断阅读/操作），只更新状态栏与铃铛 */
function uiBusy() {
  const ids = ['drawer', 'gate-modal', 'kbd-mask', 'cmd-mask', 'bell-panel'];
  return ids.some(id => { const el = $('#' + id); return el && !el.classList.contains('hidden'); });
}
async function pollOnce() {
  try {
    const d = await EcoApi.get('/api/overview');
    const stamp = String(d.snapshot || (d.watermark && d.watermark.chars) || '');
    if (!stamp || stamp === String(S.patrolStamp)) return;   // 无变化：不动 DOM（不闪）
    S.patrolStamp = stamp;
    const dot = $('#patrol-dot');
    if (dot) { dot.classList.add('live'); setTimeout(() => dot.classList.remove('live'), 2600); }
    S.cache = {};                                            // 数据变了才失效缓存
    Bell.refresh();                                           // 后台也推送告警/通知
    if (!uiBusy() && !Cruise.active) { loadStatus(); route(); }
    else loadStatus();
  } catch (e) { /* fail-open：轮询失败不影响使用 */ }
}

/* ── 状态栏 ── */
async function loadStatus() {
  try {
    const m = await EcoApi.get('/api/meta');
    $('#sb-root').textContent = '数据根 ' + m.root;
    $('#sb-root').title = m.root + (m.scripts_dir ? ' · 检索CLI ' + m.scripts_dir : '');
    $('#sb-version').textContent = `生态 ${m.version} · 评分模型 ${m.score_model}` +
      (m.search_cli_ready ? '' : ' · 检索CLI未找到');
    const lockKeys = Object.keys(m.locks || {});
    const fresh = lockKeys.filter(k => m.locks[k].age_hours != null && m.locks[k].age_hours < 2);
    $('#sb-writer').innerHTML = `单写入方：Hermes cron <span class="dot ${m.writer_today_ran ? 'ok' : 'off'}"></span>今日${m.writer_today_ran ? '已运行' : '未运行'}` +
      (fresh.length ? ' · <span title="锁文件 mtime 在 2 小时内">🔒 写入方活动中</span>'
        : lockKeys.length ? ` · <span class="faint" title="锁文件为陈旧残留（mtime ${esc(lockKeys.map(k => m.locks[k].mtime).join(', '))}）">锁文件存在（陈旧）</span>` : '');
    S.patrolStamp = m.snapshot;
    $('#sb-snapshot').textContent = '数据截至 ' + m.snapshot.replace('T', ' ');
  } catch (e) { /* fail-open */ }
}

/* ── 色板（三套精修：雾白 / 青瓷 / 深空大屏） ── */
const VIEW_TITLE = {
  dashboard: '驾驶舱', memories: '记忆库', experiences: '经验笔记本', starmap: '技能星图',
  lineage: '血缘谱系', candidates: '候选孵化台', timeline: '时间线', health: '体检评测',
  actions: '动作日志', firstaid: '急救模式',
};
const PALETTES = [
  ['a', '雾白', '#0e7490'], ['c', '青瓷', '#39786a'], ['f', '深空大屏', '#22d3ee'],
];
const PALETTE_ALIAS = { b: 'a', d: 'a', e: 'a' };   // v0.3.2 收敛：旧色板并入雾白
function setPalette(k) {
  S.palette = k;
  localStorage.setItem('eco_palette', k);
  initPalette();
}
function initPalette() {
  if (PALETTE_ALIAS[S.palette]) S.palette = PALETTE_ALIAS[S.palette];
  if (!PALETTES.some(p => p[0] === S.palette)) S.palette = 'a';
  document.documentElement.dataset.palette = S.palette;
  localStorage.setItem('eco_palette', S.palette);
  const dots = $('#palette-dots');
  if (dots) {
    dots.innerHTML = PALETTES.map(([k, n, c]) =>
      `<i data-p="${k}" title="${n}" style="background:${c}" class="${S.palette === k ? 'on' : ''}"></i>`).join('');
    $$('#palette-dots i').forEach(i => i.onclick = () => setPalette(i.dataset.p));
  }
  if (typeof Particles !== 'undefined') Particles.sync();   // 深色板才开粒子层
  if (typeof SM === 'object' && SM && SM.recolor) SM.recolor();  // 平铺星图按色板重算色阶
  if (typeof GL3D === 'object' && GL3D && GL3D.recolor && SM && SM.catColors) GL3D.recolor(SM.catColors);
}

/* ── 动画强度（T12）：low 仅淡入淡出 / medium 过渡+度量+图表 / full 全部 ── */
function setAnimLevel(lv, silent) {
  if (['low', 'medium', 'full'].indexOf(lv) < 0) lv = 'full';
  S.animLevel = lv;
  localStorage.setItem('eco_anim_level', lv);
  document.documentElement.dataset.anim = lv;
  $$('#anim-ctl button').forEach(b => b.classList.toggle('on', b.dataset.lv === lv));
  if (typeof Particles !== 'undefined') Particles.sync();
  // 星图：切换档位后就地重估（保留用户拖出来的布局，不重建整图）
  if (typeof SM === 'object' && SM && SM.applyAnimLevel) SM.applyAnimLevel();
  if (typeof GL3D === 'object' && GL3D && GL3D.ready) {
    if (Anim.on() && !Anim.degraded) GL3D.start(); else { GL3D.stop(); GL3D.rotate = false; GL3D.renderOnce(); }
  }
  if (silent) return;
  const zh = { low: '轻度（仅淡入淡出）', medium: '中度（过渡+度量+图表）', full: '满载（全部动效）' };
  toast('动画强度：' + zh[lv], true);
}

/* ── 内联 SVG 图标注入（T6）：把 [data-ico] 占位换成统一样式的矢量图标 ── */
function initIcons() {
  if (typeof Icons === 'undefined') return;
  $$('[data-ico]').forEach(el => {
    const name = el.dataset.ico;
    const svg = Icons.svg(name, el.dataset.icoSize ? +el.dataset.icoSize : 16);
    if (svg) el.innerHTML = svg;
    el.removeAttribute('data-ico');
  });
}

/* ── 全局字号（12.5/13.5/14.5/15.5 四档，默认 13.5 兼顾屏显密度与可读性） ── */
const FONT_SIZES = [12.5, 13.5, 14.5, 15.5];
function applyFont() {
  document.body.style.fontSize = FONT_SIZES[S.fontStep] + 'px';
  localStorage.setItem('eco_font_step', String(S.fontStep));
}

/* ── 新手引导（五步遮罩，可跳过可重看） ── */
const GUIDE_STEPS = [
  { title: '第 1 步 · 看健康', body: '驾驶舱 10 秒扫完：体检评分环、记忆水位条、四道门账本与运行证据。<br>红色告警条 = 需要你处理的事（如 cron 失败、记忆越线）。' },
  { title: '第 2 步 · 处理告警', body: '顶栏 🔔 铃铛聚合全部告警，每条可复制、可跳转处理。<br>记忆越线时驾驶舱会出现「立即挤出」按钮（有红色警告与确认闸门）。' },
  { title: '第 3 步 · 执行动作', body: '写操作（挤出/整合/采纳/孵化/体检…）都走确认闸门：<br>弹窗说明影响与回滚 → 高风险需勾选「我已知晓风险」→ 可勾「不再提醒」。<br>每次执行都记录在「动作日志」视图。' },
  { title: '第 4 步 · 快捷键一览', body: '按 <b>g</b> 再按视图首字母跳转：<b>g d</b> 驾驶舱 · <b>g m</b> 记忆库 · <b>g s</b> 技能星图 …<br>' +
      '按 <b>?</b> 随时唤出完整快捷键表；<b>/</b> 聚焦搜索框；<b>r</b> 刷新；<b>Esc</b> 关闭浮层。' },
  { title: '第 5 步 · 更多便利功能', body: '<b>Ctrl+K</b> 命令面板：输入"急/候选/导出"即可跳转或执行。<br>' +
      '状态栏可切换<b>动画强度</b>（轻/中/满载）与<b>色板</b>；顶栏可<b>导出当前视图</b>（JSON/Markdown/文本）。<br>' +
      '浏览器端还支持<b>桌面通知</b>（后台时推送 cron 失败/水位越线）。' },
];
function showGuide(step) {
  step = step || 0;
  if (step >= GUIDE_STEPS.length) {
    $('#guide-mask').classList.add('hidden');
    localStorage.setItem('eco_guide_done', '1');
    return;
  }
  const g = GUIDE_STEPS[step];
  const mask = $('#guide-mask');
  mask.innerHTML = `<div class="guide-box">
    <h3>${esc(g.title)}</h3>
    <div class="guide-body">${g.body}</div>
    <div class="row" style="justify-content:flex-end;margin-top:14px">
      <button class="ghost-btn" id="guide-skip">跳过引导</button>
      <button class="gate-go" id="guide-next">${step === GUIDE_STEPS.length - 1 ? '完成' : '下一步'}</button>
    </div></div>`;
  mask.classList.remove('hidden');
  $('#guide-skip').onclick = () => { mask.classList.add('hidden'); localStorage.setItem('eco_guide_done', '1'); };
  $('#guide-next').onclick = () => showGuide(step + 1);
}

/* ── 事件绑定与启动 ── */
function initSearch() {
  $$('#search-pills button').forEach(b => b.onclick = () => {
    S.searchMode = b.dataset.k;
    $$('#search-pills button').forEach(x => x.classList.toggle('on', x === b));
    // v0.3.1：切模式给明确反馈（旧内核窗口用户曾反馈"点了没反应"）
    const names = { note: '经验检索：搜经验笔记本的关键词', error: '报错检索：粘贴报错原文，自动定位根因经验', eco: '生态检索：搜技能库与工作流' };
    toast(names[S.searchMode] || S.searchMode, true);
  });
  $('#search-go').onclick = doSearch;
  $('#search-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') { doSearch(); return; }
    if (e.key === 'ArrowUp') { e.preventDefault(); SearchHist.recall(-1); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); SearchHist.recall(1); }
  });
  $('#search-input').addEventListener('focus', e => { if (!e.target.value.trim()) SearchHist.show(); });
  $('#search-input').addEventListener('input', e => {
    const v = e.target.value;
    if (/traceback|exception|error:|错误|失败|raise\s+\w+|^(import|from)\s.+\n.*error/im.test(v) && S.searchMode !== 'error') {
      S.searchMode = 'error';
      $$('#search-pills button').forEach(x => x.classList.toggle('on', x.dataset.k === 'error'));
      toast('检测到报错文本，已切到报错根因模式', true);
    }
  });
  document.addEventListener('click', e => {
    if (!e.target.closest('.searchbox')) closeSearch();
    if (!e.target.closest('.popover') && !e.target.closest('.node')) {
      const p = $('#starmap-pop');
      if (p) p.innerHTML = '';
    }
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      closeDrawer(); closeSearch();
      Gate.close();
      $('#bell-panel').classList.add('hidden');
      if (typeof CmdPalette !== 'undefined') CmdPalette.close();
      if (typeof Shortcuts !== 'undefined') Shortcuts.closeHelp();
    }
  });
}

function initNav() {
  $$('#nav a').forEach(a => a.onclick = () => goto(a.dataset.view));
  $('#refresh-btn').onclick = () => { S.cache = {}; loadStatus(); route(); toast('已重新读取快照', true); };
  $('#drawer-close').onclick = closeDrawer;
  $('#drawer-mask').onclick = closeDrawer;
  $('#firstaid-btn').onclick = () => goto('firstaid');
  const muteBtn = $('#mute-reset');   // v0.3.2：状态栏减负后此按钮移到命令面板，保留兼容
  if (muteBtn) muteBtn.onclick = () => {
    const n = Gate.resetAll();
    toast(n ? `已重置 ${n} 类动作的「不再提醒」` : '没有已静默的提醒', true);
  };
  $('#font-minus').onclick = () => { S.fontStep = Math.max(0, S.fontStep - 1); applyFont(); };
  $('#font-plus').onclick = () => { S.fontStep = Math.min(3, S.fontStep + 1); applyFont(); };
  $('#guide-btn').onclick = () => showGuide(0);
  $$('#anim-ctl button').forEach(b => b.onclick = () => setAnimLevel(b.dataset.lv));
  $$('#export-ctl button').forEach(b => b.onclick = () => exportView(b.dataset.exp));
  $('#diag-btn').onclick = () => copyDiagnostic();
  const cruiseBtn = $('#cruise-btn');
  if (cruiseBtn) cruiseBtn.onclick = () => { if (typeof Cruise !== 'undefined') Cruise.toggle(); else toast('巡航模式将在本版本稍后提供', false); };
  document.addEventListener('click', e => {
    const gd = e.target.closest('[data-goto-detail]');
    if (gd) { gotoDetail(gd.dataset.gotoDetail); return; }
    const gs = e.target.closest('[data-goto-skill]');
    if (gs) { gotoSkill(gs.dataset.gotoSkill); return; }
    const gt = e.target.closest('[data-goto]');
    if (gt) { goto(gt.dataset.goto); return; }
  });
}

/* ── 运行环境探测：pywebview 桌面窗口 vs 系统浏览器（T3） ──
   桌面端 = 凝练版（保留核心功能，动画朴素，省资源）；
   浏览器端 = 全量版（含仅浏览器便利功能与高质量动效）。 */
function initEnv() {
  const ua = navigator.userAgent || '';
  S.desktop = /pywebview/i.test(ua) || window.__ecoDesktop === true;
  window.__ecoDesktop = S.desktop;   // 供其他模块（如 bell 通知）判定
  if (S.desktop) document.documentElement.dataset.desktop = 'true';
}

/* ── 启动 ── */
initEnv();
initIcons();                       // 内联 SVG 图标（导航/顶栏/抽屉关闭）
try { Particles.init(); } catch (e) { /* 旧内核无 canvas 时静默降级 */ }
initPalette();
applyFont();
setAnimLevel(S.animLevel, true);   // 应用持久化的动画强度（静默，不打扰启动）
initSearch();
initNav();
loadStatus();
Bell.init();
try { ecoKernelCheck(); } catch (e) {}
route();
if (!localStorage.getItem('eco_guide_done')) setTimeout(() => showGuide(0), 600);
startPoll();
