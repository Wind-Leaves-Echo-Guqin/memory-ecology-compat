/* ============================================================
   bell.js — 全局告警铃铛（T11）
   聚合五源：cron 近7天失败 / 记忆越线 / 悬空引用 / 报告 P0 / 候选>0
   每条 = 描述 + 复制 + 跳转定位；红点数字；90s 轮询。
   ============================================================ */
'use strict';

var Bell = {
  items: [],
  timer: null,

  init: function () {
    var self = this;
    var btn = document.getElementById('bell-btn');
    btn.onclick = function () { self.toggle(); };
    document.addEventListener('click', function (e) {
      var p = document.getElementById('bell-panel');
      if (!p.classList.contains('hidden') &&
          !e.target.closest('#bell-panel') && !e.target.closest('#bell-btn')) {
        p.classList.add('hidden');
      }
    });
    self.refresh();
    self.timer = setInterval(function () { self.refresh(); }, 90000);
  },

  refresh: function () {
    var self = this;
    EcoApi.get('/api/overview').then(function (d) {
      self.build(d);
    }).catch(function () { /* fail-open：铃铛失败不影响页面 */ });
  },

  build: function (d) {
    var self = this;
    var items = [];
    // ① cron 近 7 天失败（executions.db 口径）
    var fails = (d.cron && d.cron.recent_fails) || [];
    if (fails.length) {
      var txt = fails.map(function (f) { return f.job + '×' + f.n; }).join(' · ');
      items.push({ sev: 'bad', src: 'cron', text: '近 7 天 cron 失败：' + txt,
        copy: '近 7 天 cron 失败（口径 executions.db）：\n' + fails.map(function (f) { return f.job + ' ×' + f.n; }).join('\n'),
        goto: 'cron' });
    }
    // ② 记忆越线
    var wm = d.watermark || {};
    if (wm.chars > wm.quota) {
      items.push({ sev: 'bad', src: 'mem', text: 'L1 记忆越线：' + wm.chars + ' > ' + wm.quota + '（超 ' + (wm.chars - wm.quota) + '）',
        copy: '记忆水位越线：MEMORY.md ' + wm.chars + ' 字符 > 管理线 ' + wm.quota + '（超 ' + (wm.chars - wm.quota) + '）。可在驾驶舱一键挤出。',
        goto: 'dashboard' });
    }
    // ③ 悬空引用
    var dangling = 0;
    (function () {
      var seen = {};
      (d.counts && d.counts._dangling != null) ? (dangling = d.counts._dangling) : 0;
    })();
    // 悬空引用数从技能视图数据取不到时由 starmap 视图补报（此处先留占位轮询）
    // ④ 报告 P0
    self._p0(items);
    // ⑤ 候选
    var cd = (d.candidates && d.candidates.by_kind) || {};
    var nCand = (cd.skill || 0) + (cd.profile || 0) + (cd.experience || 0);
    if (nCand > 0) {
      items.push({ sev: 'warn', src: 'cand', text: '待处理候选 ' + nCand + ' 项（技能 ' + (cd.skill || 0) + ' · 画像 ' + (cd.profile || 0) + ' · 经验 ' + (cd.experience || 0) + '）',
        copy: '候选池：技能 ' + (cd.skill || 0) + ' / 画像 ' + (cd.profile || 0) + ' / 经验 ' + (cd.experience || 0),
        goto: 'candidates' });
    }
    self._dangling(items);
    self.items = items;
    var badge = document.getElementById('bell-badge');
    if (items.length) {
      badge.textContent = items.length > 99 ? '99+' : items.length;
      badge.classList.remove('hidden');
    } else {
      badge.classList.add('hidden');
    }
  },

  _p0: function (items) {
    EcoApi.get('/api/health').then(function (h) {
      var body = (h.report || {}).body || '';
      var lines = body.split('\n').filter(function (L) { return /\| P0 \||P0\s|/.test(L) && /\bP0\b/.test(L) && L.trim().length > 12; }).slice(0, 3);
      lines.forEach(function (L) {
        var t = L.trim().replace(/^[|#*\-\s]+/, '').slice(0, 120);
        items.push({ sev: 'bad', src: 'report', text: '体检 P0：' + t,
          copy: '体检报告 P0 项：' + t, goto: 'health' });
      });
      Bell._renderBadge(items);
    }).catch(function () {});
  },

  _dangling: function (items) {
    EcoApi.get('/api/skills').then(function (d) {
      var names = {};
      d.skills.forEach(function (s) { names[s.name] = 1; });
      var dang = {};
      d.skills.forEach(function (s) {
        (s.dangling || []).forEach(function (r) { if (!names[r]) dang[r] = 1; });
      });
      var n = Object.keys(dang).length;
      if (n) {
        items.push({ sev: 'warn', src: 'dangling', text: '技能悬空引用 ' + n + ' 个：' + Object.keys(dang).slice(0, 3).join('、') + (n > 3 ? '…' : ''),
          copy: '悬空引用（引用了不存在的技能）：\n' + Object.keys(dang).join('\n'),
          goto: 'starmap' });
      }
      Bell._renderBadge(items);
    }).catch(function () {});
  },

  _renderBadge: function (items) {
    // 异步补源（P0/悬空）到达后重算徽标；面板开着则同步重渲染
    var badge = document.getElementById('bell-badge');
    if (items.length) {
      badge.textContent = items.length > 99 ? '99+' : items.length;
      badge.classList.remove('hidden');
    } else {
      badge.classList.add('hidden');
    }
    Bell.items = items;
    Bell._notify(items);
    var panel = document.getElementById('bell-panel');
    if (!panel.classList.contains('hidden')) Bell.renderPanel();
  },

  /* ── 桌面通知（T7/B2，仅浏览器端；后台标签页才推送，前台不打扰） ── */
  _sig: null,
  _seen: null,
  _notify: function (items) {
    try {
      if (typeof Notification === 'undefined') return;
      if (window.__ecoDesktop) return;                       // 桌面窗口不推送
      var sig = items.map(function (i) { return i.sev + '|' + i.text; }).join('\n');
      if (this._sig === null) { this._sig = sig; this._markSeen(items); return; }  // 首次只记基线
      if (sig === this._sig) return;
      this._sig = sig;
      var seen = this._seen || {};
      var fresh = items.filter(function (i) { return !seen[i.sev + '|' + i.text]; });
      this._markSeen(items);
      if (!fresh.length) return;
      if (!document.hidden) return;                          // 页面前台时静默（只更新铃铛）
      var self = this;
      var send = function () {
        try {
          var bad = fresh.filter(function (x) { return x.sev === 'bad'; });
          var head = bad.length ? '⚠ 新增严重告警 ' + bad.length + ' 条' : '新增告警 ' + fresh.length + ' 条';
          var n = new Notification('记忆生态 · 观测舱', {
            body: head + '：' + fresh[0].text.slice(0, 90), tag: 'eco-bell',
          });
          n.onclick = function () { try { window.focus(); } catch (e) {} };
        } catch (e) { /* 通知失败不影响铃铛 */ }
      };
      if (Notification.permission === 'granted') send();
      else if (Notification.permission === 'default') Notification.requestPermission().then(function (p) { if (p === 'granted') send(); });
    } catch (e) { /* fail-open */ }
  },
  _markSeen: function (items) {
    var m = {};
    items.forEach(function (i) { m[i.sev + '|' + i.text] = 1; });
    this._seen = m;
  },

  toggle: function () {
    var p = document.getElementById('bell-panel');
    if (p.classList.contains('hidden')) { this.renderPanel(); p.classList.remove('hidden'); }
    else p.classList.add('hidden');
  },

  renderPanel: function () {
    var p = document.getElementById('bell-panel');
    if (!this.items.length) {
      p.innerHTML = '<div class="bell-head">全局告警</div><div class="bell-empty">✅ 暂无告警——cron / 水位 / 悬空引用 / 报告 P0 / 候选均正常。</div>';
      return;
    }
    var html = '<div class="bell-head">全局告警 <span class="faint">' + this.items.length + ' 条</span></div>';
    this.items.forEach(function (it, i) {
      html += '<div class="bell-item ' + it.sev + '">' +
        '<span class="dot ' + (it.sev === 'bad' ? 'bad' : 'warn') + '"></span>' +
        '<span class="bell-text">' + esc(it.text) + '</span>' +
        '<span class="bell-ops"><a data-bcopy="' + i + '">复制</a>' +
        (it.goto ? ' · <a data-bgoto="' + it.goto + '">去处理</a>' : '') + '</span></div>';
    });
    p.innerHTML = html;
    $$('[data-bcopy]', p).forEach(function (a) {
      a.onclick = function () { ecoCopy(Bell.items[+a.dataset.bcopy].copy || Bell.items[+a.dataset.bcopy].text); };
    });
    $$('[data-bgoto]', p).forEach(function (a) {
      a.onclick = function () { p.classList.add('hidden'); goto(a.dataset.bgoto); };
    });
  }
};
