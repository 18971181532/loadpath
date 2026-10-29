/* LoadPath 工作台 —— 手写 Canvas，无任何前端依赖。
 *
 * 数据流：/api/analyze → S.analyze（几何、位移、内力、校核）
 *         /api/optimize → S.opt（优化前后与迭代历史）
 *         /api/scan     → S.scan（逐根拆除的重要度）
 * 四个页签共用同一套绘图函数，只是"上色函数"和"右栏内容"不同。
 */
'use strict';

const S = {
  cases: [],
  caseName: 'warren',
  tab: 'deform',
  analyze: null,
  opt: null,
  scan: null,
  params: { gamma: 1.5, defl: 250, prune: 0.2 },
  view: { scale: 200, anim: true, labels: false, ghost: true },
  caseIndex: 0,
  selected: null,
  hover: null,
  t: 0,
  busy: false
};

const $ = (id) => document.getElementById(id);
const cv = $('cv');
const ctx = cv.getContext('2d');

/* ------------------------------------------------------------------ 工具 */

const fmt = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v))
  ? '—' : Number(v).toFixed(d);

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) for (const k in attrs) {
    if (k === 'class') node.className = attrs[k];
    else if (k === 'html') node.innerHTML = attrs[k];
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), attrs[k]);
    else node.setAttribute(k, attrs[k]);
  }
  (children || []).forEach((c) => node.appendChild(
    typeof c === 'string' ? document.createTextNode(c) : c));
  return node;
}

async function getJSON(url) {
  const r = await fetch(url);
  const text = await r.text();
  let body = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch (e) {
    throw new Error('响应不是合法 JSON（' + text.length + ' 字节）');
  }
  if (!r.ok) throw new Error(body.error || ('HTTP ' + r.status));
  if (!text) throw new Error('空响应');
  return body;
}

/* ------------------------------------------------------------------ 颜色 */

const COL = {
  tension: '#cf4040',
  compression: '#2f6fd0',
  idle: '#9aa9bb',
  ghost: '#c9d4e2',
  node: '#22324a',
  support: '#4a6588',
  load: '#b8791a',
  grid: '#e9eff6'
};

const RISK_COL = {
  '致命': '#a01b12', '关键': '#d4641a', '重要': '#d3a41c',
  '一般': '#6f9cc4', '冗余': '#bcc7d5'
};

function ratioColor(r) {
  // 0 → 绿，0.8 → 琥珀，1.0+ → 红
  const t = Math.min(Math.max(r, 0), 1.25) / 1.25;
  if (t < 0.64) {
    const k = t / 0.64;
    return lerpHex('#3f9a6d', '#d9a441', k);
  }
  return lerpHex('#d9a441', '#c0392b', (t - 0.64) / 0.36);
}

function lerpHex(a, b, k) {
  const pa = parseInt(a.slice(1), 16), pb = parseInt(b.slice(1), 16);
  const r = Math.round(((pa >> 16) & 255) * (1 - k) + ((pb >> 16) & 255) * k);
  const g = Math.round(((pa >> 8) & 255) * (1 - k) + ((pb >> 8) & 255) * k);
  const bl = Math.round((pa & 255) * (1 - k) + (pb & 255) * k);
  return 'rgb(' + r + ',' + g + ',' + bl + ')';
}

/* ------------------------------------------------------------------ 变换 */

function makeTransform(bbox, w, h, pad) {
  const [x0, y0, x1, y1] = bbox;
  const mw = Math.max(x1 - x0, 1e-6);
  const mh = Math.max(y1 - y0, 1e-6);
  const s = Math.min((w - 2 * pad) / mw, (h - 2 * pad) / mh);
  const ox = (w - mw * s) / 2 - x0 * s;
  const oy = (h - mh * s) / 2 + y1 * s;
  return {
    s,
    x: (mx) => mx * s + ox,
    y: (my) => oy - my * s
  };
}

/* ------------------------------------------------------------------ 数据 */

async function boot() {
  try {
    S.cases = await getJSON('/api/cases');
  } catch (e) {
    $('verdict').textContent = '无法连接服务';
    return;
  }
  renderCaseTabs();
  await loadCase(S.caseName);
}

