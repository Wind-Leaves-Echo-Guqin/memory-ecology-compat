/* ============================================================
   viz/particles.js — 动态光晕/粒子背景层（T11）
   · Canvas 2D 全屏粒子（默认 48 个，慢速机自动降档），颜色取当前色板 accent 弱化
   · 鼠标移动时粒子向指针轻微偏移（光线跟随感）；离开后缓慢回漂
   · 只在深空大屏 f 套 / 全屏巡航模式激活；浅色板与轻度动画下自动停用
   · requestAnimationFrame 驱动，页面隐藏时暂停（省电）
   ============================================================ */
'use strict';

var Particles = {
  canvas: null, ctx: null, ps: [], raf: 0, active: false,
  w: 0, h: 0, dpr: 1,
  mouse: { x: -9999, y: -9999, t: 0 },
  target: 48,
  count: 48,
  lastT: 0, slowFrames: 0,

  init: function () {
    var c = document.createElement('canvas');
    c.id = 'eco-particles';
    document.body.insertBefore(c, document.body.firstChild);
    this.canvas = c;
    this.ctx = c.getContext('2d');
    this.resize();
    var self = this;
    window.addEventListener('resize', function () { self.resize(); });
    window.addEventListener('mousemove', function (e) { self.mouse.x = e.clientX; self.mouse.y = e.clientY; self.mouse.t = Date.now(); });
    document.addEventListener('visibilitychange', function () { if (document.hidden) self.stop(); else self.sync(); });
    this.sync();
  },

  /* 是否应当激活：深色大屏或巡航 + 非轻度动画 + 系统未要求减少动效 */
  shouldRun: function () {
    if (typeof S === 'object' && S.animLevel === 'low') return false;
    try { if (typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches) return false; } catch (e) {}
    var cruising = typeof Cruise !== 'undefined' && Cruise.active;
    return document.documentElement.dataset.palette === 'f' || cruising;
  },

  sync: function () {
    if (this.shouldRun()) this.start(); else this.stop();
  },

  resize: function () {
    if (!this.canvas) return;
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.w = window.innerWidth; this.h = window.innerHeight;
    this.canvas.width = Math.round(this.w * this.dpr);
    this.canvas.height = Math.round(this.h * this.dpr);
    this.canvas.style.width = this.w + 'px';
    this.canvas.style.height = this.h + 'px';
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
  },

  spawn: function () {
    this.ps = [];
    var n = this.count;
    for (var i = 0; i < n; i++) {
      var a = Math.random() * Math.PI * 2, sp = 0.06 + Math.random() * 0.22;
      this.ps.push({
        x: Math.random() * this.w, y: Math.random() * this.h,
        vx: Math.cos(a) * sp, vy: Math.sin(a) * sp,
        r: 0.7 + Math.random() * 2.1, o: 0.12 + Math.random() * 0.34,
      });
    }
  },

  start: function () {
    if (this.active) return;
    this.active = true;
    if (!this.ps.length) this.spawn();
    var self = this;
    this.lastT = performance.now ? performance.now() : Date.now();
    function frame(t) {
      if (!self.active) return;
      self.step(t);
      self.raf = requestAnimationFrame(frame);
    }
    this.raf = requestAnimationFrame(frame);
  },

  stop: function () {
    this.active = false;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    if (this.ctx) this.ctx.clearRect(0, 0, this.w, this.h);
    if (this.canvas) this.canvas.style.opacity = '0';
  },

  step: function (t) {
    var now = t || (performance.now ? performance.now() : Date.now());
    var dt = Math.min(48, now - this.lastT) / 16.7;   // 归一到 60fps 步长
    this.lastT = now;
    // 慢速机自动降粒子数（连续掉帧）
    if (dt > 2.2) { this.slowFrames++; if (this.slowFrames > 45 && this.count > 24) { this.count = Math.round(this.count * 0.7); this.spawn(); this.slowFrames = 0; } }
    else this.slowFrames = Math.max(0, this.slowFrames - 1);

    var ctx = this.ctx, w = this.w, h = this.h;
    ctx.clearRect(0, 0, w, h);
    this.canvas.style.opacity = '1';
    // 颜色取当前色板 accent（换肤即时生效），弱化为低 alpha 光晕
    var accent = (getComputedStyle(document.documentElement).getPropertyValue('--accent') || '#22d3ee').trim();
    var near = Date.now() - this.mouse.t < 2600;
    for (var i = 0; i < this.ps.length; i++) {
      var p = this.ps[i];
      if (near) {
        var dx = this.mouse.x - p.x, dy = this.mouse.y - p.y;
        var d2 = dx * dx + dy * dy;
        if (d2 < 90000 && d2 > 1) {          // 300px 内被指针"吸引"
          var f = 0.0009 * (1 - Math.sqrt(d2) / 300);
          p.vx += dx * f; p.vy += dy * f;
        }
      }
      p.x += p.vx * dt; p.y += p.vy * dt;
      // 阻尼 + 最小漂移，避免越吸越快
      p.vx *= 0.995; p.vy *= 0.995;
      var sp = Math.hypot(p.vx, p.vy);
      if (sp < 0.05) { p.vx += (Math.random() - 0.5) * 0.04; p.vy += (Math.random() - 0.5) * 0.04; }
      if (p.x < -20) p.x = w + 20; else if (p.x > w + 20) p.x = -20;
      if (p.y < -20) p.y = h + 20; else if (p.y > h + 20) p.y = -20;
      var g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.r * 6);
      g.addColorStop(0, accent);
      g.addColorStop(1, 'transparent');
      ctx.globalAlpha = p.o;
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(p.x, p.y, p.r * 6, 0, 6.2832); ctx.fill();
    }
    ctx.globalAlpha = 1;
  },
};
