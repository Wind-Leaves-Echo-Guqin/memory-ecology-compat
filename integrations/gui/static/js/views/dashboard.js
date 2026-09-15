/* ============================================================
   views/dashboard.js — 驾驶舱（v0.3）
   · cron 双口径分行（瞬时 jobs.json / 近7天 executions.db，各自标注采集时点）
   · 记忆越线红色告警卡（当前值/线/超出量/下次配额门倒计时）+ 一键挤出（走闸门）
   · 四门卡片：账本动作 N 条 + 运行证据（cron 最近一次状态）
   · 候选提醒卡；黄条点击复制错误全文
   ============================================================ */
'use strict';

RENDER.dashboard = async function (v) {
  const d = await cached('overview', 8000, () => EcoApi.get('/api/overview'));
  const h = await cached('health-mini', 30000, () => EcoApi.get('/api/health'));
  const c = d.counts;
  const score = d.score ? d.score.score : null;
  const ringCls = score == null ? '' : score >= 80 ? 'ok' : score >= 60 ? 'warn' : 'bad';
  const ringColor = { ok: 'var(--ok)', warn: 'var(--warn)', bad: 'var(--bad)' }[ringCls] || 'var(--ink3)';
  const over = d.watermark.chars > d.watermark.quota;
  const pct = Math.min(100, d.watermark.chars / d.watermark.quota * 100);
  const overW = Math.max(0, (d.watermark.chars - d.watermark.quota) / d.watermark.quota * 100);
  const gateMeta = {
    gate: ['门① 写入整合', 'var(--gate1)', 'gate_integrate'],
    quota: ['门② 巩固/配额', 'var(--gate2)', 'quota_extrude'],
    distill: ['门③ 画像蒸馏', 'var(--gate3)', null],
    review: ['门④ 复核', 'var(--gate4)', null],
  };
  // 评分趋势 + P0
  const hist = d.history || [];
  const tsvg = trendSvg(hist);
  const p0 = extractP0((h.report || {}).body);
  // cron 双口径（T2）+ 失败任务卡（v0.3.1 可操作修复）
  const e7 = d.cron.executions_7d || {};
  const snap = d.cron.jobs_snapshot || {};
  const fails = d.cron.recent_fails || [];
  const failDetails = d.cron.fail_details || [];
  const failFull = '近 7 天 cron 失败（口径 executions.db，截至 ' + (e7.ts || '').replace('T', ' ') + '）：\n' +
    failDetails.map(f => `【${f.job_name}】${f.finished_at}\n${f.error}`).join('\n\n') || '无失败';
  // 下次配额门倒计时（每小时 25 分）
  const nextQuotaMs = quotaGateCountdown();
  // 候选
  const ck = (d.candidates && d.candidates.by_kind) || {};
  const nCand = (ck.skill || 0) + (ck.profile || 0) + (ck.experience || 0);

  v.innerHTML = `
    ${fails.length ? `<div class="card fail-card"><h3>cron 失败任务 <small>近 7 天 ${e7.n_fails || fails.length} 次（口径 executions.db · 截至 ${(e7.ts || '').slice(11, 16)}）——每条可直接修复</small></h3>
      ${failDetails.map((f, i) => {
        const drift = /drift_skip|config drifted/.test(f.error || '');
        const pinCmd = f.provider ? `hermes cron edit ${f.job} --provider ${f.provider} --model ${f.model || '<model>'}` : '';
        return `<div class="fail-item">
        <div class="fail-head"><span class="dot bad"></span><b>${esc(f.job_name)}</b>
          <span class="faint">${esc(f.finished_at.replace('T', ' '))}</span>
          <span class="fail-ops">
            <a data-cron-rerun="${esc(f.job)}" data-rerun-name="${esc(f.job_name)}">重跑</a> ·
            <a data-cron-copy="hermes cron run ${esc(f.job)}">复制重跑命令</a>
            ${drift ? ` · <a data-cron-pin="${esc(f.job)}" data-provider="${esc(f.provider)}" data-model="${esc(f.model)}" data-pin-name="${esc(f.job_name)}">固定模型（消除 drift_skip）</a>` : ''}
            ${pinCmd && drift ? ` · <a data-cron-copy="${esc(pinCmd)}">复制固定命令</a>` : ''}
          </span></div>
        <div class="fail-err mono" id="ferr-${i}">${esc((f.error || '').slice(0, 400))}${(f.error || '').length > 400 ? '…' : ''}
          <a data-cron-copy-full="${i}" class="faint">复制全文</a></div>
      </div>`;
      }).join('')}
    </div>` : ''}
    ${over ? `<div class="alarm" style="border-color:var(--bad);background:rgba(208,83,83,.08);color:var(--bad)">
      🔴 <b>L1 记忆已越线</b>：${d.watermark.chars} / 管理线 ${d.watermark.quota} 字符（超 ${d.watermark.chars - d.watermark.quota}）
      · 下次配额门 <b>${nextQuotaMs}</b> 后自动运行（每小时 25 分）
      <button class="ghost-btn act-btn" data-act="quota_extrude" style="margin-left:10px;border-color:var(--bad);color:var(--bad)">立即挤出</button>
      <span class="red-note">会挤出沉淀到 L2 详情层（写前自动备份）</span></div>` : ''}
    <div class="vh">驾驶舱 <small>可操作驾驶舱 · 10 秒扫完 · 快照 ${esc(d.score ? d.score.time : '—')}</small></div>
    <div class="vsub">体检评分口径 = 每日 12:40 报告 · 其余计数 = 目录实时统计（两者时刻不同，不混称）</div>
    <div class="grid2">
      <div class="card"><h3>体检评分</h3>
        <div class="ringbox">
          <svg class="ring" width="96" height="96" viewBox="0 0 100 100">
            <circle class="track" cx="50" cy="50" r="45" stroke-width="7"/>
            <circle class="arc" cx="50" cy="50" r="45" stroke-width="7"
              style="stroke:${ringColor}; stroke-dashoffset:${score == null ? 283 : 283 * (1 - score / 100)}"/>
          </svg>
          <div class="ring-info">
            <b>${score ?? '—'}</b> <span class="dim">/100</span><br>
            <span class="dot ${ringCls || 'off'}"></span>${score == null ? '暂无评分' : score >= 80 ? '🟢 健康' : score >= 60 ? '🟡 亚健康' : '🔴 需关注'}<br>
            <span class="faint" style="font-size:11.5px">模型 ${esc(d.score ? d.score.model : '—')} · 7 维明细 → <a data-goto="health">体检视图</a></span>
          </div>
        </div>
      </div>
      <div class="card"><h3>评分历史 <small>同模型才连线，跨版本不比（生态口径）</small></h3>
        ${tsvg || '<div class="empty">暂无评分历史</div>'}
      </div>
    </div>
    <div class="card"><div class="water">
      <div class="bar">
        <div class="fill" style="width:${pct}%"></div>
        ${over ? `<div class="over" style="left:${100 - overW}%; width:${overW}%"></div><div class="ql" style="left:${100 - overW}%"></div>` : ''}
      </div>
      <div class="legend">
        <span>L1 常驻记忆 <b class="num">${d.watermark.chars}</b> 字 / 管理线 ${d.watermark.quota}（去空白口径）</span>
        <span class="${over ? 'alert' : 'faint'}" style="${over ? 'color:var(--bad);font-weight:600' : ''}">${over ? `已越线 +${d.watermark.chars - d.watermark.quota} · 红色段呼吸=真实状态` : '未越线'}</span>
      </div>
    </div></div>
    ${nCand > 0 ? `<div class="card" style="border-color:var(--warn)"><h3>待处理候选 <small>${nCand} 项——出现即有对应门/孵化台可消费</small></h3>
      <div class="row"><span class="chip click" data-goto="candidates">技能候选 <b>${ck.skill || 0}</b></span>
      <span class="chip click" data-goto="candidates">画像候选 <b>${ck.profile || 0}</b></span>
      <span class="chip click" data-goto="candidates">经验候选 <b>${ck.experience || 0}</b></span>
      <a data-goto="candidates">→ 去候选孵化台</a></div></div>` : ''}
    <div class="card"><h3>生态计数 <small>目录实时统计</small></h3>
      <div class="row">
        <span class="chip click" data-goto="memories">L1 <b>${c.l1_entries}</b> 条</span>
        <span class="chip click" data-goto="memories">L2 详情 <b>${c.l2}</b></span>
        <span class="chip click" data-goto="memories">隔离区 <b>${c.quarantine}</b></span>
        <span class="chip click" data-goto="experiences">经验 <b>${c.exp_total}</b><span class="faint">（draft ${c.exp_draft} · verified ${c.exp_verified}）</span></span>
        <span class="chip click" data-goto="starmap">技能 <b>${c.skills}</b><span class="faint">（唯一 ${c.skills_unique} · 归档 ${c.skills_archive} · 候选 ${c.skills_candidates}）</span></span>
        <span class="chip">生态 cron <b>${c.cron_eco}</b>/${c.cron_total}</span>
      </div>
    </div>
    <div class="card"><h3>四道门 <small>账本 = eco.db 机读记录 · 运行证据 = cron 计划最近执行</small></h3>
      <div class="row" style="align-items:stretch">
        ${Object.entries(gateMeta).map(([k, [nm, col, act]]) => {
          const g = d.gates[k];
          const cnt = d.gate_counts[k] || 0;
          const job = (d.cron.eco_jobs || []).find(j =>
            (k === 'gate' && j.name.indexOf('写入') >= 0) || (k === 'quota' && j.name.indexOf('配额') >= 0) ||
            (k === 'distill' && j.name.indexOf('蒸馏') >= 0) || (k === 'review' && j.name.indexOf('复核') >= 0));
          const ev = job ? `<br><span class="faint">运行证据：cron ${esc(job.display)} · 最近 ${esc(job.last_run_at || '—')} · <span class="tag ${job.last_status === 'ok' ? 'ok' : 'bad'}">${esc(job.last_status || '—')}</span></span>`
            : '';
          return `<div class="gate"><div class="gname"><i class="gdot" style="background:${col}"></i>${nm}</div>
            <div class="gmeta">账本动作 <b class="num">${cnt}</b> 条
            ${g ? `<br>最近 ${esc(g.ts.replace('T', ' '))} · ${esc(g.action)}` : '<br><span class="faint">账本尚无动作</span>'}
            ${ev}${cnt ? '' : ''}</div>
            ${act ? `<div style="margin-top:7px"><button class="ghost-btn act-btn" data-act="${act}" style="font-size:11px;padding:3px 9px">立即执行</button></div>` : ''}</div>`;
        }).join('')}
      </div>
    </div>
    <div class="card"><h3>每日健康行 <small>VERSION.md 口径（生态心电图）</small></h3>
      ${d.health_row ? `<div class="healthline" style="border:none;margin:0;padding:0">
        <span>${esc(d.health_row.date)}</span>
        <span>基因库 ${esc(d.health_row.gene)}</span>
        <span>cron ${esc(d.health_row.cron)}</span>
        <span>连续失败 ${esc(d.health_row.fail)}</span>
        <span>记忆占用 ${esc(d.health_row.mem)}</span></div>`
        : '<div class="empty">VERSION.md 无健康行</div>'}
      <div class="healthline" style="border:none;margin:10px 0 0;padding:0">
        <span class="tag">瞬时口径（jobs.json）：${snap.ok || 0} ok / ${snap.err || 0} err · 采集 ${(snap.ts || '').slice(11, 16)}</span>
        <span class="tag ${e7.n_fails ? 'warn' : 'ok'}">近 7 天口径（executions.db）：${e7.n_exec || 0} 次执行 · ${e7.n_fails || 0} 次失败 · 采集 ${(e7.ts || '').slice(11, 16)}</span>
      </div>
      ${d.gene && d.gene.length ? `<div class="healthline" style="border:none;margin:10px 0 0;padding:0">
        <span class="faint">基因库近提交：</span>${d.gene.slice(0, 3).map(g => `<span class="faint mono">${esc(g)}</span>`).join('')}</div>` : ''}
    </div>
    ${p0 ? `<div class="card"><h3>最优先行动 <small>体检报告 §9 · P0/P1/P2 分级建议</small></h3>
      <div class="row">${p0.map(s => `<span class="chip" style="width:100%">${esc(s)} <a data-goto="health">→ 去体检视图定位</a></span>`).join('')}</div>
    </div>` : ''}`;

  // 失败任务卡操作绑定（v0.3.1）
  $$('[data-cron-copy]', v).forEach(a => a.onclick = () => ecoCopy(a.dataset.cronCopy, '命令已复制'));
  $$('[data-cron-copy-full]', v).forEach(a => a.onclick = () => {
    const f = failDetails[+a.dataset.cronCopyFull];
    ecoCopy(`【${f.job_name}】${f.finished_at}\n${f.error}`, '错误全文已复制');
  });
  $$('[data-cron-rerun]', v).forEach(a => a.onclick = () => {
    const id = a.dataset.cronRerun, name = a.dataset.rerunName;
    Gate.ask('cron_rerun', {
      name: `重跑 cron 任务「${name}」`, risk: 'strong',
      impact: `让调度器在下个 tick 重新执行任务 ${id}（hermes cron run）。任务本身幂等，误重跑无破坏性。`,
      rollback: '无需回滚（重跑一次调度动作）'
    }, { job_id: id }, p => {
      EcoApi.post('/api/action/cron_rerun', { stage: 'run', params: p }).then(r => {
        toast(r.ok ? '已下发重跑——下个调度 tick 生效' : '重跑失败：' + (r.output || '').slice(0, 80), r.ok);
        if (r.ok) setTimeout(() => { S.cache = {}; route(); }, 2500);
      }).catch(e => toast('重跑失败：' + e.message, false));
    });
  });
  $$('[data-cron-pin]', v).forEach(a => a.onclick = () => {
    const id = a.dataset.cronPin, provider = a.dataset.provider, model = a.dataset.model, name = a.dataset.pinName;
    if (!provider) { ecoCopy(`hermes cron edit ${id} --provider <provider> --model <model>`, '请补全 provider/model 后执行'); return; }
    Gate.ask('cron_pin', {
      name: `固定「${name}」的模型`, risk: 'strong',
      impact: `将任务 ${id} 钉在 provider=${provider} / model=${model || '?'}，消除 drift_skip（配置漂移跳过）。`,
      rollback: '可再次 hermes cron edit 改回；只影响该任务'
    }, { job_id: id, provider, model }, p => {
      EcoApi.post('/api/action/cron_pin', { stage: 'run', params: p }).then(r => {
        toast(r.ok ? '模型已固定' : '固定失败：' + (r.output || '').slice(0, 80), r.ok);
        if (r.ok) setTimeout(() => { S.cache = {}; route(); }, 2500);
      }).catch(e => toast('固定失败：' + e.message, false));
    });
  });
  $$('[data-goto]', v).forEach(a => a.onclick = () => goto(a.dataset.goto));
  bindActionButtons(v);
};

