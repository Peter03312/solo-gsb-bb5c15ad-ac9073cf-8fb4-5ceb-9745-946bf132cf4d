'use strict';
/* Shared-kitchen allergen scheduler frontend (vanilla JS, no build step). */

const state = {
  allergens: [],
  equipment: [],
  batches: [],
  cleanings: [],
  schedule: null,
  order: [], // desired batch priority
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function toast(msg, isErr = false) {
  const el = $('#toast');
  el.textContent = msg;
  el.className = 'toast show' + (isErr ? ' err' : '');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.className = 'toast'; }, 2600);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (_) { data = text; }
  if (!res.ok) {
    throw new Error((data && data.error) || `请求失败 (${res.status})`);
  }
  return data;
}

const fmtTime = (s) => (s || '').replace('T', ' ').slice(0, 16);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ------------------------------- tabs ------------------------------- */

$$('#tabs button').forEach((btn) => {
  btn.addEventListener('click', () => {
    $$('#tabs button').forEach((b) => b.classList.toggle('active', b === btn));
    const tab = btn.dataset.tab;
    $$('.tab').forEach((t) => t.classList.toggle('active', t.id === `tab-${tab}`));
  });
});

/* ----------------------------- reference ----------------------------- */

async function loadAll() {
  const [allergens, equipment, batches, cleanings, latest] = await Promise.all([
    api('/api/allergens'),
    api('/api/equipment'),
    api('/api/batches'),
    api('/api/cleanings'),
    api('/api/schedule/latest'),
  ]);
  state.allergens = allergens;
  state.equipment = equipment;
  state.batches = batches;
  state.cleanings = cleanings;
  state.schedule = latest;
  if (!state.order.length || !state.order.every((id) => batches.some((b) => b.id === id))
      || state.order.length !== batches.length) {
    state.order = [...batches]
      .sort((a, b) => (a.planned_start < b.planned_start ? -1 : 1))
      .map((b) => b.id);
  }
  renderAll();
}

/* --------------------------- chips & forms --------------------------- */

function renderChips(container, items, name, selected = []) {
  container.innerHTML = items.map((it) => {
    const checked = selected.includes(it.id) ? 'checked' : '';
    const label = it.name_zh ? `${it.name_zh}${it.name_en ? ` (${it.name_en})` : ''}` : it.name;
    return `<label><input type="checkbox" name="${name}" value="${it.id}" ${checked}>${esc(label)}</label>`;
  }).join('');
}

function checkedValues(form, name) {
  return $$(`input[name="${name}"]:checked`, form).map((el) => Number(el.value));
}

function renderForms() {
  // batch form
  $$('#form-batch .chips').forEach((box) => {
    if (box.dataset.name === 'allergen_ids') renderChips(box, state.allergens, 'allergen_ids');
    if (box.dataset.name === 'equipment_ids') renderChips(box, state.equipment, 'equipment_ids');
  });
  // cleaning form
  const equipSel = $('#form-cleaning select[name="equipment_id"]');
  equipSel.innerHTML = '<option value="">请选择…</option>' +
    state.equipment.map((e) => `<option value="${e.id}">${esc(e.name)}</option>`).join('');
  const coverBox = $('#form-cleaning .chips');
  renderChips(coverBox, state.allergens, 'cleaning_allergen_ids');
  const coverAll = $('#form-cleaning input[name="covers_all"]');
  const syncCover = () => { $('#covers-field').style.opacity = coverAll.checked ? .45 : 1; };
  coverAll.onchange = syncCover; syncCover();
}

/* ----------------------------- entity UI ----------------------------- */

function renderBatches() {
  $('#batch-count').textContent = `共 ${state.batches.length} 条`;
  const el = $('#batch-list');
  if (!state.batches.length) {
    el.innerHTML = '<div class="empty">暂无批次，请先在上方录入或点击右上角“载入演示数据”。</div>';
    return;
  }
  el.innerHTML = state.batches.map((b) => `
    <div class="entity">
      <div class="head">
        <div><span class="title">${esc(b.name)}</span>
          <span class="time">#${b.id} · ${fmtTime(b.planned_start)} ~ ${fmtTime(b.planned_end)}</span></div>
        <button class="btn tiny danger" data-del-batch="${b.id}">删除</button>
      </div>
      <div class="tags">
        ${b.allergens.map((a) => `<span class="tag al">${esc(a.name)}</span>`).join('')}
        ${b.equipment.map((e) => `<span class="tag eq">${esc(e.name)}</span>`).join('')}
        ${b.notes ? `<span class="tag">${esc(b.notes)}</span>` : ''}
      </div>
    </div>`).join('');
  $$('[data-del-batch]', el).forEach((btn) => btn.addEventListener('click', async () => {
    if (!confirm('删除该批次？')) return;
    await api(`/api/batches/${btn.dataset.delBatch}`, { method: 'DELETE' });
    toast('批次已删除');
    await loadAll();
  }));
}

