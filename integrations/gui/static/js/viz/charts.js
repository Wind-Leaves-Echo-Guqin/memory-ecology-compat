/* ============================================================
   viz/charts.js — 时间线可视化（T9）：泳道图 / 按日直方图 / 演化回放
   数据源：/api/logs 的 rows（[{table, ts, action, target, note}]），ts 为 ISO 文本。
   零依赖手写 SVG；遵循 prefers-reduced-motion（绘制动画由 CSS 控制）。
   ============================================================ */
'use strict';

var Charts = {
  gateCol: { gate: 'var(--gate1)', quota: 'var(--gate2)', distill: 'var(--gate3)', review: 'var(--gate4)' },
  gateName: { gate: '门① 写入整合', quota: '门② 巩固/配额', distill: '门③ 画像蒸馏', review: '门④ 复核' },

  /* ── ① 泳道图：每门一行，事件为色块，悬停 tooltip ── */
  swimlane: function (rows, onPick) {
    var self = this;
    if (!rows.length) return '<div class="empty">暂无事件</div>';
    var gates = ['gate', 'quota', 'distill', 'review'];
    var min = rows[rows.length - 1].ts, max = rows[0].ts;   // rows 已按 ts 降序
    var t0 = new Date(min).getTime() || 0, t1 = new Date(max).getTime() || t0 + 1;
    if (t1 - t0 < 60000) t1 = t0 + 60000;
    var W = 1000, laneH = 46, padL = 96, padR = 16, top = 18;
    var H = top + gates.length * laneH + 26;
    var X = function (ts) { return padL + (new Date(ts).getTime() - t0) / (t1 - t0) * (W - padL - padR); };
    var svg = '<svg class="swim" viewBox="0 0 ' + W + ' ' + H + '" style="width:100%">';
    // 网格（每天一条）
    var dayCursor = new Date(t0); dayCursor.setHours(0, 0, 0, 0);
    while (dayCursor.getTime() <= t1) {
      var gx = X(dayCursor.toISOString());
      if (gx >= padL) svg += '<line x1="' + gx + '" y1="' + top + '" x2="' + gx + '" y2="' + (H - 22) +
        '" stroke="var(--line)" stroke-dasharray="2 4"/>' +
        '<text x="' + (gx + 3) + '" y="' + (H - 8) + '" font-size="9">' + dayCursor.toISOString().slice(5, 10) + '</text>';
      dayCursor.setDate(dayCursor.getDate() + 1);
    }
    gates.forEach(function (g, i) {
      var y = top + i * laneH + laneH / 2;
      svg += '<text x="8" y="' + (y + 3) + '" font-size="10.5">' + esc(self.gateName[g]) + '</text>' +
        '<line x1="' + padL + '" y1="' + y + '" x2="' + (W - padR) + '" y2="' + y + '" stroke="var(--line)"/>';
    });
    rows.forEach(function (r, idx) {
      var gi = gates.indexOf(r.table);
      if (gi < 0) return;
      var y = top + gi * laneH + laneH / 2;
      var x = X(r.ts);
      svg += '<g class="swim-ev" data-ev="' + idx + '" style="cursor:pointer">' +
        '<rect x="' + (x - 2.2) + '" y="' + (y - 7) + '" width="4.4" height="14" rx="2" fill="' + self.gateCol[r.table] + '"/>' +
        '<rect x="' + (x - 7) + '" y="' + (y - 10) + '" width="14" height="20" fill="transparent"/></g>';
    });
    svg += '</svg><div class="swim-tip hidden" id="swim-tip"></div>';
    // 交互绑定延迟到挂载后（渲染函数返回字符串，视图层调 Charts.bindSwim）
    Charts._swimRows = rows; Charts._swimPick = onPick || null;
    return svg;
  },

  bindSwim: function (container) {
    var rows = Charts._swimRows, pick = Charts._swimPick;
    $$('.swim-ev', container).forEach(function (g) {
      g.addEventListener('mouseenter', function (e) {
        var r = rows[+g.dataset.ev];
        var tip = document.getElementById('swim-tip');
        if (!r || !tip) return;
        tip.classList.remove('hidden');
        tip.innerHTML = '<b>' + esc(r.action) + '</b> ' + esc(r.ts.slice(5, 16).replace('T', ' ')) +
          '<br>' + esc(r.target || '') + (r.note ? '<br><span class="faint">' + esc(r.note.slice(0, 80)) + '</span>' : '');
        var box = container.getBoundingClientRect();
        var el = g.getBoundingClientRect();
        tip.style.left = Math.min(el.left - box.left + 12, box.width - 260) + 'px';
        tip.style.top = (el.top - box.top - 8) + 'px';
      });
      g.addEventListener('mouseleave', function () {
        var tip = document.getElementById('swim-tip');
        if (tip) tip.classList.add('hidden');
      });
      g.addEventListener('click', function () {
        var r = rows[+g.dataset.ev];
        if (r && pick) pick(r);
      });
    });
  },

  /* ── ② 按日直方图：每天各门动作数堆叠柱 ── */
  histogram: function (rows) {
    var self = this;
    if (!rows.length) return '<div class="empty">暂无事件</div>';
    var days = {}, order = [];
    rows.forEach(function (r) {
      var d = r.ts.slice(0, 10);
      if (!days[d]) { days[d] = { gate: 0, quota: 0, distill: 0, review: 0 }; order.push(d); }
      if (days[d][r.table] != null) days[d][r.table]++;
    });
    order.sort();
    var W = 1000, H = 150, padL = 40, padB = 22, padT = 12;
    var maxN = 1;
    order.forEach(function (d) {
      var s = days[d].gate + days[d].quota + days[d].distill + days[d].review;
      if (s > maxN) maxN = s;
    });
    var bw = Math.min(34, (W - padL - 10) / order.length - 4);
    var svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%">';
    svg += '<line x1="' + padL + '" y1="' + (H - padB) + '" x2="' + (W - 8) + '" y2="' + (H - padB) + '" stroke="var(--line)"/>';
    order.forEach(function (d, i) {
      var x = padL + i * ((W - padL - 10) / order.length) + 2;
      var y = H - padB;
      var total = 0;
      ['gate', 'quota', 'distill', 'review'].forEach(function (g) {
        var n = days[d][g];
        if (!n) return;
        var h = n / maxN * (H - padB - padT);
        y -= h;
        svg += '<rect x="' + x + '" y="' + y + '" width="' + bw + '" height="' + h + '" fill="' + self.gateCol[g] + '" opacity=".82">' +
          '<title>' + d + ' ' + self.gateName[g] + '：' + n + '</title></rect>';
        total += n;
      });
      if (order.length <= 14 || i % Math.ceil(order.length / 14) === 0)
        svg += '<text x="' + (x + bw / 2) + '" y="' + (H - 8) + '" font-size="9" text-anchor="middle">' + d.slice(5) + '</text>' +
          '<text x="' + (x + bw / 2) + '" y="' + (y - 3) + '" font-size="9" text-anchor="middle" fill="var(--ink3)">' + total + '</text>';
    });
    svg += '</svg>';
    return svg;
  },

  /* ── ③ 演化回放：时间轴滑块 + 播放/暂停 + 倍速 + 按日快进 ── */
  replay: function (rows, mountEl, onEvent) {
    var self = this;
    if (!rows.length) { mountEl.innerHTML = '<div class="empty">暂无可回放事件</div>'; return; }
    // 升序时间流
    var flow = rows.slice().sort(function (a, b) { return a.ts.localeCompare(b.ts); });
    var days = [];
    flow.forEach(function (r) {
      var d = r.ts.slice(0, 10);
      if (!days.length || days[days.length - 1] !== d) days.push(d);
    });
    var idx = -1, playing = false, speed = 1, dayMode = false, timer = null;
    mountEl.innerHTML =
      '<div class="rp-ctl">' +
      '<button id="rp-play" class="ghost-btn">▶ 播放</button>' +
      '<button id="rp-day" class="ghost-btn" title="按日快进">⏭ 按日</button>' +
      '<div class="pills" id="rp-speed"><button data-s="1" class="on">1×</button><button data-s="4">4×</button><button data-s="16">16×</button></div>' +
      '<input id="rp-slider" type="range" min="0" max="' + (flow.length - 1) + '" value="0" style="flex:1">' +
      '<span id="rp-pos" class="faint" style="min-width:150px;text-align:right">—</span>' +
      '</div>' +
      '<div id="rp-event" class="rp-event"><span class="faint">拖动滑块或点播放——逐事件点亮生态演化。</span></div>';
    var slider = $('#rp-slider', mountEl), posEl = $('#rp-pos', mountEl), evEl = $('#rp-event', mountEl);

    function show(i) {
      idx = Math.max(0, Math.min(flow.length - 1, i));
      slider.value = idx;
      var r = flow[idx];
      posEl.textContent = r.ts.slice(0, 16).replace('T', ' ') + ' · ' + (idx + 1) + '/' + flow.length;
      evEl.innerHTML = '<i class="gdot" style="background:' + self.gateCol[r.table] + '"></i>' +
        '<b>' + esc(r.action) + '</b> <span class="tag">' + esc(self.gateName[r.table]) + '</span> ' +
        esc(r.target || '') + (r.note ? ' <span class="faint">' + esc(r.note.slice(0, 100)) + '</span>' : '');
      if (onEvent) onEvent(r, idx / Math.max(1, flow.length - 1));
    }
    function step() {
      if (!playing) return;
      if (idx >= flow.length - 1) { pause(); return; }
      show(idx + 1);
      timer = setTimeout(step, Math.max(30, 420 / speed));
    }
    function play() {
      playing = true; $('#rp-play', mountEl).textContent = '⏸ 暂停'; step();
    }
    function pause() {
      playing = false;
      if (timer) clearTimeout(timer);
      $('#rp-play', mountEl).textContent = '▶ 播放';
    }
    $('#rp-play', mountEl).onclick = function () { playing ? pause() : play(); };
    $('#rp-day', mountEl).onclick = function () {
      pause();
      var cur = flow[idx].ts.slice(0, 10), j = idx;
      while (j < flow.length - 1 && flow[j + 1].ts.slice(0, 10) === cur) j++;
      show(Math.min(j + 1, flow.length - 1));
    };
    $$('#rp-speed button', mountEl).forEach(function (b) {
      b.onclick = function () {
        speed = +b.dataset.s;
        $$('#rp-speed button', mountEl).forEach(function (x) { x.classList.toggle('on', x === b); });
      };
    });
    slider.oninput = function () { pause(); show(+slider.value); };
    show(0);
  }
};
