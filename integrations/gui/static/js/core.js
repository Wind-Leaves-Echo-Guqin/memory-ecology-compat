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
  patrolStamp: null,
  focus: null,
  expFilters: { type: '', status: '', sort: 'created', page: 1 },
  memFilters: { type: '', status: '', sort: 'last' },
  timelineFilter: '',
  timelineMode: 'chart',
  timelineChart: 'swim',
  healthTab: 'report',
  starmap: { mode: 'skeleton', list: false, cat: '', lines: { rel: true, blood: true, dup: true } },
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
async function route() {
  const view = (location.hash.replace(/^#\//, '') || 'dashboard');
  S.view = view;
  $$('#nav a').forEach(a => a.classList.toggle('on', a.dataset.view === view));
  const v = $('#view');
  v.innerHTML = '<div class="loading">正在读取生态快照…</div>';
  try {
    await (RENDER[view] || RENDER.dashboard)(v);
    v.classList.remove('view-anim'); void v.offsetWidth; v.classList.add('view-anim');
  } catch (e) {
    v.innerHTML = `<div class="empty">读取失败：${esc(e.message)}<br>fail-open：检索/接口失败不影响其余视图，可点右上角 ⟳ 重试</div>`;
  }
  if (S.focus) {
    const f = S.focus; S.focus = null;
    await sleep(150);
    if (f.kind === 'detail') await openDetailDrawer(f.slug);
    else if (f.kind === 'exp') await openExpDrawer(f.id);
    else if (f.kind === 'skill') focusSkill(f.name);
  }
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
window.addEventListener('hashchange', route);

/* ── 检索（三模式 + 复制） ── */
async function doSearch() {
  const q = $('#search-input').value.trim();
  if (!q) return;
  const panel = $('#search-panel');
  panel.classList.remove('hidden');
  panel.innerHTML = '<div class="faint" style="padding:8px">检索中…（subprocess 调用只读 CLI）</div>';
  try {
    const r = await EcoApi.get(`/api/search?kind=${S.searchMode}&q=${encodeURIComponent(q)}`);
    if (!r.ok) throw new Error(r.error || '失败');
    let html = '';
    const resultText = JSON.stringify(r, null, 2);
    html += '<div class="sr-copy"><a id="search-copy">复制结果</a></div>';
    if (S.searchMode === 'error')
      html += `<div class="sr-head">根因分层 · 异常类：${esc((r.exc || []).join(', ') || '—')}</div>`;
    if (r.hits && r.hits.length) {
      r.hits.forEach(hh => {
        const id = hh.id || '';
        html += `<div class="sr-hit" ${id.startsWith('exp-') ? `data-exp="${esc(id)}"` : id ? `data-skill="${esc(hh.name || id)}"` : ''}>
          <div class="sr-line">
            ${hh.type ? `<span class="tag">${esc(hh.type)}</span>` : ''}
            ${hh.status ? `<span class="tag">${esc(hh.status)}</span>` : ''}
            <span class="sr-t">${esc(hh.trigger || hh.name || hh.id)}</span>
            ${hh.indeg != null ? `<span class="faint">被引${hh.indeg}</span>` : ''}
          </div>
          ${hh.evidence ? `<div class="sr-d mono">${esc(hh.evidence.slice(0, 140))}</div>` : ''}
          ${hh.desc ? `<div class="sr-d">${esc(hh.desc.slice(0, 140))}</div>` : ''}
          ${hh.sections ? `<div class="sr-d faint">${esc(hh.sections.slice(0, 120))}</div>` : ''}
        </div>`;
      });
    } else if (r.raw) {
      html += `<pre class="mono" style="white-space:pre-wrap;font-size:11.5px;color:var(--ink2)">${esc(r.raw.slice(0, 2000))}</pre>`;
    } else {
      html += '<div class="faint" style="padding:8px">无命中。</div>';
    }
    panel.innerHTML = html;
    $('#search-copy').onclick = () => ecoCopy(resultText, '检索结果已复制');
    $$('[data-exp]', panel).forEach(el => el.onclick = () => { closeSearch(); gotoExp(el.dataset.exp); });
    $$('[data-skill]', panel).forEach(el => el.onclick = () => { closeSearch(); gotoSkill(el.dataset.skill); });
  } catch (e) {
    panel.innerHTML = `<div class="alarm" style="margin:0">检索失败（fail-open，不影响其他功能）：${esc(e.message)}</div>`;
  }
}
function closeSearch() { $('#search-panel').classList.add('hidden'); }

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

/* ── 色板（五浅冷精修 + 第6套深空大屏） ── */
const PALETTES = [
  ['a', '雾白', '#0e7490'], ['b', '冷蓝灰', '#35639e'], ['c', '青瓷', '#3a7d6d'],
  ['d', '黛紫', '#5e56ad'], ['e', '玄墨', '#41474f'], ['f', '深空大屏', '#22d3ee'],
];
function initPalette() {
  document.documentElement.dataset.palette = S.palette;
  $('#palette-dots').innerHTML = PALETTES.map(([k, n, c]) =>
    `<i data-p="${k}" title="${n}" style="background:${c}" class="${S.palette === k ? 'on' : ''}"></i>`).join('');
  $$('#palette-dots i').forEach(i => i.onclick = () => {
    S.palette = i.dataset.p;
    localStorage.setItem('eco_palette', S.palette);
    initPalette();
  });
}

/* ── 全局字号（12/13/14/15 四档，localStorage 持久化） ── */
const FONT_SIZES = [12, 13, 14, 15];
function applyFont() {
  document.body.style.fontSize = FONT_SIZES[S.fontStep] + 'px';
  localStorage.setItem('eco_font_step', String(S.fontStep));
}

/* ── 新手引导（三步遮罩，可跳过可重看） ── */
const GUIDE_STEPS = [
  { title: '第 1 步 · 看健康', body: '驾驶舱 10 秒扫完：体检评分环、记忆水位条、四道门账本与运行证据。<br>红色告警条 = 需要你处理的事（如 cron 失败、记忆越线）。' },
  { title: '第 2 步 · 处理告警', body: '顶栏 🔔 铃铛聚合全部告警，每条可复制、可跳转处理。<br>记忆越线时驾驶舱会出现「立即挤出」按钮（有红色警告与确认闸门）。' },
  { title: '第 3 步 · 执行动作', body: '写操作（挤出/整合/采纳/孵化/体检…）都走确认闸门：<br>弹窗说明影响与回滚 → 高风险需勾选「我已知晓风险」→ 可勾「不再提醒」。<br>每次执行都记录在「动作日志」视图。' },
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
  $('#search-input').addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
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
    }
  });
}