function renderCleanings() {
  $('#cleaning-count').textContent = `共 ${state.cleanings.length} 条`;
  const el = $('#cleaning-list');
  if (!state.cleanings.length) {
    el.innerHTML = '<div class="empty">暂无清洁记录。</div>';
    return;
  }
  el.innerHTML = state.cleanings.map((c) => `
    <div class="entity">
      <div class="head">
        <div><span class="title">${esc(c.equipment_name)}</span>
          <span class="time">#${c.id} · 完成 ${fmtTime(c.cleaned_at)} · 有效至 ${fmtTime(c.valid_until)}</span></div>
        <div>
          <span class="tag ${c.currently_valid ? 'valid' : 'invalid'}">
            ${c.currently_valid ? '当前仍有效' : '已失效'}</span>
          <button class="btn tiny danger" data-del-cleaning="${c.id}">删除</button>
        </div>
      </div>
      <div class="tags">
        <span class="tag eq">${esc(c.method || '未注明方式')}</span>
        ${c.covers_all ? '<span class="tag valid">覆盖全部过敏原</span>'
          : c.allergens.map((a) => `<span class="tag al">${esc(a.name)}</span>`).join('')}
        ${c.notes ? `<span class="tag">${esc(c.notes)}</span>` : ''}
      </div>
    </div>`).join('');
  $$('[data-del-cleaning]', el).forEach((btn) => btn.addEventListener('click', async () => {
    if (!confirm('删除该清洁记录？')) return;
    await api(`/api/cleanings/${btn.dataset.delCleaning}`, { method: 'DELETE' });
    toast('清洁记录已删除');
    await loadAll();
  }));
}

function renderRefLists() {
  $('#equipment-list').innerHTML = state.equipment.length
    ? state.equipment.map((e) => `<li><span>${esc(e.name)}</span><span class="ty">${esc(e.equip_type || '—')}</span></li>`).join('')
    : '<li class="empty">暂无</li>';
  $('#allergen-list').innerHTML = state.allergens.map((a) => `
    <li><span>${esc(a.name_zh)}</span><span class="ty">${esc(a.name_en || (a.is_builtin ? '内置' : '自定义'))}</span></li>`).join('');
}

/* ----------------------------- schedule ------------------------------ */

function batchById(id) { return state.batches.find((b) => b.id === id); }

function renderOrder() {
  const el = $('#order-list');
  if (!state.order.length) {
    el.innerHTML = '<div class="empty">暂无可排入的批次。</div>';
    return;
  }
  el.innerHTML = state.order.map((id, i) => {
    const b = batchById(id);
    if (!b) return '';
    return `<li>
      <span class="idx">${i + 1}</span>
      <span class="nm">${esc(b.name)}</span>
      <span class="tm">${fmtTime(b.planned_start)}–${fmtTime(b.planned_end)}</span>
      <button class="btn tiny" data-move="${id}" data-dir="up" ${i === 0 ? 'disabled' : ''}>↑</button>
      <button class="btn tiny" data-move="${id}" data-dir="down" ${i === state.order.length - 1 ? 'disabled' : ''}>↓</button>
    </li>`;
  }).join('');
  $$('[data-move]', el).forEach((btn) => btn.addEventListener('click', () => {
    const id = Number(btn.dataset.move);
    const i = state.order.indexOf(id);
    const j = btn.dataset.dir === 'up' ? i - 1 : i + 1;
    if (j < 0 || j >= state.order.length) return;
    [state.order[i], state.order[j]] = [state.order[j], state.order[i]];
    renderOrder();
  }));
}

function riskBlock(risks) {
  if (!risks || !risks.length) return '';
  return `<details class="risks" open>
    <summary>⚠ ${risks.length} 处受控残留风险（已由有效期内清洁覆盖，点击展开来源）</summary>
    ${risks.map((r) => `
      <div class="risk-item">
        <span class="src">[${esc(r.equipment)} · ${esc(r.allergen)}]</span>
        ${esc(r.message)}
      </div>`).join('')}
  </details>`;
}

