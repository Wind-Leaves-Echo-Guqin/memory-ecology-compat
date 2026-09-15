/* ============================================================
   cruise.js — 全屏巡航模式（T14）
   两种模式（可切换，偏好持久化）：
     ① rotate 自动轮播九视图：隐藏导航/状态栏，按键位顺序循环，每视图停留 N 秒
     ② big    固定驾驶舱大屏：只显示驾驶舱，字号与卡片放大（监控电视/副屏）
   · 进入即切深空大屏 f 色板（退出恢复原色板）
   · 鼠标移动 / 任意按键退出（rotate 模式）；Esc 退出
   · 轮播不打断后台轮询：cron 失败/水位越线仍由 Bell + 桌面通知推送
   ============================================================ */
'use strict';

var Cruise = {
  active: false,
  mode: localStorage.getItem('eco_cruise_mode') || 'rotate',
  dwell: +(localStorage.getItem('eco_cruise_dwell') || 10),
  order: ['dashboard', 'health', 'memories', 'experiences', 'starmap', 'lineage', 'candidates', 'timeline', 'actions'],
  idx: 0,
  _timer: 0,
  _ctlrTimer: 0,
  _prevPalette: null,
  _bindMove: null,

  toggle: function () { this.active ? this.exit() : this.enter(); },

  enter: function (mode) {
    if (mode) { this.mode = mode; localStorage.setItem('eco_cruise_mode', mode); }
    this.active = true;
    this._prevPalette = S.palette;
    document.documentElement.classList.add('cruising');
    document.documentElement.classList.toggle('cruise-big', this.mode === 'big');
    setPalette('f');                       // 大屏场景恒用深色（浅色刺眼）
    this.idx = Math.max(0, this.order.indexOf(S.view));
    this._panel();
    if (this.mode === 'rotate') { this._go(this.idx); this._tick(); }
    else { goto('dashboard'); this._ctlShow(true); }
    this._bindExit();
    if (typeof Particles !== 'undefined') Particles.sync();
    toast(this.mode === 'rotate' ? '巡航：轮播九视图（鼠标移动或按键退出）' : '巡航：固定驾驶舱大屏（移动鼠标显示控制条）', true);
  },

  exit: function () {
    if (!this.active) return;
    this.active = false;
    clearInterval(this._timer);
    clearTimeout(this._ctlrTimer);
    document.documentElement.classList.remove('cruising', 'cruise-big');
    var p = $('#cruise-ctl');
    if (p) p.remove();
    if (this._bindMove) {
      window.removeEventListener('mousemove', this._bindMove);
      window.removeEventListener('keydown', this._bindMove, true);
      this._bindMove = null;
    }
    if (this._prevPalette && this._prevPalette !== 'f') setPalette(this._prevPalette);
    loadStatus();
    route();
    if (typeof Particles !== 'undefined') Particles.sync();
    toast('已退出巡航', true);
  },

  _go: function (i) {
    this.idx = ((i % this.order.length) + this.order.length) % this.order.length;
    goto(this.order[this.idx]);
    this._ctlShow(false);
    this._bar(0);
  },

  _tick: function () {
    var self = this;
    clearInterval(this._timer);
    var step = 250, elapsed = 0, total = Math.max(3, this.dwell) * 1000;
    this._timer = setInterval(function () {
      elapsed += step;
      self._bar(Math.min(1, elapsed / total));
      if (elapsed >= total) { elapsed = 0; self._go(self.idx + 1); }
    }, step);
  },

  /* 底部进度条 */
  _bar: function (p) {
    var b = $('#cruise-bar');
    if (b) b.style.width = (p * 100) + '%';
  },

  _panel: function () {
    var old = $('#cruise-ctl');
    if (old) old.remove();
    var p = document.createElement('div');
    p.id = 'cruise-ctl';
    p.innerHTML =
      '<div class="cc-row">' +
      '<span class="cc-modes"><button data-cm="rotate" class="' + (this.mode === 'rotate' ? 'on' : '') + '">轮播九视图</button>' +
      '<button data-cm="big" class="' + (this.mode === 'big' ? 'on' : '') + '">固定大屏</button></span>' +
      '<label class="cc-dwell">停留 <input type="range" id="cc-dwell" min="5" max="60" step="5" value="' + this.dwell + '"> <b id="cc-dwellv">' + this.dwell + '</b>s</label>' +
      '<button class="cc-exit" id="cc-exit">退出巡航</button>' +
      '</div><div class="cc-progress"><div id="cruise-bar"></div></div>';
    document.body.appendChild(p);
    var self = this;
    $$('#cruise-ctl [data-cm]').forEach(function (b) {
      b.onclick = function () {
        if (self.mode === b.dataset.cm) return;
        self.mode = b.dataset.cm;
        localStorage.setItem('eco_cruise_mode', self.mode);
        document.documentElement.classList.toggle('cruise-big', self.mode === 'big');
        $$('#cruise-ctl [data-cm]').forEach(function (x) { x.classList.toggle('on', x === b); });
        if (self.mode === 'rotate') { self._go(self.idx); self._tick(); } else { clearInterval(self._timer); goto('dashboard'); }
        self._ctlShow(true);
      };
    });
    var sl = $('#cc-dwell');
    if (sl) sl.oninput = function () {
      self.dwell = +sl.value;
      localStorage.setItem('eco_cruise_dwell', String(self.dwell));
      $('#cc-dwellv').textContent = self.dwell;
      if (self.mode === 'rotate') self._tick();
    };
    $('#cc-exit').onclick = function () { self.exit(); };
  },

  /* 控制条自动隐藏（大屏模式）；轮播模式只在鼠标动时显形 */
  _ctlShow: function (sticky) {
    var p = $('#cruise-ctl');
    if (!p) return;
    p.classList.remove('cc-hidden');
    clearTimeout(this._ctlrTimer);
    var self = this;
    if (!sticky) this._ctlrTimer = setTimeout(function () { p.classList.add('cc-hidden'); }, 3200);
  },

  _bindExit: function () {
    var self = this;
    if (this._bindMove) return;
    this._bindMove = function (e) {
      if (!self.active) return;
      if (e.type === 'keydown') {
        if (e.key === 'Escape') { self.exit(); return; }
        // 忽略修饰键单独按下，避免误退
        if (['Shift', 'Control', 'Alt', 'Meta'].indexOf(e.key) >= 0) return;
        if (e.key === 'F11') return;
        self.exit();
        return;
      }
      self._ctlShow(false);
    };
    window.addEventListener('mousemove', this._bindMove);
    window.addEventListener('keydown', this._bindMove, true);
  },
};
