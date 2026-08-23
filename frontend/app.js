const $ = (id) => document.getElementById(id);
const formJSON = (form) => Object.fromEntries(new FormData(form).entries());
const nullable = (value) => value === '' ? null : value;
function normalizeDateTimeInput(value) {
  const raw = String(value ?? '').trim();
  if (!raw) return null;
  let match = raw.match(/^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})?$/);
  if (!match) match = raw.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?$/);
  if (!match) return raw;
  const [, year, month, day, hour, minute, second] = match;
  const numbers = [year, month, day, hour, minute, second || '0'].map(Number);
  const [yearNumber, monthNumber, dayNumber, hourNumber, minuteNumber, secondNumber] = numbers;
  if (monthNumber < 1 || monthNumber > 12 || dayNumber < 1 || dayNumber > 31 || hourNumber > 23 || minuteNumber > 59 || secondNumber > 59) return raw;
  const pad = number => String(number).padStart(2, '0');
  return `${yearNumber}-${pad(monthNumber)}-${pad(dayNumber)}T${pad(hourNumber)}:${pad(minuteNumber)}${second ? `:${pad(secondNumber)}` : ''}`;
}
let currentVoyageId = null;
let currentCrew = [];
let imoLookupTimer = null;
let savedCrewChanges = [];
let pendingDownCrew = [];
let pendingUpCrew = [];
let editingDownChangeId = null;
let editingUpChangeId = null;
let currentVoyages = [];
let currentVessels = [];
let editingVoyageId = null;
let parsedVesselTextExtraction = null;
let parsedVoyageTextExtraction = null;
let extractedVesselExtra = {};
let extractedVoyageExtra = {};
const requestedVoyageId = Number(new URLSearchParams(window.location.search).get('voyage')) || null;

function setMsg(id, text, error = false) {
  const node = $(id); node.textContent = text; node.className = `message ${error ? 'error' : 'success'}`;
}

function voyageFormDateTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 16) : '';
}

function fillVoyageForm(voyage) {
  const form = $('voyageForm');
  $('vesselSearch').value = '';
  renderVesselOptions();
  for (const key of ['vessel_id', 'inbound_voyage_no', 'outbound_voyage_no', 'berth', 'previous_port', 'previous_port_country', 'next_port', 'next_port_country', 'route', 'entry_type']) {
    if (form.elements[key]) form.elements[key].value = voyage[key] ?? '';
  }
  for (const key of ['arrival_time', 'departure_time', 'previous_port_departure_time']) {
    if (form.elements[key]) form.elements[key].value = voyageFormDateTime(voyage[key]);
  }
  syncVesselSearch(voyage.vessel_id);
  const vessel = currentVessels.find(item => item.id === Number(voyage.vessel_id));
  if (vessel) {
    fillVesselForm(vessel);
    setMsg('vesselMsg', `已同步当前航次的船舶档案：${vessel.chinese_name || vessel.english_name || vessel.imo || ''}`);
  }
}

function vesselDisplay(vessel) {
  return `${vessel.chinese_name || ''} ${vessel.english_name || ''}｜IMO ${vessel.imo || '未填写'}`.trim();
}

function syncVesselSearch(vesselId) {
  const vessel = currentVessels.find(item => item.id === Number(vesselId));
  if ($('vesselSearch')) $('vesselSearch').value = vessel ? vesselDisplay(vessel) : '';
}

function renderVesselOptions(keyword = '') {
  const select = $('vesselSelect');
  if (!select) return;
  const key = String(keyword || '').trim().toLowerCase();
  const matches = currentVessels.filter(vessel => !key || [vessel.chinese_name, vessel.english_name, vessel.imo].some(value => String(value || '').toLowerCase().includes(key)));
  const currentId = select.value;
  select.innerHTML = matches.length
    ? matches.map(vessel => `<option value="${vessel.id}">${vessel.id}｜${vessel.chinese_name || ''} ${vessel.english_name || ''}｜IMO ${vessel.imo || '未填写'}</option>`).join('')
    : '<option value="">没有匹配的船舶</option>';
  if (matches.some(vessel => String(vessel.id) === currentId)) select.value = currentId;
  else if (matches.length === 1) select.value = String(matches[0].id);
  else select.value = '';
}

