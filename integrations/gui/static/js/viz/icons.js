/* ============================================================
   viz/icons.js — 内联 SVG 图标集与空态插画（v0.3.3 / T6）
   为什么不用 unicode 符号（◉▦✦✳⑃…）：各平台字体的字重与基线不一致，
   与正文混排时忽大忽小、对齐靠猜；内联 SVG 用 currentColor + 固定网格，
   字重、基线、换肤全部可控，且零网络请求（符合"不引入 CDN"红线）。
   规范：16×16 网格 · stroke-width 1.5 · round 端点 · fill:none · currentColor
   ============================================================ */
'use strict';

var Icons = {
  _p: {
    dashboard: '<path d="M2.2 11.2a6 6 0 0 1 11.6 0"/><path d="M8 11.2 11.2 7.2"/><circle cx="8" cy="11.4" r="1.1"/>',
    memories: '<path d="M8 1.8 14.4 5 8 8.2 1.6 5z"/><path d="m2.2 8 5.8 2.9L13.8 8"/><path d="m2.2 11 5.8 2.9L13.8 11"/>',
    experiences: '<path d="M8 1.6 9.4 6.6 14.4 8 9.4 9.4 8 14.4 6.6 9.4 1.6 8 6.6 6.6z"/>',
    starmap: '<circle cx="3.2" cy="11.6" r="1.7"/><circle cx="8.4" cy="4.6" r="1.9"/><circle cx="13" cy="10.4" r="1.6"/>' +
      '<path d="m4.5 10.4 2.9-4.2M9.7 6.1l2.5 2.9M4.9 11.9l6.6-.9"/>',
    lineage: '<circle cx="4" cy="3.4" r="1.8"/><circle cx="12" cy="3.4" r="1.8"/><circle cx="8" cy="12.6" r="1.8"/>' +
      '<path d="M4 5.2v2.2c0 1.1.9 2 2 2h4c1.1 0 2-.9 2-2V5.2M8 9.4v1.4"/>',
    candidates: '<path d="M8 1.9c2.5 0 4.5 3.5 4.5 6.3A4.5 4.5 0 0 1 8 14.1 4.5 4.5 0 0 1 3.5 8.2C3.5 5.4 5.5 1.9 8 1.9Z"/>' +
      '<path d="m6.4 8.4 1.5-1.1 1.3 1.3 1.6-1.3"/>',
    timeline: '<path d="M2 3.6h12M2 8h12M2 12.4h12"/>' +
      '<circle cx="5.4" cy="3.6" r="1.5" fill="currentColor" stroke="none"/>' +
      '<circle cx="10.2" cy="8" r="1.5" fill="currentColor" stroke="none"/>' +
      '<circle cx="7" cy="12.4" r="1.5" fill="currentColor" stroke="none"/>',
    health: '<path d="M8 13.4S2.2 9.9 2.2 6.3A2.9 2.9 0 0 1 8 4.7a2.9 2.9 0 0 1 5.8 1.6c0 3.6-5.8 7.1-5.8 7.1Z"/>' +
      '<path d="m5.9 7.5 1.5 1.5 2.8-2.8"/>',
    actions: '<circle cx="8" cy="8" r="2.6"/>' +
      '<path d="M8 1.8v2M8 12.2v2M1.8 8h2M12.2 8h2M3.6 3.6l1.4 1.4M11 11l1.4 1.4M12.4 3.6 11 5M5 11l-1.4 1.4"/>',
    bell: '<path d="M8 2.2a3.6 3.6 0 0 0-3.6 3.6c0 3.2-1.5 4.3-1.5 4.3h10.2s-1.5-1.1-1.5-4.3A3.6 3.6 0 0 0 8 2.2Z"/>' +
      '<path d="M6.5 12.2a1.6 1.6 0 0 0 3 0"/>',
    firstaid: '<rect x="1.8" y="4.6" width="12.4" height="8.6" rx="1.6"/><path d="M6 4.6V3.2h4v1.4"/>' +
      '<path d="M8 6.7v4.4M5.8 8.9h4.4"/>',
    refresh: '<path d="M13.2 8A5.2 5.2 0 1 1 11.5 4"/><path d="M13.4 1.8v3.4H10"/>',
    search: '<circle cx="7" cy="7" r="4.4"/><path d="m10.3 10.3 4 4"/>',
    close: '<path d="m3.4 3.4 9.2 9.2M12.6 3.4l-9.2 9.2"/>',
    copy: '<rect x="5.4" y="5.2" width="8.4" height="9" rx="1.4"/>' +
      '<path d="M10.6 5V3.5c0-.8-.6-1.4-1.4-1.4H3.6c-.8 0-1.4.6-1.4 1.4v6.1c0 .8.6 1.4 1.4 1.4h1.6"/>',
    warn: '<path d="M8 2.3 14.3 13.7H1.7z"/><path d="M8 6.3v3.5M8 11.6v.1"/>',
    check: '<path d="m3 8.6 3.3 3.2L13 4.9"/>',
    star: '<path d="m8 1.9 1.9 3.9 4.3.6-3.1 3 .8 4.3L8 11.7 4.1 13.7l.8-4.3-3.1-3 4.3-.6z"/>',
    external: '<path d="M6.6 3.2H3.6c-.8 0-1.4.6-1.4 1.4v7.8c0 .8.6 1.4 1.4 1.4h7.8c.8 0 1.4-.6 1.4-1.4v-3"/>' +
      '<path d="M9.4 2.4h4.2v4.2M13.4 2.6 7.8 8.2"/>',
    cruise: '<path d="M2.4 6V2.4H6M10 2.4h3.6V6M13.6 10v3.6H10M6 13.6H2.4V10"/>',
    diag: '<rect x="3.4" y="2.6" width="9.2" height="10.8" rx="1.6"/>' +
      '<path d="M6.2 2.6V1.5h3.6v1.1M5.8 6.4h4.4M5.8 9.1h4.4"/>',
    help: '<circle cx="8" cy="8" r="6.2"/><path d="M6.2 6.2a1.9 1.9 0 1 1 2.5 1.9c-.5.2-.7.5-.7 1v.4M8 11.7v.1"/>',
    snow: '<path d="M8 1.8v12.4M3.2 4.4l9.6 6.6M12.8 4.4l-9.6 6.6"/><path d="m8 1.8-1.8 1.7M8 1.8l1.8 1.7M8 14.2l-1.8-1.7M8 14.2l1.8-1.7"/>',
    up: '<path d="M8 13.2V3.2M4.2 6.8 8 3l3.8 3.8"/>',
    down: '<path d="M8 2.8v10M4.2 9.2 8 13l3.8-3.8"/>',
    flow: '<path d="M2.4 8h4.2M9.4 8h4.2"/><circle cx="8" cy="8" r="1.6"/>',
    lock: '<rect x="3.4" y="7" width="9.2" height="6.6" rx="1.5"/><path d="M5.6 7V5.3a2.4 2.4 0 0 1 4.8 0V7"/>',
    palette: '<path d="M8 1.9a6.1 6.1 0 0 0 0 12.2c.9 0 1.5-.7 1.5-1.5 0-.4-.2-.8-.4-1-.3-.3-.4-.6-.4-1 0-.8.7-1.5 1.5-1.5h.9A2.9 2.9 0 0 0 14 6.2C13.6 3.7 11.1 1.9 8 1.9Z"/>' +
      '<circle cx="5.4" cy="6.4" r=".9" fill="currentColor" stroke="none"/><circle cx="8.4" cy="4.8" r=".9" fill="currentColor" stroke="none"/><circle cx="11" cy="6.2" r=".9" fill="currentColor" stroke="none"/>',
  },

  /* 取图标 SVG 串（size 默认 16；class 追加在 svg 上） */
  svg: function (name, size, cls) {
    var p = Icons._p[name];
    if (!p) return '';
    var s = size || 16;
    return '<svg class="ico' + (cls ? ' ' + cls : '') + '" width="' + s + '" height="' + s +
      '" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" ' +
      'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' + p + '</svg>';
  },

  /* ── 空态插画（纯手写 SVG，零依赖；120×86 网格，1.4 stroke，低 alpha 双色） ── */
  illus: {
    lineage:
      '<circle cx="24" cy="26" r="9"/><circle cx="96" cy="26" r="9"/><circle cx="60" cy="66" r="9"/>' +
      '<path d="M33 30c14 6 20 12 20 27" stroke-dasharray="4 5"/>' +
      '<path d="M87 30c-14 6-20 12-20 27" stroke-dasharray="4 5"/>' +
      '<path d="M49 66h-6M77 66h-6" /><path d="m56 61 8 10M64 61l-8 10" opacity=".55"/>',
    candidates:
      '<path d="M60 14c12 0 22 17 22 30a22 22 0 0 1-44 0c0-13 10-30 22-30Z"/>' +
      '<path d="m51 44 7-5 6 6 8-7" opacity=".7"/>' +
      '<path d="M18 74h84" stroke-dasharray="3 6" opacity=".7"/>',
    memories:
      '<path d="M60 16 100 34 60 52 20 34Z"/><path d="m20 48 40 18 40-18" opacity=".75"/>' +
      '<path d="m20 62 40 18 40-18" opacity=".5"/>',
    search: '<circle cx="52" cy="38" r="20"/><path d="m67 53 18 18"/>' +
      '<path d="M45 33a7 7 0 0 1 12 4.4c0 3-4.4 3.4-4.4 6.2" opacity=".7"/><circle cx="52" cy="48" r="1.4" fill="currentColor" stroke="none" opacity=".7"/>',
    timeline: '<path d="M16 68h88"/><path d="M16 48h88" stroke-dasharray="3 6" opacity=".6"/>' +
      '<circle cx="34" cy="48" r="5"/><circle cx="60" cy="68" r="5"/><circle cx="86" cy="68" r="5" opacity=".6"/>',
    actions: '<rect x="26" y="16" width="68" height="56" rx="6"/>' +
      '<path d="M40 34h40M40 46h40M40 58h22" stroke-dasharray="3 5" opacity=".7"/>',
    generic: '<rect x="24" y="24" width="72" height="48" rx="6" stroke-dasharray="5 5"/>' +
      '<path d="M48 48h24" opacity=".6"/>',
  },

  /* 空态块：插画 + 标题 + 说明（替代纯文字 .empty） */
  empty: function (kind, title, hint) {
    var art = Icons.illus[kind] || Icons.illus.generic;
    return '<div class="empty-art">' +
      '<svg class="empty-illus" viewBox="0 0 120 86" fill="none" stroke="currentColor" ' +
      'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + art + '</svg>' +
      (title ? '<div class="empty-title">' + esc(title) + '</div>' : '') +
      (hint ? '<div class="empty-hint">' + hint + '</div>' : '') +
      '</div>';
  },
};
