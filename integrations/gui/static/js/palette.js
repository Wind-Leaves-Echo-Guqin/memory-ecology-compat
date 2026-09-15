/* ============================================================
   palette.js — Ctrl+K 命令面板（T6）
   匹配源：视图名（9）+ 视图别名 + 动作名（/api/actions）+ 常见操作
   ↑↓ 导航 · Enter 执行 · Esc 关闭 · 输入即过滤（模糊子序列匹配）
   写动作一律走 Gate 闸门 + dry-run 预览（与视图内按钮同一条链）
   ============================================================ */
'use strict';

var CmdPalette = {
  open_: false,
  items: [],
  filtered: [],
  sel: 0,

  /* 视图（含别名，便于模糊命中："急"→急救箱留给动作，视图别名用常见叫法） */
  VIEWS: [
    ['dashboard', '驾驶舱', 'dashboard overview 首页 总览 概览 健康 评分'],
    ['memories', '记忆库', 'memories 记忆 L1 L2 水位 条目'],
    ['experiences', '经验笔记本', 'experiences 经验 笔记本 案例 模式'],
    ['starmap', '技能星图', 'starmap 技能 星图 图谱 引用'],
    ['lineage', '血缘谱系', 'lineage 血缘 谱系 链路 故事'],
    ['candidates', '候选孵化台', 'candidates 候选 孵化 技能候选'],
    ['timeline', '时间线', 'timeline 时间线 泳道 回放 历史'],
    ['health', '体检评测', 'health 体检 评测 报告 评分'],
    ['actions', '动作日志', 'actions 动作 日志 账本 执行记录'],
  ],

  base: function () {
    var out = this.VIEWS.map(function (v) {
      return { kind: 'view', key: v[0], name: v[1], hint: '跳转视图', match: v[1] + ' ' + v[2] + ' ' + v[0] };
    });
    var cmds = [
      ['刷新快照', '重新读取生态快照', function () { $('#refresh-btn').click(); }],
      ['导出当前视图 · JSON', '下载为 .json 文件', function () { exportView('json'); }],
      ['导出当前视图 · Markdown', '渲染为表格复制到剪贴板', function () { exportView('md'); }],
      ['导出当前视图 · 文本', '纯文本摘要，适合粘贴到 IM', function () { exportView('text'); }],
      ['复制诊断信息', '版本/数据根/cron/水位/最近动作', function () { copyDiagnostic(); }],
      ['快捷键参考', '完整快捷键表（?）', function () { Shortcuts.help(); }],
      ['查看告警', '打开全局告警铃铛', function () { Bell.toggle(); }],
      ['打开急救箱', '自检 + 一键修复', function () { goto('firstaid'); }],
      ['重看新手引导', '五步引导教程', function () { showGuide(0); }],
      ['色板 · 雾白', '浅色默认', function () { setPalette('a'); }],
      ['色板 · 青瓷', '低饱和阅读向', function () { setPalette('c'); }],
      ['色板 · 深空大屏', '深色监督大屏', function () { setPalette('f'); }],
      ['动画强度 · 轻度', '仅视图淡入淡出', function () { setAnimLevel('low'); }],
      ['动画强度 · 中度', '过渡+度量+图表动效', function () { setAnimLevel('medium'); }],
      ['动画强度 · 满载', '全部动效（默认）', function () { setAnimLevel('full'); }],
      ['重置「不再提醒」', '清空闸门静默记忆', function () {
        var n = Gate.resetAll();
        toast(n ? '已重置 ' + n + ' 类动作的「不再提醒」' : '没有已静默的提醒', true);
      }],
      ['自动轮询 · 15 秒', '后台检测数据变化', function () { setPollInterval(15); }],
      ['自动轮询 · 30 秒', '默认节奏', function () { setPollInterval(30); }],
      ['自动轮询 · 60 秒', '省资源', function () { setPollInterval(60); }],
      ['自动轮询 · 关闭', '只手动刷新', function () { setPollInterval(0); }],
    ];
    if (typeof Cruise !== 'undefined') cmds.push(['全屏巡航', '轮播九视图 / 固定驾驶舱大屏', function () { Cruise.toggle(); }]);
    cmds.forEach(function (c) {
      out.push({ kind: 'cmd', name: c[0], hint: c[1], match: c[0] + ' ' + c[1], run: c[2] });
    });
    return out;
  },

  loadActions: function () {
    var self = this;
    EcoApi.get('/api/actions').then(function (d) {
      var acts = (d && d.actions) || {};
      Object.keys(acts).forEach(function (k) {
        var a = acts[k];
        self.items.push({
          kind: 'act', key: k, name: a.name || k,
          hint: '动作（' + (a.risk === 'strong' ? '强确认' : '轻确认') + '）· ' + (a.impact || '').slice(0, 40),
          match: (a.name || '') + ' ' + k + ' ' + (a.impact || ''),
        });
      });
      if (self.open_) self.refilter();
    }).catch(function () { /* 动作拉取失败不影响面板其他功能 */ });
  },

  /* 模糊匹配：连续子串优先，其次按子序列顺序命中给分 */
  score: function (q, s) {
    if (!q) return 1;
    s = s.toLowerCase();
    var idx = s.indexOf(q);
    if (idx >= 0) return 1000 - idx;
    var i = 0, gap = 0, last = -1;
    for (var j = 0; j < s.length && i < q.length; j++) {
      if (s[j] === q[i]) { if (last >= 0) gap += j - last - 1; last = j; i++; }
    }
    return i === q.length ? Math.max(1, 200 - gap * 3) : 0;
  },

  toggle: function () { this.open_ ? this.close() : this.open(); },

  open: function () {
    var m = $('#cmd-mask');
    if (!m) return;
    this.open_ = true;
    this.sel = 0;
    m.innerHTML = '<div class="cmd-box">' +
      '<input id="cmd-input" type="text" autocomplete="off" placeholder="输入以搜索：视图 / 动作 / 操作（如"急"、"候选"、"导出"）">' +
      '<div id="cmd-list" class="cmd-list"></div>' +
      '<div class="cmd-foot"><span>↑↓ 选择</span><span>Enter 执行</span><span>Esc 关闭</span></div></div>';
    m.classList.remove('hidden');
    var inp = $('#cmd-input');
    inp.focus();
    var self = this;
    inp.addEventListener('input', function () { self.sel = 0; self.refilter(); });
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') { e.preventDefault(); self.move(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); self.move(-1); }
      else if (e.key === 'Enter') { e.preventDefault(); self.run(); }
      else if (e.key === 'Escape') { e.preventDefault(); self.close(); }
    });
    m.onclick = function (e) { if (e.target === m) self.close(); };
    this.refilter();
  },

  close: function () {
    this.open_ = false;
    var m = $('#cmd-mask');
    if (m) { m.classList.add('hidden'); m.innerHTML = ''; }
  },

  move: function (d) {
    if (!this.filtered.length) return;
    this.sel = (this.sel + d + this.filtered.length) % this.filtered.length;
    this.paint();
    var el = $('.cmd-item.on', $('#cmd-list'));
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
  },

  refilter: function () {
    var q = ($('#cmd-input') ? $('#cmd-input').value : '').trim().toLowerCase();
    var sc = this.score.bind(this);
    this.filtered = this.items
      .map(function (it) { return [sc(q, it.match), it]; })
      .filter(function (p) { return p[0] > 0; })
      .sort(function (a, b) {
        if (b[0] !== a[0]) return b[0] - a[0];
        return (a[1].kind === 'view' ? 0 : 1) - (b[1].kind === 'view' ? 0 : 1);
      })
      .slice(0, 12)
      .map(function (p) { return p[1]; });
    if (this.sel >= this.filtered.length) this.sel = 0;
    this.paint();
  },

  paint: function () {
    var self = this;
    var list = $('#cmd-list');
    if (!list) return;
    if (!this.filtered.length) { list.innerHTML = '<div class="cmd-empty">无匹配项——试试视图名（如"星图"）或操作名（如"导出"）。</div>'; return; }
    list.innerHTML = this.filtered.map(function (it, i) {
      var tag = it.kind === 'view' ? '<span class="tag">视图</span>'
        : it.kind === 'act' ? '<span class="tag warn">动作</span>' : '<span class="tag">操作</span>';
      return '<div class="cmd-item' + (i === self.sel ? ' on' : '') + '" data-i="' + i + '">' +
        tag + '<span class="cmd-name">' + esc(it.name) + '</span>' +
        '<span class="cmd-hint">' + esc(it.hint || '') + '</span></div>';
    }).join('');
    $$('.cmd-item', list).forEach(function (el) {
      el.onmouseenter = function () { self.sel = +el.dataset.i; self.paint(); };
      el.onclick = function () { self.sel = +el.dataset.i; self.run(); };
    });
  },

  run: function () {
    var it = this.filtered[this.sel];
    if (!it) return;
    this.close();
    if (it.kind === 'view') { goto(it.key); return; }
    if (it.kind === 'cmd') { it.run(); return; }
    if (it.kind === 'act') {
      if (typeof runAction === 'function') runAction(it.key, {});      // 闸门 → dry-run → run
      else toast('动作入口暂不可用（视图脚本未加载）', false);
    }
  },
};

CmdPalette.items = CmdPalette.base();
CmdPalette.loadActions();