function setVoyageEditMode(voyage) {
  editingVoyageId = voyage?.id || null;
  if (voyage) {
    fillVoyageForm(voyage);
    $('saveVoyageBtn').textContent = '保存当前航次';
    $('voyageResumeMsg').textContent = `当前航次：${voyage.inbound_voyage_no || '未填进港航次'} → ${voyage.outbound_voyage_no || '未填出港航次'}，已自动恢复历史资料。`;
  } else {
    $('voyageForm').reset();
    $('saveVoyageBtn').textContent = '保存新航次';
    $('voyageResumeMsg').textContent = '正在新建航次，保存后会自动进入历史记录。';
  }
}

async function refresh(preferredVoyageId = null) {
  currentVessels = await fetch('/api/vessels').then(r => r.json());
  $('vesselSearch').value = '';
  renderVesselOptions();
  currentVoyages = await fetch('/api/voyages').then(r => r.json());
  $('voyageSelect').innerHTML = currentVoyages.map(v => `<option value="${v.id}">${v.id}｜${v.inbound_voyage_no || ''} → ${v.outbound_voyage_no || ''}</option>`).join('');
  const preferred = preferredVoyageId || currentVoyageId || requestedVoyageId;
  const voyage = currentVoyages.find(item => item.id === Number(preferred)) || currentVoyages[0] || null;
  currentVoyageId = voyage?.id || null;
  if (voyage) {
    $('voyageSelect').value = String(voyage.id);
    setVoyageEditMode(voyage);
  } else setVoyageEditMode(null);
  await loadCrewOptions();
}

async function loadCrewOptions() {
  if (!currentVoyageId) {
    $('downCrewSelect').innerHTML = '<option value="">请先选择航次</option>';
    $('downCrewSelect').disabled = true;
    $('crewRosterMsg').textContent = '';
    return;
  }
  const res = await fetch(`/api/voyages/${currentVoyageId}/summary`);
  if (!res.ok) {
    $('downCrewSelect').innerHTML = '<option value="">无法读取船员名单</option>';
    $('downCrewSelect').disabled = true;
    return;
  }
  const data = await res.json(); currentCrew = data.crew || []; savedCrewChanges = data.crew_change || [];
  $('downCrewSelect').innerHTML = currentCrew.length
    ? currentCrew.map(c => `<option value="${c.id}">${c.name}｜${c.nationality || ''}｜${c.rank || ''}</option>`).join('')
    : '<option value="">请先导入船员名单</option>';
  $('downCrewSelect').disabled = currentCrew.length === 0;
  $('crewRosterMsg').textContent = currentCrew.length ? `当前名单 ${currentCrew.length} 人` : '尚未导入名单';
  renderDownPreview();
  renderCrewChangeLists();
}

function selectedCrew() {
  return currentCrew.find(c => String(c.id) === $('downCrewSelect').value);
}

function renderDownPreview() {
  const selected = selectedCrew();
  $('downCrewPreview').textContent = selected
    ? `姓名：${selected.name || ''}　国籍：${selected.nationality || ''}　性别：${selected.gender || ''}　出生日期：${selected.birth_date || ''}　证件号：${selected.document_no || ''}　职务：${selected.rank || ''}`
    : '请先导入船员名单。';
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
}

function changeLabel(person) {
  const extras = person.direction === 'down'
    ? `事由：${person.reason || '未填写'}｜临入：${person.temporary_entry_permit ? '是' : '否'}${person.flight_no ? `｜航班：${person.flight_no}` : ''}${person.flight_time ? `｜时间：${person.flight_time.replace('T', ' ').slice(0, 16)}` : ''}${person.route ? `｜航线：${person.route}` : ''}`
    : `职务：${person.rank || '未填写'}`;
  return `${person.name || ''}｜${person.nationality || ''}｜${person.gender || ''}｜出生：${person.birth_date || '未填写'}｜证件号：${person.document_no || ''}｜${extras}`;
}

