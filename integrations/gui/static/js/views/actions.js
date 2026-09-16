/* ============================================================
   views/actions.js — 动作日志（C4：GUI 自身审计账本）
   action_log.jsonl 渲染：时间/动作/阶段/结果/耗时/输出；可复制。
   ============================================================ */
'use strict';

RENDER.actions = async function (v) {
  let d;
  try {
    d = await EcoApi.get('/api/action_log');
  } catch (e) {
    v.innerHTML = `<div class="empty">动作账本读取失败：${esc(e.message)}</div>`;
    return;
  }
  const entries = d.entries || [];
  // 最新一条（上次会话之后新增的）高亮滑入：上次时间戳记在 localStorage
  let lastSeen = localStorage.getItem('eco_action_last') || '';
  let newest = entries.length ? (entries[0].ts || '') : '';
  v.innerHTML = `
    <div class="vh">动作日志 <small>GUI 自己的审计账本（action_log.jsonl）· 与 eco.db 生态账本分开 · 每次动作前后自动落一行</small></div>
    <div class="row" style="margin-bottom:10px">
      <span class="chip">共 <b>${entries.length}</b> 条（最新 200）</span>
      <button class="ghost-btn" id="al-copy">复制全部（JSONL）</button>
      <span class="faint">生态级长期账本见「时间线」视图（eco.db 四表）。</span>
    </div>
    ${entries.length ? `<div class="card"><table>
      <tr><th>时间</th><th>动作</th><th>阶段</th><th>结果</th><th>耗时</th><th>参数</th><th>输出摘要</th></tr>
      ${entries.map((e, i) => {
        const isNew = lastSeen && (e.ts || '') > lastSeen;
        return `<tr class="${isNew ? 'al-new' : ''}" style="${isNew ? `animation-delay:${Math.min((i) * 60, 400)}ms` : ''}">
        <td class="faint">${esc((e.ts || '').replace('T', ' '))}</td>
        <td><b>${esc(e.action)}</b></td>
        <td><span class="tag ${e.stage === 'run' ? 'warn' : ''}">${e.stage === 'run' ? '执行' : '预览'}</span></td>
        <td><span class="tag ${e.ok ? 'ok' : 'bad'}">${e.ok ? '成功' : '失败'}</span></td>
        <td class="num">${e.ms || 0}ms</td>
        <td class="faint ellipsis" style="max-width:160px">${esc(Object.entries(e.params || {}).map(([k, vv]) => k + '=' + vv).join(' ') || '—')}</td>
        <td class="faint ellipsis" style="max-width:280px" title="${esc((e.out || '').slice(0, 300))}">${esc((e.out || '').slice(0, 90))}</td>
      </tr>`;
      }).join('')}</table></div>`
      : Icons.empty('actions', '账本为空',
        '还没有通过 GUI 执行过任何动作。<br>去驾驶舱/候选孵化台试试「立即体检」（轻确认）或「立即挤出」（强确认），执行后这里会落账。')}`;
  if (newest) { try { localStorage.setItem('eco_action_last', newest); } catch (e) { } }
  const cp = $('#al-copy');
  if (cp) cp.onclick = () => ecoCopy(entries.map(e => JSON.stringify(e)).join('\n'), 'JSONL 已复制');
};
