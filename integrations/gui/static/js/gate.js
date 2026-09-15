/* ============================================================
   gate.js — 确认闸门组件（T5/C3，全局统一）
   流程：按钮 → 弹窗（动作名/影响对象/回滚方式）→ 按钮下方红色小字警告
        → 高风险需勾选"我已知晓风险" → "不再提醒"勾选（localStorage 按动作记忆）
   风险分级：light=轻确认（单次弹窗）/ strong=强确认（红字+勾选）
   任何写操作必须经此组件，未过闸门按钮不可达写接口（红线）。
   ============================================================ */
'use strict';

var Gate = {
  _pending: null,   // {action, params, onConfirm}

  muteKey: function (action) { return 'eco_gate_mute_' + action; },
  isMuted: function (action) {
    try { return localStorage.getItem(this.muteKey(action)) === '1'; } catch (e) { return false; }
  },
  resetAll: function () {
    try {
      var del = [];
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.indexOf('eco_gate_mute_') === 0) del.push(k);
      }
      del.forEach(function (k) { localStorage.removeItem(k); });
      return del.length;
    } catch (e) { return 0; }
  },

  /* action: 动作名（后端 /api/action/<name>）；spec: {name,risk,impact,rollback}
     params: POST 参数；onConfirm: 闸门通过后回调（function(params)） */
  ask: function (action, spec, params, onConfirm) {
    var self = this;
    params = params || {};
    // "不再提醒"已勾选 → 直接执行（轻/强一致语义：跳过弹窗）
    if (self.isMuted(action)) { onConfirm(params); return; }
    self._pending = { action: action, params: params, onConfirm: onConfirm };
    var strong = (spec.risk === 'strong');
    var m = document.getElementById('gate-modal');
    var mask = document.getElementById('gate-mask');
    m.innerHTML =
      '<div class="gate-box' + (strong ? ' strong' : '') + '">' +
      '<h3>' + (strong ? '⛔ ' : '') + esc(spec.name || action) + '</h3>' +
      '<div class="gate-rows">' +
      '<span>影响对象</span><span>' + esc(spec.impact || '—') + '</span>' +
      '<span>回滚方式</span><span>' + esc(spec.rollback || '—') + '</span>' +
      '<span>执行方</span><span>生产 CLI 子进程（GUI 不自实现写逻辑，动作前后落 action_log 账本）</span>' +
      '</div>' +
      (strong ? '<div class="gate-warn">⚠ 此动作会修改生态数据（写入/挤出/孵化/调度）。' +
        '生产脚本自带备份与锁机制，但执行前请确认影响。</div>' : '') +
      (strong ? '<label class="gate-ack"><input type="checkbox" id="gate-ack"> 我已知晓风险，确认执行</label>' : '') +
      '<div class="gate-btns">' +
      '<label class="gate-mute"><input type="checkbox" id="gate-mute"> 不再提醒（此类动作）</label>' +
      '<span style="flex:1"></span>' +
      '<button id="gate-cancel" class="ghost-btn">取消</button>' +
      '<button id="gate-go" class="gate-go' + (strong ? '' : ' light') + '">确认执行</button>' +
      '</div></div>';
    m.classList.remove('hidden');
    mask.classList.remove('hidden');
    $('#gate-cancel').onclick = self.close.bind(self);
    mask.onclick = self.close.bind(self);
    $('#gate-go').onclick = function () {
      if (strong && !$('#gate-ack').checked) {
        toast('高风险动作需先勾选「我已知晓风险」', false);
        return;
      }
      if ($('#gate-mute').checked) {
        try { localStorage.setItem(self.muteKey(action), '1'); } catch (e) {}
      }
      var fn = self._pending.onConfirm;
      var p = self._pending.params;
      self.close();
      if (fn) fn(p);
    };
  },

  close: function () {
    document.getElementById('gate-modal').classList.add('hidden');
    document.getElementById('gate-mask').classList.add('hidden');
    this._pending = null;
  }
};
