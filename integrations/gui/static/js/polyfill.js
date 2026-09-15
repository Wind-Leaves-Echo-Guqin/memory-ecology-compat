/* ============================================================
   polyfill.js — 旧 WebView 内核兼容层（T12/G）
   pywebview 原生窗口（宿主主用环境）内核较旧，缺以下 API 时逐个补齐。
   全部为最小实现；有原生 API 时不动。
   ============================================================ */
'use strict';

/* RENDER 注册表必须先于 views/*.js 存在（视图脚本向它挂载渲染函数） */
var RENDER = {};

/* CSS.escape —— focusSkill / 选择器拼接依赖（缺失时星图定位抛错级联失效） */
if (typeof CSS !== 'object') { window.CSS = {}; }
if (typeof CSS.escape !== 'function') {
  CSS.escape = function (s) {
    return String(s).replace(/[^a-zA-Z0-9_\u00A0-\uFFFF-]/g, function (c) {
      return '\\' + c;
    });
  };
}

/* Object.assign */
if (typeof Object.assign !== 'function') {
  Object.assign = function (target) {
    if (target == null) throw new TypeError('Cannot convert undefined or null to object');
    var to = Object(target);
    for (var i = 1; i < arguments.length; i++) {
      var src = arguments[i];
      if (src == null) continue;
      for (var k in src) { if (Object.prototype.hasOwnProperty.call(src, k)) to[k] = src[k]; }
    }
    return to;
  };
}

/* Element.closest */
if (window.Element && !Element.prototype.closest) {
  Element.prototype.closest = function (s) {
    var el = this;
    while (el && el.nodeType === 1) {
      if (el.matches(s)) return el;
      el = el.parentElement || el.parentNode;
    }
    return null;
  };
}

/* Element.matches */
if (window.Element && !Element.prototype.matches) {
  Element.prototype.matches =
    Element.prototype.msMatchesSelector ||
    Element.prototype.webkitMatchesSelector ||
    function (s) {
      var el = this;
      var list = (el.document || el.ownerDocument).querySelectorAll(s);
      for (var i = 0; i < list.length; i++) { if (list[i] === el) return true; }
      return false;
    };
}

/* Number.isFinite / Number.isInteger */
if (typeof Number.isFinite !== 'function') {
  Number.isFinite = function (v) { return typeof v === 'number' && isFinite(v); };
}
if (typeof Number.isInteger !== 'function') {
  Number.isInteger = function (v) { return typeof v === 'number' && isFinite(v) && Math.floor(v) === v; };
}

/* String.prototype.includes / startsWith / endsWith */
if (!String.prototype.includes) {
  String.prototype.includes = function (s, i) {
    return this.indexOf(s, i || 0) !== -1;
  };
}
if (!String.prototype.startsWith) {
  String.prototype.startsWith = function (s, i) { return this.indexOf(s, i || 0) === 0; };
}
if (!String.prototype.endsWith) {
  String.prototype.endsWith = function (s, l) {
    if (l === undefined || l > this.length) l = this.length;
    return this.indexOf(s, l - s.length) !== -1;
  };
}

/* Array.prototype.includes */
if (!Array.prototype.includes) {
  Array.prototype.includes = function (x) { return this.indexOf(x) !== -1; };
}

/* Object.entries / Object.values（图表与视图遍历常用） */
if (typeof Object.entries !== 'function') {
  Object.entries = function (o) {
    return Object.keys(o).map(function (k) { return [k, o[k]]; });
  };
}
if (typeof Object.values !== 'function') {
  Object.values = function (o) {
    return Object.keys(o).map(function (k) { return o[k]; });
  };
}

/* fetch 仅在彻底缺失时警告（正常内核都有；急救模式页不依赖 fetch） */

/* ── 内核能力检测（旧 → 顶栏提示，不强制） ── */
function ecoKernelCheck() {
  try {
    var old = typeof CSS.escape !== 'function' || typeof Promise !== 'function';
    var bd = false;
    try { bd = typeof CSS !== 'undefined' && 'supports' in CSS && CSS.supports('backdrop-filter', 'blur(1px)'); } catch (e) { bd = false; }
    if (old || !bd) {
      var bar = document.createElement('div');
      bar.className = 'kernel-bar';
      bar.innerHTML = '⚠ 当前窗口内核较旧（' + (old ? '缺基础 API' : '无 backdrop-filter') +
        '），部分视觉效果受限。建议复制地址用系统浏览器打开：<a href="#" id="kernel-copy-url">' + location.href + '</a>';
      document.body.insertBefore(bar, document.body.firstChild);
      var a = document.getElementById('kernel-copy-url');
      if (a) a.onclick = function () {
        if (window.ecoCopy) window.ecoCopy(location.href);
        return false;
      };
    }
  } catch (e) { /* 检测失败不阻塞 */ }
}