function renderChangeList(targetId, pending, direction) {
  const saved = savedCrewChanges.filter(person => person.direction === direction);
  const rows = [
    ...pending.map((person, index) => `<div class="change-item pending"><span><b>待保存</b> ${escapeHtml(changeLabel(person))}</span><button class="danger small-button" type="button" data-remove-direction="${direction}" data-remove-index="${index}">移除</button></div>`),
    ...saved.map(person => `<div class="change-item"><span><b>已保存</b> ${escapeHtml(changeLabel(person))}</span><span class="table-actions"><button class="ghost small-button" type="button" data-edit-id="${person.id}" data-edit-direction="${direction}">编辑</button><button class="danger small-button" type="button" data-delete-id="${person.id}" data-delete-direction="${direction}">删除</button></span></div>`),
  ];
  $(targetId).innerHTML = rows.length ? rows.join('') : '<p class="muted">暂未添加人员</p>';
}

function renderCrewChangeLists() {
  renderChangeList('downCrewList', pendingDownCrew, 'down');
  renderChangeList('upCrewList', pendingUpCrew, 'up');
}

function dateInputValue(value) {
  return value ? String(value).slice(0, 10) : '';
}

function dateTimeInputValue(value) {
  return value ? String(value).replace('Z', '').slice(0, 16) : '';
}

function resetDownEdit() {
  editingDownChangeId = null;
  $('downCrewForm').reset();
  $('downCrewSubmitBtn').textContent = '加入下船名单';
  $('cancelDownEditBtn').hidden = true;
  renderDownPreview();
}

function resetUpEdit() {
  editingUpChangeId = null;
  $('upCrewForm').reset();
  $('upCrewSubmitBtn').textContent = '加入上船名单';
  $('cancelUpEditBtn').hidden = true;
}

function startCrewChangeEdit(person) {
  if (person.direction === 'down') {
    editingDownChangeId = person.id;
    const crew = currentCrew.find(item => item.document_no === person.document_no);
    $('downCrewSelect').value = crew ? String(crew.id) : '';
    $('downCrewForm').elements.reason.value = person.reason || '';
    $('downCrewForm').elements.temporary_entry_permit.value = person.temporary_entry_permit ? 'true' : 'false';
    $('downCrewForm').elements.flight_no.value = person.flight_no || '';
    $('downCrewForm').elements.flight_time.value = dateTimeInputValue(person.flight_time);
    $('downCrewForm').elements.route.value = person.route || '';
    $('downCrewSubmitBtn').textContent = '保存下船修改';
    $('cancelDownEditBtn').hidden = false;
    renderDownPreview();
    $('downCrewForm').scrollIntoView({behavior:'smooth', block:'center'});
  } else {
    editingUpChangeId = person.id;
    for (const key of ['name', 'nationality', 'gender', 'document_no', 'rank']) $('upCrewForm').elements[key].value = person[key] || '';
    $('upCrewForm').elements.birth_date.value = dateInputValue(person.birth_date);
    $('upCrewSubmitBtn').textContent = '保存上船修改';
    $('cancelUpEditBtn').hidden = false;
    $('upCrewForm').scrollIntoView({behavior:'smooth', block:'center'});
  }
}

async function deleteSavedCrewChange(id, direction) {
  if (!window.confirm('确定删除这条换班人员记录吗？')) return;
  const res = await fetch(`/api/crew-change/${id}`, {method:'DELETE'});
  if (!res.ok) return setMsg(`${direction}CrewMsg`, await res.text(), true);
  if (direction === 'down') resetDownEdit(); else resetUpEdit();
  await loadCrewOptions();
  setMsg(`${direction}CrewMsg`, '换班人员记录已删除');
}

function fillVesselForm(vessel) {
  const form = $('vesselForm');
  for (const key of ['imo', 'chinese_name', 'english_name', 'nationality', 'call_sign', 'shipping_company', 'net_tonnage', 'gross_tonnage', 'mmsi']) {
    const input = form.elements[key];
    if (input) input.value = vessel[key] ?? '';
  }
}