function renderCaseTabs() {
  const nav = $('caseTabs');
  nav.innerHTML = '';
  S.cases.forEach((c) => {
    nav.appendChild(el('button', {
      class: c.name === S.caseName ? 'on' : '',
      title: c.note,
      onclick: () => { if (c.name !== S.caseName) loadCase(c.name); }
    }, [c.label]));
  });
}

async function loadCase(name) {
  S.caseName = name;
  S.opt = null;
  S.scan = null;
  S.selected = null;
  S.hover = null;
  S.caseIndex = 0;
  renderCaseTabs();
  $('verdict').textContent = '计算中…';
  $('verdict').className = 'verdict';
  try {
    const q = `case=${encodeURIComponent(name)}&gamma=${S.params.gamma}&defl=${S.params.defl}`;
    S.analyze = await getJSON('/api/analyze?' + q);
  } catch (e) {
    $('verdict').textContent = '计算失败';
    $('verdict').className = 'verdict bad';
    $('side').innerHTML = `<div class="empty">${e.message}</div>`;
    return;
  }
  if (S.tab === 'opt') return ensureTab('opt');
  if (S.tab === 'scan') return ensureTab('scan');
  render();
}

async function ensureTab(tab) {
  if (tab === 'opt' && !S.opt) {
    S.busy = true;
    renderSide();
    try {
      S.opt = await getJSON(`/api/optimize?case=${S.caseName}&gamma=${S.params.gamma}` +
        `&defl=${S.params.defl}&prune=${S.params.prune}`);
    } catch (e) {
      S.opt = { error: e.message };
    }
    S.busy = false;
  }
  if (tab === 'scan' && !S.scan) {
    S.busy = true;
    renderSide();
    try {
      S.scan = await getJSON(`/api/scan?case=${S.caseName}&gamma=${S.params.gamma}&defl=${S.params.defl}`);
    } catch (e) {
      S.scan = { error: e.message };
    }
    S.busy = false;
  }
  render();
}

/* ------------------------------------------------------------------ 当前视图数据 */

function viewPayload() {
  // 优化页签展示优化后的结果，其余展示原始分析结果
  if (S.tab === 'opt' && S.opt && !S.opt.error) return S.opt;
  return S.analyze;
}

function currentSolution() {
  const d = viewPayload();
  if (!d) return null;
  return d.analysis.solutions[Math.min(S.caseIndex, d.analysis.solutions.length - 1)];
}

function memberMetric(i) {
  // 返回 {color, width, dash}
  const d = viewPayload();
  if (!d) return { color: COL.idle, width: 2 };
  if (S.tab === 'opt' && S.opt && !S.opt.error) {
    const f = currentSolution().forces[i] || 0;
    return { color: forceColor(f), width: 1.4 + Math.min(Math.abs(f) / 40000, 1) * 5 };
  }
  if (S.tab === 'scan' && S.scan && !S.scan.error) {
    const rec = S.scan.scan.members.find((m) => m.member === i);
    return { color: rec ? (RISK_COL[rec.level] || COL.idle) : COL.idle, width: 3.4 };
  }
  if (S.tab === 'check') {
    const c = d.check.members.find((m) => m.member === i);
    return { color: c ? ratioColor(c.ratio) : COL.idle, width: 2 + Math.min((c ? c.ratio : 0), 1.2) * 3.4 };
  }
  const f = currentSolution().forces[i] || 0;
  return { color: forceColor(f), width: 1.6 + Math.min(Math.abs(f) / 40000, 1) * 5.4 };
}

function forceColor(f) {
  if (f > 1e-6) return COL.tension;
  if (f < -1e-6) return COL.compression;
  return COL.idle;
}

/* ------------------------------------------------------------------ 绘制 */

let W = 0, H = 0;

function resize() {
  const dpr = window.devicePixelRatio || 1;
  const rect = cv.parentElement.getBoundingClientRect();
  W = Math.max(rect.width, 320);
  H = Math.max(rect.height, 240);
  cv.width = Math.round(W * dpr);
  cv.height = Math.round(H * dpr);
  cv.style.width = W + 'px';
  cv.style.height = H + 'px';
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  draw();
}

