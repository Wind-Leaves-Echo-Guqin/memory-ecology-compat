/* ============================================================
   viz/force.js — 力导向物理引擎（T10，零依赖手写，D3-force 同级）
   物理项（速度-Verlet 积分，参数对齐 D3-force 默认量级）：
     · 多体力（电荷斥力）：默认 charge=-300，O(n²)，节点少时最稳
     · 链接力：弹簧按目标长度收敛，强度随节点度数自适应
     · 碰撞力：半径 + 间距硬约束（避免叠字/叠圆）
     · 中心引力（向 viewBox 中心的弱弹簧）+ 定心（可选固定坐标）
     · 速度衰减 0.6 / alpha 退火（alphaDecay=0.02，alphaMin≈0.001）
   · requestAnimationFrame 主循环；onTick 回调渲染
   · 拖拽惯性：外部直接改 x/y（钉住）时由本引擎反推速度，松手后自然滑行
   · 初始摆位用向日葵（phyllotaxis）螺旋 → 入场即"从中心爆散"
   · reduced-motion 或动画强度 low 时同步收敛（不打 rAF 帧）
   ============================================================ */
'use strict';

function ForceSim(opts) {
  opts = opts || {};
  this.nodes = [];            // {id, x, y, vx, vy, fixed, r, payload}
  this.links = [];            // {a, b, k, len, kind}  a/b = 节点引用
  this.running = false;
  this.onTick = null;
  this.alpha = 1;
  this.alphaTarget = 0;
  /* 物理参数（可被调用方覆盖） */
  this.charge = opts.charge != null ? opts.charge : -300;
  this.linkDistance = opts.linkDistance != null ? opts.linkDistance : 120;
  this.linkStrength = opts.linkStrength != null ? opts.linkStrength : 0.09;
  this.collisionPad = opts.collisionPad != null ? opts.collisionPad : 8;
  this.centerStrength = opts.centerStrength != null ? opts.centerStrength : 0.012;
  this.velocityDecay = opts.velocityDecay != null ? opts.velocityDecay : 0.6;
  this.alphaDecay = opts.alphaDecay != null ? opts.alphaDecay : 0.02;
  this.alphaMin = opts.alphaMin != null ? opts.alphaMin : 0.001;
  this.width = opts.width || 900;
  this.height = opts.height || 600;
  this.collapse = opts.collapse != null ? opts.collapse : 0.25;   // 入场向心收缩系数
  this.instant = !!opts.instant;   // true=同步收敛（低动画强度/减少动效）
  this._last = null;               // 用于反推被拖拽节点的速度
  /* 兼容旧调用方读取的字段名 */
  this._repulse = -this.charge;
  this._spring = this.linkStrength;
  this._center = this.centerStrength;
  this._damp = 1 - this.velocityDecay;
}

ForceSim.prototype.setGraph = function (nodes, links) {
  this.nodes = nodes;
  this.links = links;
  // 度数（链接强度自适应：连线多的节点被拉得更紧）
  var deg = {};
  links.forEach(function (L) {
    deg[L.a.id] = (deg[L.a.id] || 0) + 1;
    deg[L.b.id] = (deg[L.b.id] || 0) + 1;
  });
  this._deg = deg;
  this._seed();
  this.reheat();
};

/* 初值处理：
   ① 无坐标的节点 → 向日葵螺旋摆位（不重叠）
   ② 已有坐标的节点 → 整图质心平移到视图中心，并按 collapse 系数向心收缩
      （入场即"从中心爆散展开"；同时修正调用方坐标系与 viewBox 不一致导致的偏置） */
ForceSim.prototype._seed = function () {
  var n = this.nodes.length;
  if (!n) return;
  var cx = this.width / 2, cy = this.height / 2;
  var sx = 0, sy = 0, cnt = 0;
  this.nodes.forEach(function (d) {
    if (isFinite(d.x) && isFinite(d.y)) { sx += d.x; sy += d.y; cnt++; }
  });
  var mx = cnt ? sx / cnt : cx, my = cnt ? sy / cnt : cy;
  var radius = Math.min(this.width, this.height) * 0.05;
  var golden = Math.PI * (3 - Math.sqrt(5));
  var collapse = this.collapse;
  this.nodes.forEach(function (d, i) {
    if (!isFinite(d.x) || !isFinite(d.y)) {
      var r = radius * Math.sqrt(i + 0.5), a = golden * i;
      d.x = cx + r * Math.cos(a);
      d.y = cy + r * Math.sin(a);
    } else {
      d.x = cx + (d.x - mx) * collapse;
      d.y = cy + (d.y - my) * collapse;
    }
    d.vx = 0; d.vy = 0;
    if (!d.fixed) d.fixed = false;
  });
};

ForceSim.prototype.center = function (w, h) { this.width = w; this.height = h; };
ForceSim.prototype.size = function (w, h) { this.width = w; this.height = h; };

ForceSim.prototype.reheat = function (a) {
  this.alpha = a != null ? a : 1;
  this.alphaTarget = 0;
  if (this.instant) { this.settle(300); if (this.onTick) this.onTick(); return; }
  this.start();
};