function fillVesselFormPartial(vessel) {
  const form = $('vesselForm');
  for (const key of ['imo', 'chinese_name', 'english_name', 'nationality', 'call_sign', 'shipping_company', 'net_tonnage', 'gross_tonnage', 'mmsi']) {
    const input = form.elements[key];
    if (input && vessel[key] !== undefined && vessel[key] !== null && vessel[key] !== '') input.value = vessel[key];
  }
}

function fillVoyageFormPartial(voyage) {
  const form = $('voyageForm');
  for (const key of ['vessel_id', 'inbound_voyage_no', 'outbound_voyage_no', 'berth', 'previous_port', 'previous_port_country', 'next_port', 'next_port_country', 'route', 'entry_type']) {
    if (form.elements[key] && voyage[key] !== undefined && voyage[key] !== null && voyage[key] !== '') form.elements[key].value = voyage[key];
  }
  for (const key of ['arrival_time', 'departure_time', 'previous_port_departure_time']) {
    if (form.elements[key] && voyage[key]) form.elements[key].value = voyageFormDateTime(voyage[key]);
  }
  if (voyage.vessel_id) syncVesselSearch(voyage.vessel_id);
}

function extractedVesselMatch(vessel) {
  const extra = vessel.extra || {};
  return currentVessels.find(item => {
    const itemExtra = item.extra || {};
    return (vessel.mmsi && item.mmsi === vessel.mmsi)
      || (vessel.chinese_name && item.chinese_name === vessel.chinese_name)
      || (vessel.english_name && String(item.english_name || '').toLowerCase() === String(vessel.english_name || '').toLowerCase())
      || (extra.ship_system_no && itemExtra.ship_system_no === extra.ship_system_no);
  }) || null;
}

function renderTextExtractResult(data, ids) {
  $(ids.type).textContent = data.kind_label;
  const vesselFields = ['vessel.imo', 'vessel.chinese_name', 'vessel.english_name', 'vessel.nationality', 'vessel.call_sign', 'vessel.net_tonnage', 'vessel.gross_tonnage', 'vessel.mmsi'];
  const voyageFields = ['voyage.inbound_voyage_no', 'voyage.outbound_voyage_no', 'voyage.arrival_time', 'voyage.departure_time', 'voyage.berth', 'voyage.previous_port', 'voyage.next_port', 'voyage.route'];
  const visibleFields = (data.recognized || []).filter(item => (ids.applyLabel === '填入船舶档案' ? vesselFields : voyageFields).includes(item.field)).slice(0, 8);
  const fields = visibleFields.map(item => `<div class="extract-field"><b>${escapeHtml(item.label)}</b><span>${escapeHtml(item.value)}</span></div>`).join('');
  const summary = visibleFields.length ? `已显示 ${visibleFields.length} 个核心字段` : '核心字段暂无结果';
  $(ids.result).innerHTML = `<div class="extract-section"><h3>${summary}（共识别 ${data.recognized_count} 个）</h3><div class="extract-grid">${fields || '<span class="muted">请直接点击填入按钮，其他字段仍会正常处理</span>'}</div></div><p class="muted">其余字段不在页面展开显示，点击“${ids.applyLabel}”即可填入。</p>`;
  $(ids.result).hidden = false;
  $(ids.apply).disabled = false;
}

function vesselHasData(vessel) {
  return Object.keys(vessel || {}).some(key => key !== 'extra' && vessel[key]) || Object.keys(vessel?.extra || {}).length > 0;
}

function applyVesselTextExtraction() {
  if (!parsedVesselTextExtraction) return;
  const vessel = parsedVesselTextExtraction.vessel || {};
  fillVesselFormPartial(vessel);
  extractedVesselExtra = vessel.extra || {};
  setMsg('vesselTextExtractMsg', '船舶文字已填入船舶档案表单，请检查后保存。');
}

