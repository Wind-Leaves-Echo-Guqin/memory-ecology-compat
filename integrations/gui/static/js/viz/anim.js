/* ============================================================
   viz/anim.js — 动效基座（v0.3.3）
   设计原则（与 style.css 的动效 token 配套）：
     ① 数据先上屏，动效只作过渡，且必须可被打断（Anim.staggerIn 返回 cancel）
     ② 一律受 S.animLevel（low/medium/full）与 prefers-reduced-motion 双重管控
     ③ 不新增长驻 rAF 循环：帧率采样寄生在已有的仿真/粒子循环里
        （ForceSim.onTick → Anim.reportFrame()），低帧率触发一次性降级
     ④ low 档退化为无过渡的静态呈现（见 style.css 的 html[data-anim="low"] 规则）
   ============================================================ */
'use strict';

var Anim = {
  /* ── 强度档位 ── */
  level: function () {
    if (typeof S === 'object' && S && S.animLevel === 'low') return 'low';   // 用户显式选轻度
    try {
      if (typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches) return 'low';
    } catch (e) { /* 旧内核无 matchMedia */ }
    return (typeof S === 'object' && S && S.animLevel) || 'full';
  },
  /* 可过渡（非 low、非 reduced-motion）：入场/过渡类动效的总开关 */
  on: function () { return this.level() !== 'low'; },
  /* 全量（full 档）：仅为装饰性动效（粒子/流动/涟漪）开闸 */
  full: function () { return this.level() === 'full'; },

  /* ── 缓动与插值 ── */
  easeOutCubic: function (p) { return 1 - Math.pow(1 - p, 3); },
  easeInOutCubic: function (p) { return p < .5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2; },
  easeOutBack: function (p) { var c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2); },
  lerp: function (a, b, p) { return a + (b - a) * p; },
  clamp: function (v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; },

  /* ── 错峰入场（可中断）──
     用 CSS 动画类 + 逐元素 animation-delay 实现：不写内联 opacity，
     因此动画不被支持时元素仍是常态可见（安全降级），延迟期间由 keyframes 的
     from 帧接管（animation-fill-mode: both）。 */
  staggerIn: function (els, o) {
    o = o || {};
    var list = els ? Array.prototype.slice.call(els) : [];
    if (!list.length) return function () {};
    if (!this.on()) { list.forEach(function (el) { if (el) el.style.animationDelay = ''; }); return function () {}; }
    var step = o.step != null ? o.step : 24;
    var max = o.max != null ? o.max : 600;              // 上限：长列表尾部不必等太久
    var cls = o.cls || 'a-stagger';
    list.forEach(function (el, i) {
      if (!el) return;
      el.classList.remove(cls);
      el.style.animationDelay = Math.min(i * step, max) + 'ms';
      void el.offsetWidth;                              // 强制回流：重放同一动画
      el.classList.add(cls);
    });
    var self = this;
    this._lastStagger = list;
    return function cancel() {
      list.forEach(function (el) { if (el) { el.classList.remove(cls); el.style.animationDelay = ''; } });
    };
  },

  /* 只给"新增/变化"的元素加入场动画（轮询刷新时其余不动，避免整页闪） */
  markNew: function (els, cls) {
    if (!this.on()) return;
    var list = els ? Array.prototype.slice.call(els) : [];
    list.forEach(function (el, i) {
      if (!el) return;
      el.classList.remove(cls || 'a-stagger');
      el.style.animationDelay = Math.min(i * 24, 400) + 'ms';
      void el.offsetWidth;
      el.classList.add(cls || 'a-stagger');
    });
  },

  /* 一次性重放某个 CSS 动画（涟漪/闪光这类"每次触发都要重播"的效果） */
  pulse: function (el, cls, ms) {
    if (!el || !this.on()) return;
    el.classList.remove(cls);
    void el.offsetWidth;
    el.classList.add(cls);
    if (ms) setTimeout(function () { if (el) el.classList.remove(cls); }, ms);
  },

  /* ── 属性过渡（一次性的数值动画，走 rAF 但自行终止，不常驻）── */
  transitionOnce: function (el, props, dur, ease) {
    if (!el) return;
    var keys = Object.keys(props || {});
    if (!keys.length) return;
    if (!this.on()) {
      keys.forEach(function (k) { el.style[k] = props[k]; });
      return;
    }
    var from = {};
    keys.forEach(function (k) { from[k] = el.style[k] || ''; });
    var t0 = 0, ez = ease || this.easeOutCubic, self = this;
    function step(t) {
      if (!t0) t0 = t;
      var p = Math.min(1, (t - t0) / (dur || 300));
      var e = ez(p);
      keys.forEach(function (k) {
        var to = props[k];
        var m = /^(-?[\d.]+)px$/.exec(to);
        if (m && /^(-?[\d.]+)px$/.exec(from[k])) {
          el.style[k] = self.lerp(parseFloat(from[k]), parseFloat(m[1]), e).toFixed(1) + 'px';
        } else if (p >= 1) {
          el.style[k] = to;
        }
      });
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  },

  /* ── 视口惰性入场（IntersectionObserver 封装；老内核立即执行）── */
  inViewport: function (el, cb, opts) {
    if (!el) return;
    if (typeof IntersectionObserver !== 'function' || !this.on()) { cb(el); return; }
    var io = new IntersectionObserver(function (ents) {
      ents.forEach(function (en) {
        if (en.isIntersecting) { io.disconnect(); cb(el); }
      });
    }, opts || { root: null, rootMargin: '120px 0px', threshold: 0.01 });
    io.observe(el);
    return io;
  },
  /* 批量登记：容器内所有 [data-lazy] 子元素进入视口时加 a-in 类 */
  lazyIn: function (root, cls) {
    var list = Array.prototype.slice.call((root || document).querySelectorAll('[data-lazy]'));
    if (!list.length) return;
    var self = this;
    if (typeof IntersectionObserver !== 'function' || !this.on()) {
      list.forEach(function (el) { el.classList.add(cls || 'a-in'); });
      return;
    }
    var io = new IntersectionObserver(function (ents) {
      ents.forEach(function (en) {
        if (!en.isIntersecting) return;
        en.target.classList.add(cls || 'a-in');
        io.unobserve(en.target);
      });
    }, { root: null, rootMargin: '160px 0px', threshold: 0.01 });
    list.forEach(function (el) { io.observe(el); });
  },

  /* ── 帧率监测（寄生式：由已有的仿真/粒子循环上报，不新增 rAF）──
     reportFrame() 在每个动画帧调用；滚动窗口 1s 出一次 fps。
     连续两个窗口 < 30fps → 触发一次降级回调（自动关次要动效），并在状态栏提示一次。 */
  fps: 0,
  degraded: false,
  _n: 0, _t0: 0, _low: 0, _cbs: [], _warned: false,
  onDegrade: function (cb) { if (typeof cb === 'function') this._cbs.push(cb); },
  reportFrame: function (t) {
    var now = t || (typeof performance !== 'undefined' && performance.now ? performance.now() : Date.now());
    if (!this._t0) { this._t0 = now; this._n = 0; }
    this._n++;
    var dt = now - this._t0;
    if (dt < 1000) return;
    var f = this._n * 1000 / dt;
    this.fps = Math.round(f);
    this._t0 = now; this._n = 0;
    updateFpsBadge(this.fps);
    if (this.degraded) return;
    if (f < 30) {
      if (++this._low >= 2) {
        this.degraded = true;
        for (var i = 0; i < this._cbs.length; i++) { try { this._cbs[i](f); } catch (e) {} }
        this._notifyDegrade(f);
      }
    } else if (this._low > 0) this._low--;
  },
  _notifyDegrade: function (f) {
    if (this._warned) return;
    this._warned = true;
    var m = AnimLevelBar();
    if (typeof toast === 'function') toast('帧率 ' + f + 'fps，已自动关闭次要动效（边流动/粒子减半）', true);
  },
  /* 手动复位降级（切档案/重置时用） */
  resetDegrade: function () { this.degraded = false; this._low = 0; this._warned = false; this._n = 0; this._t0 = 0; this._cbs = []; },

  /* ── 变化签名：轮询刷新时只让"变化的条目"动画 ── */
  _sig: {},
  changed: function (key, value) {
    var h = hashStr(typeof value === 'string' ? value : JSON.stringify(value));
    var prev = this._sig[key];
    this._sig[key] = h;
    return prev !== h && prev !== undefined;
  },
  forget: function (key) { delete this._sig[key]; },
};

/* 轻量字符串散列（FNV-1a 变体；仅用于变化比对，不用于安全） */
function hashStr(s) {
  var h = 2166136261;
  s = String(s == null ? '' : s);
  for (var i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = (h * 16777619) >>> 0;
  }
  return h.toString(36) + ':' + s.length;
}

/* 状态栏降级提示位（无状态栏时静默；不额外建 DOM） */
function AnimLevelBar() {
  var el = document.getElementById('patrol-dot');
  if (!el) return null;
  el.classList.add('live');
  setTimeout(function () { el.classList.remove('live'); }, 3000);
  return el;
}

/* 状态栏实测帧率徽标（T8 性能矩阵要用真实数字，而不是"感觉流畅"） */
function updateFpsBadge(f) {
  var el = document.getElementById('fps-badge');
  if (!el) return;
  el.classList.remove('hidden');
  el.textContent = f + ' fps';
  el.className = 'fps-badge' + (f < 30 ? ' bad' : f < 50 ? ' warn' : '');
  clearTimeout(el._h);
  el._h = setTimeout(function () { el.classList.add('hidden'); }, 6000);
}