function initNav() {
  $$('#nav a').forEach(a => a.onclick = () => goto(a.dataset.view));
  $('#refresh-btn').onclick = () => { S.cache = {}; loadStatus(); route(); toast('已重新读取快照', true); };
  $('#drawer-close').onclick = closeDrawer;
  $('#drawer-mask').onclick = closeDrawer;
  $('#firstaid-btn').onclick = () => goto('firstaid');
  $('#mute-reset').onclick = () => {
    const n = Gate.resetAll();
    toast(n ? `已重置 ${n} 类动作的「不再提醒」` : '没有已静默的提醒', true);
  };
  $('#font-minus').onclick = () => { S.fontStep = Math.max(0, S.fontStep - 1); applyFont(); };
  $('#font-plus').onclick = () => { S.fontStep = Math.min(3, S.fontStep + 1); applyFont(); };
  $('#guide-btn').onclick = () => showGuide(0);
  document.addEventListener('click', e => {
    const gd = e.target.closest('[data-goto-detail]');
    if (gd) { gotoDetail(gd.dataset.gotoDetail); return; }
    const gs = e.target.closest('[data-goto-skill]');
    if (gs) { gotoSkill(gs.dataset.gotoSkill); return; }
    const gt = e.target.closest('[data-goto]');
    if (gt) { goto(gt.dataset.goto); return; }
  });
}

/* ── 启动 ── */
initPalette();
applyFont();
initSearch();
initNav();
loadStatus();
Bell.init();
try { ecoKernelCheck(); } catch (e) {}
route();
if (!localStorage.getItem('eco_guide_done')) setTimeout(() => showGuide(0), 600);
setInterval(loadStatus, 30000);