function applyVoyageTextExtraction() {
  if (!parsedVoyageTextExtraction) return;
  const vessel = parsedVoyageTextExtraction.vessel || {};
  const voyage = parsedVoyageTextExtraction.voyage || {};
  if (vesselHasData(vessel)) fillVesselFormPartial(vessel);
  if (vesselHasData(vessel)) extractedVesselExtra = {...extractedVesselExtra, ...(vessel.extra || {})};
  extractedVoyageExtra = voyage.extra || {};
  const matchedVessel = extractedVesselMatch(vessel);
  if (matchedVessel) {
    voyage.vessel_id = matchedVessel.id;
    $('vesselSelect').value = String(matchedVessel.id);
    syncVesselSearch(matchedVessel.id);
  } else if (Object.keys(vessel).some(key => key !== 'extra' && vessel[key])) {
    $('vesselSelect').value = '';
    $('vesselSearch').value = '';
  }
  fillVoyageFormPartial(voyage);
  const message = matchedVessel
    ? '航次文字已填入航次管理表单，请检查后保存。'
    : '航次文字已填入表单；未匹配到历史船舶，请先保存船舶档案，再选择船舶保存航次。';
  setMsg('voyageTextExtractMsg', message, !matchedVessel && Object.keys(voyage).length > 1);
}

async function lookupVesselByIMO() {
  const imo = $('vesselForm').elements.imo.value.trim();
  if (imo.length < 7) return;
  const res = await fetch(`/api/vessels/by-imo?imo=${encodeURIComponent(imo)}`);
  if (res.ok) {
    const vessel = await res.json();
    fillVesselForm(vessel);
    setMsg('vesselMsg', `已带入历史船舶档案：${vessel.chinese_name || vessel.english_name || imo}`);
  } else if (res.status === 404) {
    setMsg('vesselMsg', '未找到该IMO历史档案，可以继续人工录入');
  }
}

$('vesselForm').elements.imo.addEventListener('input', () => {
  clearTimeout(imoLookupTimer);
  imoLookupTimer = setTimeout(lookupVesselByIMO, 350);
});

$('vesselForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const body = formJSON(event.target);
  body.extra = {...extractedVesselExtra};
  for (const key of ['imo','chinese_name','english_name','nationality','call_sign','shipping_company','mmsi']) body[key] = nullable(body[key]);
  for (const key of ['net_tonnage','gross_tonnage']) body[key] = body[key] ? Number(body[key]) : null;
  const res = await fetch('/api/vessels', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (!res.ok) return setMsg('vesselMsg', await res.text(), true);
  const savedVessel = await res.json();
  extractedVesselExtra = {};
  await refresh();
  fillVesselForm(savedVessel);
  setMsg('vesselMsg', `船舶档案已新增：${savedVessel.chinese_name || savedVessel.english_name || savedVessel.imo || ''}；如要使用该船，请点击“新建航次”`);
});

$('voyageForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const body = formJSON(event.target);
  body.extra = {...extractedVoyageExtra};
  if (!body.vessel_id) return setMsg('voyageMsg', '请先保存或选择船舶档案，再保存航次', true);
  body.vessel_id = Number(body.vessel_id); body.crew_change = false;
  for (const key of ['arrival_time','departure_time','previous_port_departure_time']) body[key] = normalizeDateTimeInput(body[key]);
  body.entry_type = nullable(body.entry_type);
  const url = editingVoyageId ? `/api/voyages/${editingVoyageId}` : '/api/voyages';
  const method = editingVoyageId ? 'PUT' : 'POST';
  const res = await fetch(url, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (res.ok) { const item = await res.json(); currentVoyageId = item.id; await refresh(item.id); $('voyageSelect').value = String(item.id); }
  setMsg('voyageMsg', res.ok ? '航次已保存' : await res.text(), !res.ok); if (res.ok) extractedVoyageExtra = {};
});

$('newVoyageBtn').addEventListener('click', async () => {
  currentVoyageId = null; pendingDownCrew = []; pendingUpCrew = []; resetDownEdit(); resetUpEdit(); setVoyageEditMode(null); $('vesselSearch').value = ''; renderVesselOptions(); await loadCrewOptions();
  setMsg('voyageMsg', '已切换到新建航次');
});

$('newVesselBtn').addEventListener('click', () => {
  $('vesselForm').reset();
  extractedVesselExtra = {};
  setMsg('vesselMsg', '已切换到新建船舶模式，请填写后保存');
  $('vesselForm').elements.imo.focus();
});