/* 下次配额门（每小时 25 分）倒计时 → "X 分 Y 秒" 文本 */
function quotaGateCountdown() {
  const now = new Date();
  const next = new Date(now);
  next.setMinutes(25, 0, 0);
  if (next <= now) next.setHours(next.getHours() + 1);
  const s = Math.round((next - now) / 1000);
  return s >= 60 ? `${Math.floor(s / 60)} 分 ${s % 60} 秒` : `${s} 秒`;
}

/* 统一绑定动作按钮（走闸门 → dry 预览 → run） */
function bindActionButtons(container) {
  $$('[data-act]', container).forEach(btn => {
    btn.onclick = () => runAction(btn.dataset.act, {});
  });
}

/* 通用动作执行链：闸门 → dry 预览（抽屉展示）→ run（结果抽屉） */
async function runAction(action, params, formValues) {
  try {
    const meta = (await cached('actions-meta', 60000, () => EcoApi.get('/api/actions'))).actions;
    const spec = meta[action];
    if (!spec) { toast('未知动作：' + action, false); return; }
    Object.assign(params, formValues || {});
    Gate.ask(action, spec, params, async p => {
      openDrawer(`<div class="vh">${esc(spec.name)} <small>预览阶段（dry-run，不写入）…</small></div><div class="loading">正在生成预览…</div>`);
      try {
        const dry = await EcoApi.post(`/api/action/${action}`, { stage: 'dry', params: p });
        const outText = (dry.output || '').slice(0, 6000);
        openDrawer(`<div class="vh">${esc(spec.name)} <small>预览（dry-run）· ${dry.ok ? '可执行' : '预览异常'}</small></div>
          <pre class="mono dry-out">${esc(outText || '(无输出)')}</pre>
          <div class="gate-btns" style="margin-top:12px">
            <button class="ghost-btn" id="act-close">关闭</button>
            ${dry.ok ? `<button class="gate-go" id="act-run">确认执行（写入）</button>` : ''}
          </div>`);
        $('#act-close').onclick = closeDrawer;
        if (dry.ok) $('#act-run').onclick = async () => {
          openDrawer(`<div class="vh">${esc(spec.name)} <small>执行中…</small></div><div class="loading">正在执行（走生产 CLI）…</div>`);
          try {
            const run = await EcoApi.post(`/api/action/${action}`, { stage: 'run', params: p });
            openDrawer(`<div class="vh">${esc(spec.name)} <small>执行${run.ok ? '完成 ✅' : '失败 ❌'} · ${run.ms}ms · 已落 action_log 账本</small></div>
              <pre class="mono dry-out">${esc((run.output || '').slice(0, 6000) || '(无输出)')}</pre>
              ${run.ok ? '<div class="empty-ok">可在「动作日志」视图复查本次执行；eco.db 对应表将出现记录。</div>' : ''}
              <div class="gate-btns" style="margin-top:12px"><button class="ghost-btn" id="act-close2">关闭</button></div>`);
            const c2 = $('#act-close2');
            if (c2) c2.onclick = closeDrawer;
            Bell.refresh();
          } catch (e) {
            openDrawer(`<div class="alarm">执行失败：${esc(e.message)}</div>`);
          }
        };
      } catch (e) {
        openDrawer(`<div class="alarm">预览失败：${esc(e.message)}</div>`);
      }
    });
  } catch (e) { toast('动作元数据读取失败：' + e.message, false); }
}