ForceSim.prototype.start = function () {
  if (this.running) return;
  this.running = true;
  var self = this;
  this._lastT = 0;
  function frame(t) {
    if (!self.running) return;
    // 按真实时间衰减：不同刷新率（60Hz/120Hz/无 vsync）下退火时长一致
    var t0 = t || (performance.now ? performance.now() : Date.now());
    var dt = self._lastT ? Math.min(80, Math.max(1, t0 - self._lastT)) : 16.7;
    self._lastT = t0;
    self.step(dt / 16.7);
    if (self.onTick) self.onTick();
    if (self.alpha < self.alphaMin && self.alphaTarget === 0) { self.running = false; return; }
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
};

ForceSim.prototype.stop = function () { this.running = false; };

/* 同步收敛（不打 rAF 帧）：低动画强度 / prefers-reduced-motion 下使用 */
ForceSim.prototype.settle = function (steps) {
  var n = steps || 300;
  for (var i = 0; i < n; i++) {
    this.step();
    if (this.alpha < this.alphaMin && this.alphaTarget === 0) break;
  }
};

/* 拖拽支持（供视图层调用；也可只用 x/y 直接改，引擎会反推速度） */
ForceSim.prototype.dragStart = function (d) { d.fixed = true; this.reheat(0.3); };
ForceSim.prototype.dragMove = function (d, x, y) { d.x = x; d.y = y; d.vx = 0; d.vy = 0; this.reheat(0.3); };
ForceSim.prototype.dragEnd = function (d) { if (d) d.fixed = false; this.reheat(0.2); };

ForceSim.prototype.step = function (dtf) {
  var ns = this.nodes, ls = this.links, i, j, n, m;
  var dt = dtf && dtf > 0 ? dtf : 1;   // 时间步长（1 = 一个 60Hz 帧）
  var a = this.alpha;
  var cx = this.width / 2, cy = this.height / 2;
  // 外部拖拽（钉住节点被直接改坐标）→ 反推速度，松手后带惯性滑行
  if (this._last) {
    for (i = 0; i < ns.length; i++) {
      n = ns[i];
      var p = this._last[i];
      if (p && n.fixed) { n.vx = (n.x - p.x) * 0.6; n.vy = (n.y - p.y) * 0.6; }
    }
  }

  /* ① 多体力：库仑斥力（O(n²)；节点规模百级，最稳且无四叉树误差）
        w = charge·alpha / d²（D3 同式）；距离趋零时用 jiggle 随机分离 */
  var dMin2 = 1;   // distanceMin²
  for (i = 0; i < ns.length; i++) {
    n = ns[i];
    for (j = i + 1; j < ns.length; j++) {
      m = ns[j];
      var dx = m.x - n.x, dy = m.y - n.y;
      var d2 = dx * dx + dy * dy;
      if (d2 < dMin2) {
        // 重叠：随机方向分离，避免对称死锁
        dx = (Math.random() - 0.5) * 2; dy = (Math.random() - 0.5) * 2;
        d2 = dx * dx + dy * dy + 0.01;
      }
      var d = Math.sqrt(d2);
      var f = this.charge * a * 12 / d2;   // ×12 对齐 D3 默认 -30 量级的实际手感
      var fx = (dx / d) * f, fy = (dy / d) * f;
      if (!n.fixed) { n.vx += fx; n.vy += fy; }
      if (!m.fixed) { m.vx -= fx; m.vy -= fy; }
    }
  }

  /* ② 链接力：弹簧（按度数自适应强度，目标长度取边自带 len 或全局 linkDistance） */
  for (i = 0; i < ls.length; i++) {
    var L = ls[i], A = L.a, B = L.b;
    var lx = B.x - A.x, ly = B.y - A.y;
    var dist = Math.sqrt(lx * lx + ly * ly) || 0.01;
    var target = L.len != null ? L.len : this.linkDistance;
    var strength = (L.k != null ? L.k : this.linkStrength) *
      (1 + 0.35 * Math.min(4, Math.max(0, (this._deg && this._deg[A.id] || 1) - 1)));
    var force = (dist - target) / dist * a * strength;
    var lfx = lx * force, lfy = ly * force;
    if (!A.fixed) { A.vx += lfx; A.vy += lfy; }
    if (!B.fixed) { B.vx -= lfx; B.vy -= lfy; }
  }

  /* ③ 碰撞力：半径 + 间距硬约束（迭代两次，避免叠圆叠字） */
  for (var it = 0; it < 2; it++) {
    for (i = 0; i < ns.length; i++) {
      n = ns[i];
      for (j = i + 1; j < ns.length; j++) {
        m = ns[j];
        var ox = m.x - n.x, oy = m.y - n.y;
        var od = Math.sqrt(ox * ox + oy * oy) || 0.01;
        var min = (n.r || 4) + (m.r || 4) + this.collisionPad;
        if (od < min) {
          var push = (min - od) / od * a * 0.5;
          var px = ox * push, py = oy * push;
          if (!n.fixed) { n.vx -= px; n.vy -= py; }
          if (!m.fixed) { m.vx += px; m.vy += py; }
        }
      }
    }
  }

  /* ④ 中心引力 + 积分（速度-Verlet 的简化式：v 先阻尼再位移） */
  var decay = 1 - this.velocityDecay * (1 - 0.4 * (1 - a));
  for (i = 0; i < ns.length; i++) {
    n = ns[i];
    if (n.fixed) continue;
    n.vx += (cx - n.x) * this.centerStrength * a;
    n.vy += (cy - n.y) * this.centerStrength * a;
    n.vx *= decay;
    n.vy *= decay;
    n.x += Math.max(-30, Math.min(30, n.vx));
    n.y += Math.max(-30, Math.min(30, n.vy));
  }

  // 记录本帧位置（供下一帧反推拖拽速度）
  if (!this._last || this._last.length !== ns.length) this._last = [];
  for (i = 0; i < ns.length; i++) {
    n = ns[i];
    this._last[i] = this._last[i] || { x: n.x, y: n.y };
    this._last[i].x = n.x; this._last[i].y = n.y;
  }
  // alpha 退火（D3 同式，但按真实时间步长 → 刷新率无关；约 5.7s 收敛）
  this.alpha += (this.alphaTarget - this.alpha) * Math.min(1, this.alphaDecay * dt);
  if (this.alphaTarget === 0 && this.alpha < this.alphaMin) this.alpha = 0;
};