$('crewFile').addEventListener('change', async (event) => {
  if (!currentVoyageId || !event.target.files[0]) return setMsg('toolMsg', '请先保存并选择航次', true);
  const data = new FormData(); data.append('file', event.target.files[0]);
  const res = await fetch(`/api/voyages/${currentVoyageId}/crew/import`, {method:'POST', body:data});
  if (res.ok) {
    const result = await res.json();
    setMsg('toolMsg', `船员名单已导入：${result.count}人`);
    await loadCrewOptions();
  } else setMsg('toolMsg', await res.text(), true);
});

$('sourceImage').addEventListener('change', async (event) => {
  if (!currentVoyageId || !event.target.files[0]) return setMsg('toolMsg', '请先保存并选择航次', true);
  const data = new FormData(); data.append('file', event.target.files[0]);
  const res = await fetch(`/api/voyages/${currentVoyageId}/source-image`, {method:'POST', body:data});
  if (!res.ok) return setMsg('toolMsg', await res.text(), true);
  const result = await res.json(); setMsg('toolMsg', `图片已识别并带入，待人工补录：${result.missing_fields.join('、') || '无'}`);
});

$('tonnageForm').addEventListener('submit', async (event) => {
  event.preventDefault(); if (!currentVoyageId) return setMsg('tonnageMsg', '请先保存航次', true);
  const body = formJSON(event.target); body.duration_days = body.duration_days ? Number(body.duration_days) : null; body.purchase_date = nullable(body.purchase_date);
  const res = await fetch(`/api/voyages/${currentVoyageId}/tonnage`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  setMsg('tonnageMsg', res.ok ? '吨税信息已保存' : await res.text(), !res.ok);
});

$('downCrewSelect').addEventListener('change', renderDownPreview);
$('downCrewForm').addEventListener('submit', async (event) => {
  event.preventDefault(); if (!currentVoyageId) return setMsg('downCrewMsg', '请先保存航次', true);
  const selected = selectedCrew(); if (!selected) return setMsg('downCrewMsg', '请先选择下船人员', true);
  const form = formJSON(event.target);
  const body = {direction:'down', name:selected.name, nationality:selected.nationality, gender:selected.gender, birth_date:selected.birth_date, document_no:selected.document_no, rank:selected.rank, reason:form.reason, temporary_entry_permit:form.temporary_entry_permit === 'true', flight_no:nullable(form.flight_no), flight_time:normalizeDateTimeInput(form.flight_time), route:nullable(form.route)};
  if (editingDownChangeId) {
    const res = await fetch(`/api/crew-change/${editingDownChangeId}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    if (!res.ok) return setMsg('downCrewMsg', await res.text(), true);
    resetDownEdit(); await loadCrewOptions(); return setMsg('downCrewMsg', '下船人员记录已修改');
  }
  if (pendingDownCrew.some(person => person.document_no === body.document_no)) return setMsg('downCrewMsg', '该下船人员已在待保存名单中', true);
  pendingDownCrew.push(body); renderCrewChangeLists(); setMsg('downCrewMsg', `已加入下船名单，共${pendingDownCrew.length}人`); event.target.elements.flight_no.value = ''; event.target.elements.flight_time.value = ''; event.target.elements.route.value = '';
});

$('upCrewForm').addEventListener('submit', async (event) => {
  event.preventDefault(); if (!currentVoyageId) return setMsg('upCrewMsg', '请先保存航次', true);
  const form = formJSON(event.target);
  const body = {direction:'up', name:form.name, nationality:form.nationality, gender:form.gender, birth_date:form.birth_date, document_no:form.document_no, rank:nullable(form.rank), temporary_entry_permit:null, flight_no:null, flight_time:null, route:null};
  if (editingUpChangeId) {
    const res = await fetch(`/api/crew-change/${editingUpChangeId}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    if (!res.ok) return setMsg('upCrewMsg', await res.text(), true);
    resetUpEdit(); await loadCrewOptions(); return setMsg('upCrewMsg', '上船人员记录已修改');
  }
  if (pendingUpCrew.some(person => person.document_no === body.document_no)) return setMsg('upCrewMsg', '该上船人员已在待保存名单中', true);
  pendingUpCrew.push(body); renderCrewChangeLists(); setMsg('upCrewMsg', `已加入上船名单，共${pendingUpCrew.length}人`); event.target.reset();
});

async function saveCrewChangeList(direction) {
  if (!currentVoyageId) return setMsg(`${direction}CrewMsg`, '请先保存航次', true);
  const pending = direction === 'down' ? pendingDownCrew : pendingUpCrew;
  if (!pending.length) return setMsg(`${direction}CrewMsg`, `请先加入${direction === 'down' ? '下船' : '上船'}人员`, true);
  const res = await fetch(`/api/voyages/${currentVoyageId}/crew-change`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({people:pending})});
  if (!res.ok) return setMsg(`${direction}CrewMsg`, await res.text(), true);
  if (direction === 'down') pendingDownCrew = []; else pendingUpCrew = [];
  await loadCrewOptions(); setMsg(`${direction}CrewMsg`, `${direction === 'down' ? '下船' : '上船'}人员已保存`);
}

$('saveDownCrewBtn').addEventListener('click', () => saveCrewChangeList('down'));
$('saveUpCrewBtn').addEventListener('click', () => saveCrewChangeList('up'));
$('downCrewList').addEventListener('click', event => {
  const button = event.target.closest('button'); if (!button) return;
  if (button.dataset.removeDirection === 'down') {
    const index = Number(button.dataset.removeIndex); pendingDownCrew.splice(index, 1); renderCrewChangeLists(); return;
  }
  if (button.dataset.editDirection === 'down') {
    const person = savedCrewChanges.find(item => item.id === Number(button.dataset.editId)); if (person) startCrewChangeEdit(person); return;
  }
  if (button.dataset.deleteDirection === 'down') deleteSavedCrewChange(Number(button.dataset.deleteId), 'down');
});
$('upCrewList').addEventListener('click', event => {
  const button = event.target.closest('button'); if (!button) return;
  if (button.dataset.removeDirection === 'up') {
    const index = Number(button.dataset.removeIndex); pendingUpCrew.splice(index, 1); renderCrewChangeLists(); return;
  }
  if (button.dataset.editDirection === 'up') {
    const person = savedCrewChanges.find(item => item.id === Number(button.dataset.editId)); if (person) startCrewChangeEdit(person); return;
  }
  if (button.dataset.deleteDirection === 'up') deleteSavedCrewChange(Number(button.dataset.deleteId), 'up');
});
$('cancelDownEditBtn').addEventListener('click', resetDownEdit);
$('cancelUpEditBtn').addEventListener('click', resetUpEdit);

$('voyageSelect').addEventListener('change', async event => {
  currentVoyageId = Number(event.target.value); pendingDownCrew = []; pendingUpCrew = []; resetDownEdit(); resetUpEdit();
  const voyage = currentVoyages.find(item => item.id === currentVoyageId);
  if (voyage) {
    setVoyageEditMode(voyage);
    await fetch(`/api/voyages/${currentVoyageId}/touch`, {method:'POST'});
  }
  await loadCrewOptions();
});
$('refreshBtn').addEventListener('click', refresh);
$('vesselSearch').addEventListener('input', event => renderVesselOptions(event.target.value));
$('vesselSelect').addEventListener('change', event => {
  syncVesselSearch(event.target.value);
  const vessel = currentVessels.find(item => item.id === Number(event.target.value));
  if (vessel) {
    fillVesselForm(vessel);
    setMsg('vesselMsg', `已同步所选船舶档案：${vessel.chinese_name || vessel.english_name || vessel.imo || ''}`);
  }
});
$('summaryBtn').addEventListener('click', async () => { if (currentVoyageId) $('output').textContent = JSON.stringify(await fetch(`/api/voyages/${currentVoyageId}/summary`).then(r => r.json()), null, 2); });
$('forecastBtn').addEventListener('click', async () => { if (currentVoyageId) { const data = await fetch(`/api/voyages/${currentVoyageId}/forecast`, {method:'POST'}).then(r => r.json()); $('output').textContent = data.content + `\n\n待补字段：${data.missing_fields.join('、') || '无'}`; } });
$('tonnageBtn').addEventListener('click', () => { if (currentVoyageId) window.open(`/api/voyages/${currentVoyageId}/export/tonnage`, '_blank'); });
$('strongGeneralBtn').addEventListener('click', () => { if (currentVoyageId) window.open(`/api/voyages/${currentVoyageId}/export/strong-general`, '_blank'); });
$('customsCargoBtn').addEventListener('click', () => { if (currentVoyageId) window.open(`/api/voyages/${currentVoyageId}/export/customs-cargo`, '_blank'); });
$('crewChangeBtn').addEventListener('click', () => { if (currentVoyageId) window.open(`/api/voyages/${currentVoyageId}/export/crew-change`, '_blank'); });
$('crewChangeCustomsBtn').addEventListener('click', () => { if (currentVoyageId) window.open(`/api/voyages/${currentVoyageId}/export/crew-change-customs`, '_blank'); });
$('healthDeclarationBtn').addEventListener('click', () => { if (currentVoyageId) window.open(`/api/voyages/${currentVoyageId}/export/health-declaration`, '_blank'); });
$('outerFieldReceiptBtn').addEventListener('click', () => { if (currentVoyageId) window.open('/api/voyages/' + currentVoyageId + '/export/outer-field-receipt', '_blank'); });
$('borderInspectionBtn').addEventListener('click', () => { if (currentVoyageId) window.open('/api/voyages/' + currentVoyageId + '/export/border-inspection', '_blank'); });
async function parseTextExtraction(inputId, resultIds, setter, messageId) {
  const text = $(inputId).value.trim();
  if (!text) return setMsg(messageId, '请先粘贴固定格式文本', true);
  const res = await fetch('/api/text-extract/parse', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text})});
  if (!res.ok) {
    setter(null);
    $(resultIds.apply).disabled = true;
    $(resultIds.result).hidden = true;
    return setMsg(messageId, await res.text(), true);
  }
  const data = await res.json();
  setter(data);
  renderTextExtractResult(data, resultIds);
  setMsg(messageId, '识别完成，请检查提取结果');
}