function renderSchedule() {
  const s = state.schedule;
  $('#run-time').textContent = s ? `最近计算：${fmtTime(s.generated_at)}` : '尚未计算';
  if (!s) {
    $('#schedule-stats').innerHTML = '';
    $('#scheduled-list').innerHTML = '<div class="empty">点击“生成排程”查看结果。</div>';
    $('#blocked-list').innerHTML = '';
    return;
  }
  $('#schedule-stats').innerHTML = `
    <div class="stat s-ok">可执行<b>${s.summary.scheduled_count}</b></div>
    <div class="stat s-bad">阻塞<b>${s.summary.blocked_count}</b></div>
    <div class="stat s-warn">受控风险点<b>${s.summary.controlled_risk_count}</b></div>`;

  $('#scheduled-list').innerHTML = s.scheduled.length ? s.scheduled.map((it) => `
    <div class="result ${it.risk_level === 'controlled' ? 'controlled' : 'ok'}">
      <div class="head">
        <span class="pos">#${it.position}</span>
        <strong>${esc(it.name)}</strong>
        <span class="badge ${it.risk_level === 'controlled' ? 'controlled' : 'ok'}">
          ${it.risk_level === 'controlled' ? '受控 · 可执行' : '清洁 · 可执行'}</span>
        <span class="tm">${fmtTime(it.planned_start)}–${fmtTime(it.planned_end)}</span>
      </div>
      <div class="tags">
        ${it.allergens.map((a) => `<span class="tag al">${esc(a)}</span>`).join('')}
        ${it.equipment.map((e) => `<span class="tag eq">${esc(e)}</span>`).join('')}
      </div>
      ${riskBlock(it.risks)}
    </div>`).join('') : '<div class="empty">没有可执行批次。</div>';

  $('#blocked-list').innerHTML = s.blocked.length ? s.blocked.map((it) => `
    <div class="result blocked">
      <div class="head">
        <span class="pos">#${it.position}</span>
        <strong>${esc(it.name)}</strong>
        <span class="badge blocked">已拒绝排入</span>
        <span class="tm">${fmtTime(it.planned_start)}–${fmtTime(it.planned_end)}</span>
      </div>
      <div class="tags">
        ${it.allergens.map((a) => `<span class="tag al">${esc(a)}</span>`).join('')}
        ${it.equipment.map((e) => `<span class="tag eq">${esc(e)}</span>`).join('')}
      </div>
      <ul class="reasons">
        ${it.reasons.map((r) => `<li><span class="code">${esc(r.code)}</span>${esc(r.message)}</li>`).join('')}
      </ul>
      ${riskBlock(it.risks)}
    </div>`).join('') : '<div class="empty">✓ 没有阻塞批次。</div>';
}

function renderAll() {
  renderForms();
  renderBatches();
  renderCleanings();
  renderRefLists();
  renderOrder();
  renderSchedule();
}

/* ------------------------------ actions ------------------------------ */

$('#btn-run').addEventListener('click', async () => {
  try {
    state.schedule = await api('/api/schedule/run', {
      method: 'POST', body: JSON.stringify({ order: state.order }),
    });
    state.order = [...state.schedule.order];
    renderOrder();
    renderSchedule();
    toast(`排程完成：${state.schedule.summary.scheduled_count} 可执行 / `
      + `${state.schedule.summary.blocked_count} 阻塞`);
  } catch (e) { toast(e.message, true); }
});

$('#btn-export-csv').addEventListener('click', () => { window.location.href = '/api/schedule/export?format=csv'; });
$('#btn-export-md').addEventListener('click', () => { window.location.href = '/api/schedule/export?format=md'; });

$('#btn-seed').addEventListener('click', async () => {
  if (!confirm('载入演示数据将清空当前业务数据（内置过敏原保留），是否继续？')) return;
  try {
    await api('/api/admin/seed-demo', { method: 'POST' });
    state.order = [];
    await loadAll();
    toast('演示数据已载入，可点击“生成排程”');
  } catch (e) { toast(e.message, true); }
});

/* forms */
$('#form-batch').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  try {
    await api('/api/batches', {
      method: 'POST',
      body: JSON.stringify({
        name: f.name.value,
        planned_start: f.planned_start.value,
        planned_end: f.planned_end.value,
        notes: f.notes.value,
        allergen_ids: checkedValues(f, 'allergen_ids'),
        equipment_ids: checkedValues(f, 'equipment_ids'),
      }),
    });
    f.reset();
    state.order = [];
    toast('批次已保存');
    await loadAll();
  } catch (e) { toast(e.message, true); }
});

$('#form-cleaning').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  try {
    const coversAll = f.covers_all.checked;
    await api('/api/cleanings', {
      method: 'POST',
      body: JSON.stringify({
        equipment_id: Number(f.equipment_id.value),
        method: f.method.value,
        cleaned_at: f.cleaned_at.value,
        valid_until: f.valid_until.value,
        covers_all: coversAll,
        allergen_ids: coversAll ? [] : checkedValues(f, 'cleaning_allergen_ids'),
        notes: f.notes.value,
      }),
    });
    f.reset();
    $('#covers-field').style.opacity = 1;
    toast('清洁记录已保存');
    await loadAll();
  } catch (e) { toast(e.message, true); }
});

$('#form-equipment').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  try {
    await api('/api/equipment', {
      method: 'POST',
      body: JSON.stringify({ name: f.name.value, equip_type: f.equip_type.value,
        code: f.name.value }),
    });
    f.reset();
    toast('器具已保存');
    await loadAll();
  } catch (e) { toast(e.message, true); }
});

$('#form-allergen').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const f = ev.target;
  try {
    await api('/api/allergens', {
      method: 'POST',
      body: JSON.stringify({ name_zh: f.name_zh.value, name_en: f.name_en.value,
        code: f.name_en.value || f.name_zh.value }),
    });
    f.reset();
    toast('过敏原已保存');
    await loadAll();
  } catch (e) { toast(e.message, true); }
});

/* ------------------------------- boot -------------------------------- */

(async function boot() {
  try {
    const meta = await api('/api/meta');
    $('#svc-port').textContent = meta.port;
    const a = $('#svc-url');
    a.textContent = meta.public_url;
    a.href = meta.public_url;
  } catch (e) {
    $('#svc-url').textContent = '元信息获取失败';
  }
  try {
    await loadAll();
  } catch (e) {
    toast(e.message, true);
  }
})();