function trendSvg(hist) {
  if (!hist.length) return '';
  const w = 560, hgt = 110, pad = 26;
  const min = Math.min(...hist.map(x => x.score)) - 8;
  const max = Math.max(...hist.map(x => x.score)) + 8;
  const X = i => pad + i * (w - pad * 2) / Math.max(1, hist.length - 1);
  const Y = s => hgt - 24 - (s - min) / Math.max(1, max - min) * (hgt - 46);
  const pts = hist.map((x, i) => `${X(i).toFixed(1)},${Y(x.score).toFixed(1)}`).join(' ');
  // v0.3.2：每个点直接标数值（不再只能看走势）+ 悬停 title 给完整时间
  const dots = hist.map((x, i) =>
    `<circle cx="${X(i)}" cy="${Y(x.score)}" r="3.4" fill="var(--accent)"><title>${esc(x.time)} · ${x.score} 分（${esc(x.model)}）</title></circle>` +
    `<text x="${X(i)}" y="${(Y(x.score) - 7).toFixed(1)}" text-anchor="middle" font-size="9" fill="var(--ink2)">${x.score}</text>`).join('');
  const labels = hist.map((x, i) => i % Math.ceil(hist.length / 5) === 0 || i === hist.length - 1
    ? `<text x="${X(i)}" y="${hgt - 6}" text-anchor="middle">${esc(x.time.slice(5, 10))}</text>` : '').join('');
  return `<svg class="trend" viewBox="0 0 ${w} ${hgt}" style="width:100%">
    <line x1="${pad}" y1="${hgt - 24}" x2="${w - pad}" y2="${hgt - 24}" stroke="var(--line)"/>
    <polyline class="draw" points="${pts}" fill="none" stroke="var(--accent)" stroke-width="1.6"/>
    ${dots}${labels}</svg>
    <div class="trendcap">近 ${hist.length} 条 · 每点标数值 · 全部为模型 <b>${esc(hist[hist.length - 1].model)}</b>${hist.some(x => x.model !== hist[hist.length - 1].model) ? '（含跨模型记录——仅同模型段连线）' : ''}</div>`;
}

function extractP0(body) {
  if (!body) return [];
  const out = [];
  for (const L of body.split('\n')) {
    if (/\bP0\b/.test(L) && L.trim().length > 12 && !out.includes(L.trim()))
      out.push(L.trim().replace(/^[|#*\-\s]+/, '').slice(0, 160));
    if (out.length >= 2) break;
  }
  return out;
}