$('parseVesselTextBtn').addEventListener('click', () => parseTextExtraction('vesselTextExtractInput', {type:'vesselTextExtractType', result:'vesselTextExtractResult', apply:'applyVesselTextBtn', applyLabel:'填入船舶档案'}, value => parsedVesselTextExtraction = value, 'vesselTextExtractMsg'));
$('parseVoyageTextBtn').addEventListener('click', () => parseTextExtraction('voyageTextExtractInput', {type:'voyageTextExtractType', result:'voyageTextExtractResult', apply:'applyVoyageTextBtn', applyLabel:'填入航次管理'}, value => parsedVoyageTextExtraction = value, 'voyageTextExtractMsg'));
$('applyVesselTextBtn').addEventListener('click', applyVesselTextExtraction);
$('applyVoyageTextBtn').addEventListener('click', applyVoyageTextExtraction);
function clearTextExtraction(inputId, resultIds, typeId, messageId, setter) {
  $(inputId).value = '';
  $(resultIds.result).hidden = true;
  $(resultIds.apply).disabled = true;
  $(typeId).textContent = '等待识别';
  setter(null);
  setMsg(messageId, '');
}
$('clearVesselTextBtn').addEventListener('click', () => clearTextExtraction('vesselTextExtractInput', {result:'vesselTextExtractResult', apply:'applyVesselTextBtn'}, 'vesselTextExtractType', 'vesselTextExtractMsg', value => parsedVesselTextExtraction = value));
$('clearVoyageTextBtn').addEventListener('click', () => clearTextExtraction('voyageTextExtractInput', {result:'voyageTextExtractResult', apply:'applyVoyageTextBtn'}, 'voyageTextExtractType', 'voyageTextExtractMsg', value => parsedVoyageTextExtraction = value));
refresh();
