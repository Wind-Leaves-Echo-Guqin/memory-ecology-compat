/* ============================================================
   shortcuts.js — 键盘快捷键系统（T5）
   · g + 视图键 跳转九视图（300ms 内按第二键，超时重置）
   · ? 快捷键参考浮层（独立于新手引导，? 再按关闭）
   · / 聚焦搜索框、Ctrl+K 命令面板、r 刷新、Esc 关闭浮层
   · 输入框/文本域/可编辑区域内不拦截按键（避免抢键）
   ============================================================ */
'use strict';

var Shortcuts = {
  /* g 键第二键 → 视图 */
  GOTO: {
    d: 'dashboard', m: 'memories', e: 'experiences', s: 'starmap', l: 'lineage',
    c: 'candidates', t: 'timeline', h: 'health', a: 'actions',
  },
  /* 参考表（与 ? 浮层共用；顺序即展示顺序） */
  REF: [
    ['g d / g m / g e', '驾驶舱 / 记忆库 / 经验笔记本'],
    ['g s / g l / g c', '技能星图 / 血缘谱系 / 候选孵化台'],
    ['g t / g h / g a', '时间线 / 体检评测 / 动作日志'],
    ['/ 或 Ctrl+K', '聚焦搜索框 / 打开命令面板'],
    ['r', '重新读取快照（刷新当前视图）'],
    ['?', '本参考表（再按 ? 或 Esc 关闭）'],
    ['Ctrl+Shift+F', '全屏巡航（轮播九视图 / 固定驾驶舱大屏）'],
    ['Esc', '关闭抽屉 / 浮层 / 闸门 / 面板'],
  ],
  _g: false,
  _t: null,

  init: function () {
    var self = this;
    document.addEventListener('keydown', function (e) { self.onKey(e); });
  },

  /* 焦点在输入类元素里时不抢键（搜索框内除 Esc/Ctrl+K 外一律放行） */
  typing: function (e) {
    var el = e.target;
    if (!el || !el.tagName) return false;
    var t = el.tagName.toLowerCase();
    return t === 'input' || t === 'textarea' || t === 'select' || el.isContentEditable === true;
  },

  onKey: function (e) {
    var self = this;
    var k = e.key;
    // Ctrl+K / Cmd+K：命令面板（输入框内也生效）
    if ((e.ctrlKey || e.metaKey) && (k === 'k' || k === 'K')) {
      e.preventDefault();
      if (typeof CmdPalette !== 'undefined') CmdPalette.toggle();
      return;
    }
    // Ctrl+Shift+F：全屏巡航（输入框内也生效）
    if ((e.ctrlKey || e.metaKey) && e.shiftKey && (k === 'f' || k === 'F')) {
      e.preventDefault();
      if (typeof Cruise !== 'undefined') Cruise.toggle();
      return;
    }
    if (k === 'Escape') { this.closeHelp(); return; }
    if (this.typing(e) || e.ctrlKey || e.metaKey || e.altKey) {
      if (this._g) this.resetG();
      return;
    }
    // g 前缀模式
    if (this._g) {
      var view = this.GOTO[k.toLowerCase()];
      this.resetG();
      if (view) { e.preventDefault(); goto(view); }
      return;
    }
    if (k === 'g' || k === 'G') {
      this._g = true;
      clearTimeout(this._t);
      this._t = setTimeout(function () { self._g = false; }, 300);
      return;
    }
    if (k === '?' || (e.shiftKey && e.code === 'Slash')) { e.preventDefault(); this.help(); return; }
    if (k === '/' || (!e.shiftKey && e.code === 'Slash')) {
      e.preventDefault();
      var inp = $('#search-input');
      if (inp) { inp.focus(); inp.select(); if (typeof SearchHist !== 'undefined') SearchHist.show(); }
      return;
    }
    if (k === 'r' || k === 'R') {
      e.preventDefault();
      var btn = $('#refresh-btn');
      if (btn) btn.click(); else { S.cache = {}; loadStatus(); route(); }
    }
  },

  resetG: function () { this._g = false; clearTimeout(this._t); },

  /* ? 快捷键参考浮层（独立浮层，再按 ? 关闭） */
  help: function () {
    var m = $('#kbd-mask');
    if (!m) return;
    if (!m.classList.contains('hidden')) { this.closeHelp(); return; }
    m.innerHTML = '<div class="kbd-box">' +
      '<h3>⌨ 快捷键参考 <small>再按 ? 或 Esc 关闭</small></h3>' +
      '<div class="kbd-grid">' +
      this.REF.map(function (r) {
        return '<span class="kbd-key">' + esc(r[0]) + '</span><span class="kbd-desc">' + esc(r[1]) + '</span>';
      }).join('') +
      '</div>' +
      '<div class="kbd-foot">写操作全部走确认闸门；快捷键不会绕过闸门。</div>' +
      '<div class="row" style="justify-content:flex-end;margin-top:12px">' +
      '<button class="ghost-btn" id="kbd-diag">复制诊断信息</button>' +
      '<button class="ghost-btn" id="kbd-close">关闭</button></div></div>';
    m.classList.remove('hidden');
    $('#kbd-close').onclick = this.closeHelp.bind(this);
    $('#kbd-diag').onclick = function () { if (typeof copyDiagnostic === 'function') copyDiagnostic(); };
    m.onclick = function (e) { if (e.target === m) Shortcuts.closeHelp(); };
  },
  closeHelp: function () {
    var m = $('#kbd-mask');
    if (m) m.classList.add('hidden');
  },
};

Shortcuts.init();
