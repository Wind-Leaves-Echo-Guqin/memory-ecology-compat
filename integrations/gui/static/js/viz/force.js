/* ============================================================
   viz/force.js — 手写力导向物理引擎（T6，零依赖）
   · 库仑斥力（O(n²)，150+ 节点可接受）+ related/blood 弹簧 + 中心弱引力 + 阻尼
   · requestAnimationFrame 主循环；节点可拖拽（拖拽中钉住）
   · 全部坐标为世界坐标，视图层通过 transform（translate+scale）渲染
   ============================================================ */
'use strict';

function ForceSim() {
  this.nodes = [];      // {id, x, y, vx, vy, fixed, r, payload}
  this.links = [];      // {a, b, k, len}  a/b = node 引用
  this.running = false;
  this.onTick = null;
  this._repulse = 2600;
  this._spring = 0.015;
  this._center = 0.004;
  this._damp = 0.86;
}

ForceSim.prototype.setGraph = function (nodes, links) {
  this.nodes = nodes;
  this.links = links;
  this.reheat();
};

ForceSim.prototype.reheat = function () {
  this.nodes.forEach(function (n) { n.vx = 0; n.vy = 0; });
  this.start();
};

ForceSim.prototype.start = function () {
  if (this.running) return;
  this.running = true;
  var self = this;
  function frame() {
    self.step();
    if (self.onTick) self.onTick();
    if (self.running) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
};

ForceSim.prototype.stop = function () { this.running = false; };

ForceSim.prototype.step = function () {
  var ns = this.nodes, ls = this.links;
  var i, j, n, m;
  // 斥力（库仑平方衰减的简化线性版，O(n²)）
  for (i = 0; i < ns.length; i++) {
    n = ns[i];
    for (j = i + 1; j < ns.length; j++) {
      m = ns[j];
      var dx = n.x - m.x, dy = n.y - m.y;
      var d2 = dx * dx + dy * dy;
      if (d2 < 1) { dx = (Math.random() - 0.5); dy = (Math.random() - 0.5); d2 = dx * dx + dy * dy + 0.01; }
      var d = Math.sqrt(d2);
      var f = this._repulse / d2;
      if (d < (n.r + m.r + 8)) f += 1.2;           // 硬重叠补充推力
      var fx = (dx / d) * f, fy = (dy / d) * f;
      if (!n.fixed) { n.vx += fx; n.vy += fy; }
      if (!m.fixed) { m.vx -= fx; m.vy -= fy; }
    }
  }
  // 弹簧（边：吸引回目标长度）
  for (i = 0; i < ls.length; i++) {
    var L = ls[i], a = L.a, b = L.b;
    var ddx = b.x - a.x, ddy = b.y - a.y;
    var dd = Math.sqrt(ddx * ddx + ddy * ddy) || 0.01;
    var f2 = (dd - L.len) * L.k;
    var fx2 = (ddx / dd) * f2, fy2 = (ddy / dd) * f2;
    if (!a.fixed) { a.vx += fx2; a.vy += fy2; }
    if (!b.fixed) { b.vx -= fx2; b.vy -= fy2; }
  }
  // 中心引力 + 积分
  var kinetic = 0;
  for (i = 0; i < ns.length; i++) {
    n = ns[i];
    if (!n.fixed) {
      n.vx += -n.x * this._center;
      n.vy += -n.y * this._center;
      n.vx *= this._damp; n.vy *= this._damp;
      n.x += Math.max(-14, Math.min(14, n.vx));
      n.y += Math.max(-14, Math.min(14, n.vy));
    }
    kinetic += Math.abs(n.vx) + Math.abs(n.vy);
  }
  // 冷却：动能足够低自动暂停（仍可拖拽再唤醒）
  if (kinetic < 0.8 * ns.length * 0.05) this.running = false;
};