function drawGrid(T, bbox) {
  ctx.save();
  ctx.strokeStyle = COL.grid;
  ctx.lineWidth = 1;
  const step = 1000 * T.s > 40 ? 1000 : 2000;
  const [x0, y0, x1, y1] = bbox;
  const sy0 = Math.floor(y0 / step) * step, sy1 = Math.ceil(y1 / step) * step;
  const sx0 = Math.floor(x0 / step) * step, sx1 = Math.ceil(x1 / step) * step;
  for (let x = sx0; x <= sx1; x += step) {
    ctx.beginPath(); ctx.moveTo(T.x(x), 0); ctx.lineTo(T.x(x), H); ctx.stroke();
  }
  for (let y = sy0; y <= sy1; y += step) {
    ctx.beginPath(); ctx.moveTo(0, T.y(y)); ctx.lineTo(W, T.y(y)); ctx.stroke();
  }
  ctx.restore();
}

function drawSupports(model, T) {
  ctx.save();
  ctx.strokeStyle = COL.support;
  ctx.fillStyle = COL.support;
  ctx.lineWidth = 1.6;
  model.supports.forEach((sp) => {
    const n = model.nodes[sp.node];
    const x = T.x(n.x), y = T.y(n.y);
    const r = 9;
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x - r, y + r * 1.5);
    ctx.lineTo(x + r, y + r * 1.5);
    ctx.closePath();
    ctx.stroke();
    if (sp.fix_x && sp.fix_y) {
      ctx.beginPath(); ctx.moveTo(x - r, y + r * 1.5); ctx.lineTo(x + r, y + r * 1.5); ctx.stroke();
      // 斜线地基
      ctx.beginPath();
      for (let k = -r; k <= r; k += 5) {
        ctx.moveTo(x + k, y + r * 1.5);
        ctx.lineTo(x + k - 4, y + r * 1.5 + 5);
      }
      ctx.stroke();
    } else {
      // 滚动支座：两个小轮
      [-3.5, 3.5].forEach((dx) => {
        ctx.beginPath(); ctx.arc(x + dx, y + r * 1.5 + 4, 3, 0, Math.PI * 2); ctx.stroke();
      });
      ctx.beginPath(); ctx.moveTo(x - r, y + r * 1.5 + 7.5); ctx.lineTo(x + r, y + r * 1.5 + 7.5); ctx.stroke();
    }
  });
  ctx.restore();
}

function drawLoads(model, T) {
  const cases = model.cases;
  if (!cases.length) return;
  const theCase = cases[Math.min(S.caseIndex, cases.length - 1)];
  let maxF = 0;
  theCase.loads.forEach((l) => { maxF = Math.max(maxF, Math.hypot(l.fx, l.fy)); });
  if (maxF <= 0) return;
  ctx.save();
  ctx.strokeStyle = COL.load;
  ctx.fillStyle = COL.load;
  ctx.lineWidth = 1.8;
  theCase.loads.forEach((l) => {
    const n = model.nodes[l.node];
    const x = T.x(n.x), y = T.y(n.y);
    const len = 30 + 30 * Math.hypot(l.fx, l.fy) / maxF;
    const ux = l.fx / Math.hypot(l.fx, l.fy), uy = -l.fy / Math.hypot(l.fx, l.fy);
    const x0 = x - ux * len, y0 = y - uy * len;
    ctx.lineWidth = 2.2;
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x - ux * 9, y - uy * 9); ctx.stroke();
    // 箭头
    const a = Math.atan2(uy, ux);
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x - 11 * Math.cos(a - 0.38), y - 11 * Math.sin(a - 0.38));
    ctx.lineTo(x - 11 * Math.cos(a + 0.38), y - 11 * Math.sin(a + 0.38));
    ctx.closePath(); ctx.fill();
  });
  ctx.restore();
}

