/* ============================================================
   api.js — fetch 统一封装（T12 稳定性）
   · 1 次退避重试（200ms）
   · 连续 3 次失败 → 急救模式页（firstaid.js 渲染）
   · 成功响应落 localStorage（快照降级），失败时回放旧数据 + 顶部黄条
   旧内核无 fetch → 直接进急救模式页。
   ============================================================ */
'use strict';

var EcoApi = {
  failStreak: 0,
  dead: false,
  cacheKey: 'eco_snapshot_cache',

  get: function (path) {
    var self = this;
    if (typeof fetch !== 'function') { self._onDead(); throw new Error('内核无 fetch'); }
    return self._attempt(path).catch(function () {
      return new Promise(function (res, rej) {
        setTimeout(function () {
          self._attempt(path).then(res, function (e) { rej(e); });
        }, 200);
      });
    }).then(function (j) {
      self.failStreak = 0;
      self._saveSnapshot(path, j);
      return j;
    }, function (e) {
      self.failStreak++;
      if (self.failStreak >= 3) self._onDead();
      throw e;
    });
  },

  _attempt: function (path) {
    return fetch(path).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).then(function (j) {
      if (!j.ok) throw new Error(j.error || '请求失败');
      return j;
    });
  },

  post: function (path, body) {
    var self = this;
    if (typeof fetch !== 'function') { self._onDead(); throw new Error('内核无 fetch'); }
    return fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().catch(function () { throw new Error('HTTP ' + r.status); });
    });
  },

  /* ── 快照降级 ── */
  _saveSnapshot: function (path, data) {
    try {
      if (path.indexOf('/api/overview') === 0 || path.indexOf('/api/meta') === 0) {
        var all = this._load();
        all[path] = { ts: new Date().toISOString(), data: data };
        localStorage.setItem(this.cacheKey, JSON.stringify(all));
      }
    } catch (e) { /* 存储满等忽略 */ }
  },
  _load: function () {
    try { return JSON.parse(localStorage.getItem(this.cacheKey) || '{}'); }
    catch (e) { return {}; }
  },
  snapshot: function (path) {
    var hit = this._load()[path];
    return hit ? hit : null;
  },

  _onDead: function () {
    if (this.dead) return;
    this.dead = true;
    if (window.EcoFirstAidMode) EcoFirstAidMode();
  }
};

/* ── 复制工具（三级兜底：clipboard → execCommand → 手动全选弹窗） ──
   旧内核窗口 clipboard 可能被拒、execCommand 也可能失败——第三级弹出
   可全选的文本框，保证"一定能复制"（用户红线：报错必须能复制）。 */
window.ecoCopy = function (text, okMsg) {
  var done = function () { if (window.toast) toast((okMsg || '已复制到剪贴板'), true); };
  var fail = function () { _manualCopyDialog(text, okMsg); };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done, function () { _fallbackCopy(text) ? done() : fail(); });
  } else {
    _fallbackCopy(text) ? done() : fail();
  }
};
function _fallbackCopy(text) {
  try {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;left:-999px;top:0';
    document.body.appendChild(ta);
    ta.focus(); ta.select();
    var ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch (e) { return false; }
}
function _manualCopyDialog(text, okMsg) {
  var m = document.getElementById('gate-modal');
  var mask = document.getElementById('gate-mask');
  m.innerHTML = '<div class="gate-box"><h3>📋 请手动复制</h3>' +
    '<div class="empty-ok">窗口剪贴板不可用（旧内核常见）。下面的文本已全选：<br>' +
    '按 <b>Ctrl+C</b> 复制，或右键→复制。</div>' +
    '<textarea id="mcopy-ta" class="dry-out" style="width:100%;height:200px;margin-top:8px"></textarea>' +
    '<div class="gate-btns" style="margin-top:10px">' +
    '<button class="gate-go light" id="mcopy-again">再试自动复制</button>' +
    '<span style="flex:1"></span>' +
    '<button class="ghost-btn" id="mcopy-close">关闭</button></div></div>';
  m.classList.remove('hidden');
  mask.classList.remove('hidden');
  var ta = document.getElementById('mcopy-ta');
  ta.value = text;
  ta.focus(); ta.select();
  $('#mcopy-close').onclick = function () { m.classList.add('hidden'); mask.classList.add('hidden'); };
  $('#mcopy-again').onclick = function () {
    ta.focus(); ta.select();
    if (_fallbackCopy(text)) {
      m.classList.add('hidden'); mask.classList.add('hidden');
      if (window.toast) toast((okMsg || '已复制到剪贴板'), true);
    } else if (window.toast) toast('仍失败——请在文本框中 Ctrl+C', false);
  };
}