function drawMembers(model, T, opts) {
  const { thin, dashed, widthScale, colorFn, offset, alpha } = opts;
  ctx.save();
  ctx.globalAlpha = alpha === undefined ? 1 : alpha;
  ctx.lineCap = 'round';
  model.members.forEach((m, i) => {
    if (dashed && !dashed.has(i)) return;
    const a = model.nodes[m.i], b = model.nodes[m.j];
    const [dax, day] = offset ? offset(m.i) : [0, 0];
    const [dbx, dby] = offset ? offset(m.j) : [0, 0];
    const spec = colorFn(i);
    ctx.beginPath();
    ctx.moveTo(T.x(a.x + dax), T.y(a.y + day));
    ctx.lineTo(T.x(b.x + dbx), T.y(b.y + dby));
    ctx.strokeStyle = spec.color;
    ctx.lineWidth = thin ? 1 : spec.width * (widthScale || 1);
    if (spec.dash) ctx.setLineDash(spec.dash); else ctx.setLineDash([]);
    ctx.stroke();
  });
  ctx.setLineDash([]);
  ctx.restore();
}

function drawNodes(model, T, offset) {
  ctx.save();
  ctx.fillStyle = '#ffffff';
  ctx.strokeStyle = COL.node;
  ctx.lineWidth = 1.4;
  model.nodes.forEach((n, i) => {
    const [dx, dy] = offset ? offset(i) : [0, 0];
    ctx.beginPath();
    ctx.arc(T.x(n.x + dx), T.y(n.y + dy), 3.2, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  });
  ctx.restore();
}

function drawLabels(model, T) {
  ctx.save();
  ctx.font = '10px ui-monospace, Consolas, monospace';
  ctx.fillStyle = '#7b8ba0';
  ctx.textAlign = 'center';
  model.members.forEach((m, i) => {
    if (S.selected !== null && S.selected !== i) return;
    const a = model.nodes[m.i], b = model.nodes[m.j];
    ctx.fillText(String(i), (T.x(a.x) + T.x(b.x)) / 2, (T.y(a.y) + T.y(b.y)) / 2 - 4);
  });
  ctx.restore();
}

function draw() {
  if (!S.analyze) return;
  const payload = viewPayload();
  if (!payload || !payload.model) return;
  const model = payload.model;
  ctx.clearRect(0, 0, W, H);
  const T = makeTransform(payload.bbox, W, H, 56);
  drawGrid(T, payload.bbox);

  const sol = currentSolution();
  const scale = S.view.scale * (S.view.anim ? 0.72 + 0.28 * Math.sin(S.t) : 1);
  const offset = (S.tab === 'deform' && sol)
    ? (i) => [sol.disp[i][0] * scale, sol.disp[i][1] * scale]
    : null;

  drawSupports(model, T);

  // 优化页签：先画被剔除的杆（虚影）
  if (S.tab === 'opt' && S.opt && !S.opt.error) {
    const orig = S.analyze.model;
    if (S.view.ghost) {
      const removed = new Set(S.opt.optimize.removed);
      drawMembers(orig, T, {
        thin: true, widthScale: 1, alpha: 0.85,
        colorFn: (i) => removed.has(i)
          ? { color: '#c9d4e2', width: 1, dash: [5, 4] }
          : { color: '#eef2f8', width: 1 }
      });
    }
    drawMembers(model, T, { colorFn: memberMetric, widthScale: 1.15 });
  } else if (S.tab === 'deform') {
    // 未变形轮廓
    drawMembers(model, T, {
      thin: true, alpha: 0.9,
      colorFn: () => ({ color: '#d7e0eb', width: 1 })
    });
    if (offset) drawMembers(model, T, { colorFn: memberMetric, offset });
  } else {
    drawMembers(model, T, { colorFn: memberMetric, widthScale: 1.1 });
  }

  drawNodes(model, T, S.tab === 'deform' ? offset : null);
  drawLoads(model, T);
  if (S.view.labels || S.selected !== null) {
    drawLabels(model, T);
    if (S.selected !== null) highlight(model, T);
  }
  drawAxesNote(model);
}

function highlight(model, T) {
  const m = model.members[S.selected];
  if (!m) return;
  const a = model.nodes[m.i], b = model.nodes[m.j];
  ctx.save();
  ctx.strokeStyle = '#1d2a3a';
  ctx.lineWidth = 1.2;
  ctx.setLineDash([3, 3]);
  ctx.beginPath();
  ctx.moveTo(T.x(a.x), T.y(a.y));
  ctx.lineTo(T.x(b.x), T.y(b.y));
  ctx.stroke();
  ctx.restore();
}

function drawAxesNote(model) {
  ctx.save();
  ctx.font = '11px ui-monospace, Consolas, monospace';
  ctx.fillStyle = '#a8b4c4';
  ctx.textAlign = 'right';
  ctx.fillText(`跨度 ${(model.stats.span / 1000).toFixed(1)} m`, W - 14, H - 14);
  ctx.restore();
}

/* ------------------------------------------------------------------ 右栏 */

function kpi(label, value, unit, cls) {
  return `<div class="kpi ${cls || ''}"><span>${label}</span><b>${value}<small>${unit || ''}</small></b></div>`;
}

function renderSide() {
  const side = $('side');
  const d = viewPayload();
  if (S.busy) { side.innerHTML = '<div class="empty">计算中…</div>'; return; }
  if (!d) { side.innerHTML = '<div class="empty">无数据</div>'; return; }

  let head = '', kpis = '', body = '';

  if (S.tab === 'deform') {
    const sol = currentSolution();
    head = `<h2>${d.case.label}</h2><p>${d.case.note}</p>`;
    kpis = kpi('最大轴力', fmt(Math.max(...sol.forces.map(Math.abs)) / 1000, 1), 'kN', 'em')
      + kpi('最大位移', fmt(sol.max_disp, 2), 'mm')
      + kpi('自由度', d.analysis.dof, `/ ${d.analysis.free} 自由`)
      + kpi('半带宽', d.analysis.bandwidth, 'RCM 后');
    const loads = d.model.cases;
    body += '<div class="block-r"><h3>工况</h3><div class="chips">';
    loads.forEach((c, i) => {
      body += `<span class="chip" style="cursor:pointer;${i === S.caseIndex ? 'background:#dce9fb;color:#255bb0' : ''}" data-case="${i}">${c.name}</span>`;
    });
    body += '</div></div>';

    const rows = sol.disp.map((p, i) => ({ i, ux: p[0], uy: p[1] }))
      .sort((a, b) => Math.hypot(b.ux, b.uy) - Math.hypot(a.ux, a.uy)).slice(0, 10);
    body += '<div class="block-r"><h3>位移最大的节点</h3><table><thead><tr>' +
      '<th>节点</th><th>ux</th><th>uy</th><th>|u|</th></tr></thead><tbody>';
    rows.forEach((r) => {
      body += `<tr><td class="st-name">${r.i}</td><td>${fmt(r.ux, 3)}</td>` +
        `<td>${fmt(r.uy, 3)}</td><td>${fmt(Math.hypot(r.ux, r.uy), 3)}</td></tr>`;
    });
    body += '</tbody></table></div>';
  }

  if (S.tab === 'check') {
    const c = d.check;
    head = `<h2>校核</h2><p>允许应力法 · γ=${S.params.gamma} · 挠度限值 跨/${S.params.defl}</p>`;
    kpis = kpi('最大利用率', fmt(c.max_ratio, 2), '', c.max_ratio > 1 ? 'bad' : 'ok')
      + kpi('最大挠度', fmt(c.max_disp, 1), 'mm', c.disp_ratio > 1 ? 'bad' : '')
      + kpi('挠度限值', fmt(c.disp_limit, 1), 'mm')
      + kpi('杆件', d.model.stats.members, `根 / ${fmt(d.model.stats.mass, 0)} kg`);
    if (c.issues.length) {
      body += '<div class="block-r"><h3>问题</h3><ul class="issues">' +
        c.issues.map((i) => `<li>${i}</li>`).join('') + '</ul></div>';
    }
    const rows = c.members.slice().sort((a, b) => b.ratio - a.ratio);
    body += '<div class="block-r"><h3>杆件明细</h3><table><thead><tr>' +
      '<th>#</th><th>节点</th><th>N/kN</th><th>λ</th><th>利用率</th></tr></thead><tbody>';
    rows.forEach((m) => {
      const bar = `<span class="ratio-bar" style="width:${Math.min(m.ratio, 1) * 34 + 4}px;background:${ratioColor(m.ratio)}"></span>`;
      body += `<tr data-member="${m.member}" class="${S.selected === m.member ? 'sel' : ''}">` +
        `<td class="st-name">${m.member}</td><td>${m.i}–${m.j}</td>` +
        `<td>${fmt(m.force / 1000, 1)}</td><td>${fmt(m.slenderness, 0)}</td>` +
        `<td class="${m.ratio > 1 ? 'st-bad' : ''}">${bar}${fmt(m.ratio, 2)}</td></tr>`;
    });
    body += '</tbody></table></div>';
  }

  if (S.tab === 'opt') {
    if (!S.opt) { side.innerHTML = '<div class="empty">优化中…</div>'; return; }
    if (S.opt.error) { side.innerHTML = `<div class="empty">${S.opt.error}</div>`; return; }
    const o = S.opt.optimize;
    head = `<h2>优化结果</h2><p>先减拓扑、再减截面；阈值 ${S.params.prune}</p>`;
    kpis = kpi('减重', fmt(o.saving * 100, 1), '%', 'ok')
      + kpi('质量', fmt(o.after.mass, 0), `kg ← ${fmt(o.before.mass, 0)}`)
      + kpi('杆件', o.after.members, `← ${o.before.members}`)
      + kpi('利用率', fmt(o.after.max_ratio, 2), `← ${fmt(o.before.max_ratio, 2)}`);
    const maxM = Math.max(...o.history.map((h) => h.mass), 1);
    body += '<div class="block-r"><h3>质量收敛</h3><div class="hist">' +
      o.history.map((h) => `<i style="height:${Math.max(3, (h.mass / maxM) * 50)}px" title="第${h.iteration}轮 ${fmt(h.mass, 1)} kg"></i>`).join('') +
      '</div><p class="note">共 ' + o.history.length + ' 轮选型，用时 ' + fmt(o.elapsed * 1000, 0) + ' ms；' +
      '剔除 ' + o.removed.length + ' 根杆件。</p></div>';
    if (o.removed.length) {
      body += '<div class="block-r"><h3>被剔除的杆件</h3><div class="chips">' +
        o.removed.slice(0, 40).map((i) => `<span class="chip">#${i}</span>`).join('') + '</div></div>';
    }
    body += '<div class="block-r"><h3>优化后校核</h3><p class="note">' +
      (S.opt.check.verdict === '通过' ? '全部指标满足要求。' : S.opt.check.issues.join('；')) + '</p></div>';
  }

  if (S.tab === 'scan') {
    if (!S.scan) { side.innerHTML = '<div class="empty">扫描中…</div>'; return; }
    if (S.scan.error) { side.innerHTML = `<div class="empty">${S.scan.error}</div>`; return; }
    const sc = S.scan.scan;
    head = `<h2>杆件重要度</h2><p>逐根拆除后重分析，共 ${sc.members.length} 次求解</p>`;
    kpis = kpi('整体冗余度', fmt(sc.degree * 100, 0), '%', sc.degree < 0.3 ? 'bad' : 'ok')
      + kpi('致命杆件', sc.fatal, '根（拆则失稳）')
      + kpi('关键杆件', sc.critical, '根')
      + kpi('用时', fmt(sc.elapsed * 1000, 0), 'ms');

    const fatal = sc.members.filter((m) => !m.stable);
    const safe = sc.members.filter((m) => m.stable && m.ratio_after > 1.0);
    const sound = sc.members.filter((m) => m.stable && m.ratio_after <= 1.0);

    if (fatal.length) {
      body += '<div class="block-r"><h3>失效即失稳（' + fatal.length + ' 根）</h3>' +
        '<p class="tbl-note">这些杆件位于承重链的必经路径上，任意一根失效都会让结构变成机构。' +
        '</p><div class="chips">' +
        fatal.map((m) => `<span class="chip fatal" data-member="${m.member}" style="cursor:pointer">#${m.member}</span>`).join('') +
        '</div></div>';
    }
    if (safe.length) {
      body += '<div class="block-r"><h3>拆除后超限（' + safe.length + ' 根）</h3>' +
        '<p class="tbl-note">结构仍稳定，但剩余杆件的利用率会越过 1.0。</p><table><thead><tr>' +
        '<th style="width:34px">#</th><th>节点</th><th style="width:56px">拆后</th></tr></thead><tbody>';
      safe.slice(0, 20).forEach((m) => {
        body += `<tr data-member="${m.member}" class="${S.selected === m.member ? 'sel' : ''}">` +
          `<td class="st-name">${m.member}</td><td>${m.i}–${m.j}</td>` +
          `<td class="st-bad">${fmt(m.ratio_after, 2)}</td></tr>`;
      });
      body += '</tbody></table></div>';
    }
    if (sound.length) {
      body += '<div class="block-r"><h3>可拆除（' + sound.length + ' 根）</h3>' +
        '<p class="tbl-note">这些杆件拆掉后结构仍然安全，说明它们对当前荷载工况贡献很小——' +
        '可能是优化时该剔除的对象。</p><table><thead><tr>' +
        '<th style="width:34px">#</th><th>节点</th><th style="width:56px">重要度</th></tr></thead><tbody>';
      sound.slice().sort((a, b) => a.severity - b.severity).slice(0, 20).forEach((m) => {
        body += `<tr data-member="${m.member}" class="${S.selected === m.member ? 'sel' : ''}">` +
          `<td class="st-name">${m.member}</td><td>${m.i}–${m.j}</td>` +
          `<td style="color:${RISK_COL[m.level]}">${fmt(m.severity, 2)}</td></tr>`;
      });
      body += '</tbody></table></div>';
    }
    if (!fatal.length && !sound.length && !safe.length) {
      body += '<div class="empty">没有杆件</div>';
    }
  }

  side.innerHTML = `<div class="side-head">${head}</div><div class="kpis">${kpis}</div>${body}`;
  bindSideEvents(side);
}

function bindSideEvents(side) {
  side.querySelectorAll('[data-member]').forEach((tr) => {
    tr.addEventListener('click', () => {
      S.selected = Number(tr.dataset.member);
      renderSide();
      draw();
    });
  });
  side.querySelectorAll('[data-case]').forEach((chip) => {
    chip.addEventListener('click', () => {
      S.caseIndex = Number(chip.dataset.case);
      renderSide();
      draw();
    });
  });
}

/* ------------------------------------------------------------------ 左栏 */

function renderLeft() {
  const d = S.analyze;
  if (!d) return;
  const st = d.model.stats;
  $('caseNote').textContent = d.case.note;
  $('caseStats').innerHTML = [
    ['节点', st.nodes], ['杆件', st.members], ['自由度', st.dof],
    ['跨度', (st.span / 1000).toFixed(1) + ' m'],
    ['总长', (st.length / 1000).toFixed(1) + ' m'],
    ['质量', st.mass.toFixed(1) + ' kg']
  ].map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');
}

function renderLegend() {
  const items = [];
  if (S.tab === 'scan' && S.scan && !S.scan.error) {
    Object.entries(RISK_COL).forEach(([k, v]) => {
      items.push(`<li><i class="sq" style="background:${v}"></i>${k}</li>`);
    });
  } else if (S.tab === 'check') {
    items.push(`<li><i style="background:${ratioColor(0.15)}"></i>利用率低</li>`);
    items.push(`<li><i style="background:${ratioColor(0.75)}"></i>接近限值</li>`);
    items.push(`<li><i style="background:${ratioColor(1.1)}"></i>超限</li>`);
  } else {
    items.push(`<li><i style="background:${COL.tension}"></i>受拉</li>`);
    items.push(`<li><i style="background:${COL.compression}"></i>受压</li>`);
    items.push(`<li><i class="sq" style="background:#d7e0eb"></i>未变形</li>`);
  }
  items.push(`<li><i class="sq" style="background:${COL.load}"></i>荷载方向</li>`);
  $('legend').innerHTML = items.join('');
}

function renderVerdict() {
  const d = viewPayload();
  const badge = $('verdict');
  if (!d) { badge.textContent = '—'; badge.className = 'verdict'; return; }
  const v = d.check.verdict;
  badge.textContent = `${d.case.label} · ${v}`;
  badge.className = 'verdict ' + (v === '通过' ? 'ok' : (v === '不稳定' ? 'bad' : 'bad'));
}

function render() {
  renderLeft();
  renderLegend();
  renderVerdict();
  renderSide();
  draw();
}

/* ------------------------------------------------------------------ 交互 */

function nearestMember(mx, my) {
  const d = viewPayload();
  if (!d) return null;
  const T = makeTransform(d.bbox, W, H, 56);
  let best = null, bestD = 10;
  d.model.members.forEach((m, i) => {
    const a = d.model.nodes[m.i], b = d.model.nodes[m.j];
    const ax = T.x(a.x), ay = T.y(a.y), bx = T.x(b.x), by = T.y(b.y);
    const dx = bx - ax, dy = by - ay;
    const len2 = dx * dx + dy * dy || 1;
    let t = ((mx - ax) * dx + (my - ay) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    const px = ax + t * dx, py = ay + t * dy;
    const dist = Math.hypot(mx - px, my - py);
    if (dist < bestD) { bestD = dist; best = i; }
  });
  return best;
}

function memberInfo(i) {
  const d = viewPayload();
  if (!d) return '';
  const m = d.model.members[i];
  const f = currentSolution().forces[i] || 0;
  const c = (d.check.members || []).find((x) => x.member === i);
  const lines = [
    `杆件 #${i}  (${m.i}–${m.j})`,
    `截面 ${m.section_label}`,
    `轴力 ${fmt(f / 1000, 2)} kN  ${f > 0 ? '受拉' : (f < 0 ? '受压' : '零杆')}`
  ];
  if (c) {
    lines.push(`λ = ${fmt(c.slenderness, 0)}`);
    lines.push(`利用率 ${fmt(c.ratio, 2)}`);
  }
  if (S.tab === 'scan' && S.scan && !S.scan.error) {
    const rec = S.scan.scan.members.find((x) => x.member === i);
    if (rec) lines.push(`拆除后 ${rec.stable ? '利用率 ' + fmt(rec.ratio_after, 2) : '成为机构'}（${rec.level}）`);
  }
  return lines.join('\n');
}

cv.addEventListener('mousemove', (ev) => {
  const rect = cv.getBoundingClientRect();
  const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
  const hit = nearestMember(mx, my);
  const tip = $('tip');
  if (hit === null) {
    if (S.hover !== null) { S.hover = null; cv.style.cursor = 'default'; }
    tip.hidden = true;
    return;
  }
  S.hover = hit;
  cv.style.cursor = 'pointer';
  tip.hidden = false;
  tip.textContent = memberInfo(hit);
  tip.style.left = mx + 'px';
  tip.style.top = my + 'px';
});

cv.addEventListener('mouseleave', () => { $('tip').hidden = true; });

cv.addEventListener('click', () => {
  if (S.hover === null) return;
  S.selected = S.selected === S.hover ? null : S.hover;
  renderSide();
  draw();
});

/* ------------------------------------------------------------------ 控件 */

$('tabs').addEventListener('click', (ev) => {
  const btn = ev.target.closest('button[data-tab]');
  if (!btn) return;
  const tab = btn.dataset.tab;
  if (tab === S.tab) return;
  S.tab = tab;
  [...$('tabs').children].forEach((b) => b.classList.toggle('on', b === btn));
  S.selected = null;
  if (tab === 'opt' || tab === 'scan') ensureTab(tab);
  else render();
});

$('scale').addEventListener('input', (ev) => {
  S.view.scale = Number(ev.target.value);
  $('scaleLabel').textContent = '×' + S.view.scale;
  draw();
});

$('anim').addEventListener('change', (ev) => { S.view.anim = ev.target.checked; draw(); });
$('labels').addEventListener('change', (ev) => { S.view.labels = ev.target.checked; draw(); });
$('ghost').addEventListener('change', (ev) => { S.view.ghost = ev.target.checked; draw(); });

$('apply').addEventListener('click', () => {
  S.params.gamma = Number($('gamma').value) || 1.5;
  S.params.defl = Number($('defl').value) || 250;
  S.params.prune = Number($('prune').value) || 0.2;
  S.opt = null;
  S.scan = null;
  loadCase(S.caseName);
});

window.addEventListener('resize', resize);

function tick() {
  if (S.view.anim && S.tab === 'deform') {
    S.t = (S.t + 0.03) % (Math.PI * 2);
    draw();
  }
  requestAnimationFrame(tick);
}

boot();
requestAnimationFrame(tick);
setTimeout(resize, 0);
window.addEventListener('load', resize);
