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
function normalizeDateInput(value) {
  const raw = String(value ?? '').trim();
  if (!raw) return '';
  let match = raw.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (!match) match = raw.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$/);
  if (!match) return raw;
  const [, year, month, day] = match;
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  if (date.getUTCFullYear() !== Number(year) || date.getUTCMonth() !== Number(month) - 1 || date.getUTCDate() !== Number(day)) return raw;
  return `${year}-${String(Number(month)).padStart(2, '0')}-${String(Number(day)).padStart(2, '0')}`;
}
function bindDateInput(selector) {
  document.querySelectorAll(selector).forEach(input => {
    const normalize = () => {
      const value = normalizeDateInput(input.value);
      if (/^\d{8}$/.test(input.value.trim()) && value !== input.value.trim()) input.value = value;
    };
    input.addEventListener('input', normalize);
    input.addEventListener('blur', normalize);
    input.addEventListener('change', normalize);
  });
}
let currentVoyageId = null;
let voyageDirty = false;
let currentCrew = [];
let crewLoadToken = 0;
let loadedCrewVoyageId = null;
let imoLookupTimer = null;
let savedCrewChanges = [];
let pendingDownCrew = [];
let pendingUpCrew = [];
let temporaryEntryApplicants = [];
let exitStampApplicants = [];
let seafarerVerification = {items: [], job: null};
let seafarerPollTimer = null;
const SEAFARER_AGENT_URL = 'http://127.0.0.1:17321';
let seafarerAgentConnected = false;
let localSeafarerTaskId = null;
let localSeafarerPollTimer = null;
let localSeafarerSyncedIds = new Set();
let editingDownChangeId = null;
let editingUpChangeId = null;
let currentVoyages = [];
let currentVessels = [];
let lockedVesselId = null;
let isNewVesselMode = false;
let editingVoyageId = null;
let parsedVesselTextExtraction = null;
let parsedVoyageTextExtraction = null;
let extractedVesselExtra = {};
let extractedVoyageExtra = {};
let requestedVoyageId = Number(new URLSearchParams(window.location.search).get('voyage')) || null;

function setMsg(id, text, error = false) {
  const node = $(id); node.textContent = text; node.className = `message ${error ? 'error' : 'success'}`;
}

function updateVesselFormMode() {
  const form = $('vesselForm');
  const editable = isNewVesselMode;
  for (const control of form.querySelectorAll('input, button[type="submit"]')) control.disabled = !editable;
  $('newVesselBtn').textContent = editable ? '取消新建船舶' : '新建船舶';
}

function voyageFormDateTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 16) : '';
}

function fillVoyageForm(voyage) {
  const form = $('voyageForm');
  for (const key of ['vessel_id', 'inbound_voyage_no', 'outbound_voyage_no', 'berth', 'previous_port', 'previous_port_country', 'next_port', 'next_port_country', 'route', 'entry_type']) {
    if (form.elements[key]) form.elements[key].value = voyage[key] ?? '';
  }
  for (const key of ['arrival_time', 'departure_time', 'previous_port_departure_time']) {
    if (form.elements[key]) form.elements[key].value = voyageFormDateTime(voyage[key]);
  }
  if (form.elements.customs_inspection) form.elements.customs_inspection.checked = Boolean(voyage.customs_inspection);
  updateCustomsInspectionText();
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

function selectedVessel() {
  const id = Number($('vesselSelect')?.value);
  return currentVessels.find(item => item.id === id) || null;
}

function updateToolAvailability() {
  const enabled = Boolean(currentVoyageId);
  for (const id of ['crewFile', 'forecastBtn', 'summaryBtn', 'strongGeneralBtn', 'customsCargoBtn', 'tonnageBtn', 'crewChangeBtn', 'crewChangeCustomsBtn', 'healthDeclarationBtn', 'outerFieldReceiptBtn', 'borderInspectionBtn', 'maritimePreapprovalBtn']) {
    if ($(id)) $(id).disabled = !enabled;
  }
  if (!enabled && $('seafarerVerifyBtn')) $('seafarerVerifyBtn').disabled = true;
}

function updateVesselLockUI() {
  const vessel = currentVessels.find(item => item.id === Number(lockedVesselId));
  const selected = selectedVessel();
  const hasVessel = Boolean(vessel);
  $('mainWorkspaceGrid').classList.toggle('vessel-locked', hasVessel);
  $('vesselSearch').disabled = false;
  $('vesselSelect').disabled = !currentVessels.length;
  $('newVoyageBtn').disabled = false;
  if (currentVoyageId) {
    // 保留历史航次恢复时的说明，不用船舶选择状态干预用户切换。
  } else {
    $('voyageResumeMsg').textContent = hasVessel
      ? '已选择船舶，请填写新航次或从下方选择该船历史航次。'
      : '请先在左侧船舶档案中选择船舶。';
  }
  const voyageForm = $('voyageForm');
  for (const control of voyageForm.querySelectorAll('input:not([type="hidden"]), select, button')) control.disabled = !hasVessel;
  updateToolAvailability();
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
  else {
    const exactMatch = matches.length === 1 && [matches[0].chinese_name, matches[0].english_name, matches[0].imo]
      .some(value => String(value || '').trim().toLowerCase() === key);
    if (exactMatch) {
      select.value = String(matches[0].id);
      if (String(matches[0].id) !== currentId) select.dispatchEvent(new Event('change', {bubbles: true}));
    } else select.value = '';
  }
}

function setVoyageEditMode(voyage) {
  editingVoyageId = voyage?.id || null;
  voyageDirty = false;
  if (voyage) {
    fillVoyageForm(voyage);
    $('saveVoyageBtn').textContent = '保存当前航次';
    $('voyageResumeMsg').textContent = `当前航次：${voyage.inbound_voyage_no || '未填进港航次'} → ${voyage.outbound_voyage_no || '未填出港航次'}，已自动恢复历史资料。`;
  } else {
    $('voyageForm').reset();
    $('voyageVesselId').value = lockedVesselId || '';
    updateCustomsInspectionText();
    $('saveVoyageBtn').textContent = '保存新航次';
    $('voyageResumeMsg').textContent = lockedVesselId
      ? '正在新建当前选中船舶的航次，保存后会进入该船历史记录。'
      : '请先在左侧船舶档案中选择船舶。';
  }
}

async function refresh(preferredVoyageId = null) {
  currentVessels = await fetch('/api/vessels').then(r => r.json());
  renderVesselOptions();
  const allVoyages = await fetch('/api/voyages').then(r => r.json());
  // 从航次历史“继续操作”进入时，指定航次所属船舶优先于当前页面选择。
  // 这样历史航次恢复后，船舶档案与航次始终来自同一条记录。
  const requested = requestedVoyageId ? allVoyages.find(item => item.id === requestedVoyageId) : null;
  if (requested) lockedVesselId = requested.vessel_id;
  if (!currentVessels.some(item => item.id === Number(lockedVesselId))) lockedVesselId = null;
  if (lockedVesselId) {
    $('vesselSelect').value = String(lockedVesselId);
    syncVesselSearch(lockedVesselId);
  } else {
    const selectedId = Number($('vesselSelect').value);
    if (currentVessels.some(item => item.id === selectedId)) syncVesselSearch(selectedId);
    else {
      $('vesselSelect').value = '';
      $('vesselSearch').value = '';
    }
  }
  currentVoyages = lockedVesselId ? allVoyages.filter(item => Number(item.vessel_id) === Number(lockedVesselId)) : [];
  $('voyageSelect').innerHTML = currentVoyages.length
    ? `<option value="">请选择该船历史航次</option>${currentVoyages.map(v => `<option value="${v.id}">${v.id}｜${v.inbound_voyage_no || ''} → ${v.outbound_voyage_no || ''}</option>`).join('')}`
    : '<option value="">暂无该船历史航次</option>';
  const preferred = preferredVoyageId || currentVoyageId || (requestedVoyageId && currentVoyages.some(item => item.id === requestedVoyageId) ? requestedVoyageId : null);
  const voyage = currentVoyages.find(item => item.id === Number(preferred)) || null;
  currentVoyageId = voyage?.id || null;
  if (voyage) {
    $('voyageSelect').value = String(voyage.id);
    setVoyageEditMode(voyage);
  } else setVoyageEditMode(null);
  updateVesselLockUI();
  updateVesselFormMode();
  await loadCrewOptions();
}

function verificationStatusClass(status) {
  if (status === '有效') return 'verification-status-valid';
  if (status === '无效' || status === '失败') return 'verification-status-invalid';
  if (status === '重试中' || status === '查询中') return 'verification-status-retry';
  if (status === '不适用') return 'verification-status-na';
  return 'verification-status-pending';
}

function renderSeafarerVerification(data = {items: [], job: null, eligible_count: 0, completed_count: 0}) {
  seafarerVerification = data;
  const rows = (data.items || []).filter(item => item.eligible);
  const job = data.job;
  const running = Boolean(job && ['排队中', '查询中'].includes(job.status));
  const statusNode = $('seafarerVerificationStatus');
  if (statusNode) {
    statusNode.textContent = running
      ? `核验中 ${job.processed}/${job.total}`
      : rows.length ? `中国籍海员证 ${data.completed_count || 0}/${rows.length}` : '无可核验人员';
  }
  const body = $('seafarerVerificationBody');
  if (body) {
    body.innerHTML = rows.length ? rows.map(item => {
      const status = item.status || '待查询';
      const title = item.error_info ? ` title="${escapeHtml(item.error_info)}"` : '';
      return `<tr><td>${escapeHtml(item.name || '')}</td><td>${escapeHtml(item.document_no || '')}</td><td class="${verificationStatusClass(status)}"${title}>${escapeHtml(status)}</td><td>${escapeHtml(item.certificate_status || '')}</td><td>${escapeHtml(item.valid_date || '')}</td></tr>`;
    }).join('') : '<tr><td colspan="5" class="muted">导入名单后自动识别中国籍海员证人员</td></tr>';
  }
  const button = $('seafarerVerifyBtn');
  const stopping = Boolean(job && job.status === '停止中');
  if (button) {
    button.disabled = !currentVoyageId || !rows.length || stopping || !seafarerAgentConnected;
    button.textContent = running || stopping ? (stopping ? '正在停止…' : '停止核验') : '核验中国籍海员证';
    button.classList.toggle('danger', running || stopping);
  }
  if (job && job.status === '失败') setMsg('seafarerVerificationMsg', `海员证核验任务失败：${job.error || '请检查网络和浏览器环境'}`, true);
  else if (!running && job && job.status === '已完成') setMsg('seafarerVerificationMsg', `核验完成：${data.completed_count || 0}/${rows.length} 人已返回结果`);
  else if (!running && job && job.status === '已停止') setMsg('seafarerVerificationMsg', '核验已停止，可再次点击按钮重新核验');
  else if (!rows.length && currentVoyageId) setMsg('seafarerVerificationMsg', '当前名单中没有中国籍海员证人员');
  else if (stopping) setMsg('seafarerVerificationMsg', '正在停止当前查询，请稍候');
  else if (running) setMsg('seafarerVerificationMsg', '正在逐人查询，完成一人后会立即更新状态');
  else if ($('seafarerVerificationMsg')) $('seafarerVerificationMsg').textContent = '';
}

async function requestSeafarerAgent(path, options = {}) {
  const request = {...options, headers: {'Content-Type': 'application/json', ...(options.headers || {})}};
  const res = await fetch(`${SEAFARER_AGENT_URL}${path}`, request);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `本地核验工具请求失败（${res.status}）`);
  return data;
}

function updateSeafarerAgentStatus(connected, message = '') {
  seafarerAgentConnected = connected;
  const node = $('seafarerAgentStatus');
  if (node) {
    node.textContent = message || (connected ? '本地工具已连接' : '本地工具未连接');
    node.className = `status-chip ${connected ? 'verification-agent-status-ok' : 'verification-agent-status-offline'}`;
  }
  const button = $('seafarerAgentCheckBtn');
  if (button) button.textContent = connected ? '重新检测' : '检测本地工具';
  if ($('seafarerVerifyBtn') && !localSeafarerTaskId) {
    const rows = (seafarerVerification.items || []).filter(item => item.eligible);
    $('seafarerVerifyBtn').disabled = !currentVoyageId || !rows.length || !connected;
  }
}

async function checkSeafarerAgent(silent = false) {
  try {
    const data = await requestSeafarerAgent('/health');
    updateSeafarerAgentStatus(true, `本地工具已连接 v${data.version || '1.0'}`);
    if (!silent) setMsg('seafarerVerificationMsg', '本地核验工具连接正常');
    return true;
  } catch (error) {
    updateSeafarerAgentStatus(false);
    if (!silent) setMsg('seafarerVerificationMsg', '未检测到本地核验工具，请先下载安装后再核验', true);
    return false;
  }
}

function stopLocalSeafarerPolling() {
  if (localSeafarerPollTimer) {
    clearTimeout(localSeafarerPollTimer);
    localSeafarerPollTimer = null;
  }
}

async function stopLocalSeafarerTask() {
  if (!localSeafarerTaskId) return;
  try { await requestSeafarerAgent(`/jobs/${encodeURIComponent(localSeafarerTaskId)}/stop`, {method: 'POST', body: '{}'}); }
  catch (_) { /* 工具关闭时，云端页面仍可继续使用 */ }
}

function clearLocalSeafarerTask() {
  stopLocalSeafarerPolling();
  if (localSeafarerTaskId) stopLocalSeafarerTask();
  localSeafarerTaskId = null;
  localSeafarerSyncedIds = new Set();
}

async function syncLocalSeafarerResult(item) {
  const memberId = Number(item.crew_member_id);
  if (!memberId || localSeafarerSyncedIds.has(memberId)) return;
  const res = await fetch(`/api/voyages/${currentVoyageId}/seafarer-verification/local-result`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(item),
  });
  if (!res.ok) throw new Error(await res.text());
  localSeafarerSyncedIds.add(memberId);
}

async function pollLocalSeafarerTask() {
  if (!localSeafarerTaskId) return;
  try {
    const job = await requestSeafarerAgent(`/jobs/${encodeURIComponent(localSeafarerTaskId)}`);
    const finishedItems = (job.items || []).filter(item => item.status && item.status !== '待查询');
    for (const item of finishedItems) await syncLocalSeafarerResult(item);
    const existing = new Map((seafarerVerification.items || []).map(item => [Number(item.crew_member_id), item]));
    for (const item of job.items || []) {
      const current = existing.get(Number(item.crew_member_id));
      if (current) Object.assign(current, item, {eligible: true});
    }
    const running = ['排队中', '查询中', '停止中'].includes(job.status);
    renderSeafarerVerification({...seafarerVerification, job});
    if (running) {
      localSeafarerPollTimer = setTimeout(pollLocalSeafarerTask, 1000);
    } else {
      const finished = job.status === '已完成' || job.status === '已停止';
      localSeafarerTaskId = null;
      stopLocalSeafarerPolling();
      await loadSeafarerVerification();
      setMsg('seafarerVerificationMsg', finished ? '本机核验完成，结果已同步到云端' : `本机核验失败：${job.error || '请检查本地工具日志'}`, !finished);
    }
  } catch (error) {
    localSeafarerTaskId = null;
    stopLocalSeafarerPolling();
    updateSeafarerAgentStatus(false);
    setMsg('seafarerVerificationMsg', `本地核验同步失败：${error.message}`, true);
  }
}

async function startLocalSeafarerTask() {
  if (!currentVoyageId) return setMsg('seafarerVerificationMsg', '请先保存并选择航次', true);
  if (!seafarerAgentConnected && !(await checkSeafarerAgent())) return;
  const rows = (seafarerVerification.items || []).filter(item => item.eligible).map(item => ({
    crew_member_id: item.crew_member_id, name: item.name, nationality: item.nationality,
    rank: item.rank, document_no: item.document_no,
  }));
  if (!rows.length) return setMsg('seafarerVerificationMsg', '当前名单中没有可核验的中国籍海员证人员', true);
  localSeafarerSyncedIds = new Set();
  const task = await requestSeafarerAgent('/verify', {
    method: 'POST', body: JSON.stringify({task_id: `voyage-${currentVoyageId}-${Date.now()}`, rows}),
  });
  localSeafarerTaskId = task.task_id;
  setMsg('seafarerVerificationMsg', '已调用本机浏览器核验，请保持浏览器窗口打开');
  await pollLocalSeafarerTask();
}

async function loadSeafarerVerification() {
  const voyageId = Number(currentVoyageId) || null;
  if (seafarerPollTimer) { clearTimeout(seafarerPollTimer); seafarerPollTimer = null; }
  if (!voyageId) {
    renderSeafarerVerification();
    return;
  }
  const res = await fetch(`/api/voyages/${voyageId}/seafarer-verification`);
  if (Number(currentVoyageId) !== voyageId) return;
  if (!res.ok) {
    renderSeafarerVerification();
    return;
  }
  const data = await res.json();
  renderSeafarerVerification(data);
  if (data.job && ['排队中', '查询中', '停止中'].includes(data.job.status)) {
    const voyageId = currentVoyageId;
    seafarerPollTimer = setTimeout(async () => {
      seafarerPollTimer = null;
      if (Number(currentVoyageId) === Number(voyageId)) await loadSeafarerVerification();
    }, 1000);
  }
}

function clearCrewViewForVoyageSwitch() {
  clearLocalSeafarerTask();
  currentCrew = [];
  savedCrewChanges = [];
  temporaryEntryApplicants = [];
  exitStampApplicants = [];
  loadedCrewVoyageId = null;
  const select = $('downCrewSelect');
  if (select) {
    select.innerHTML = '<option value="">请先读取当前航次船员名单</option>';
    select.disabled = true;
  }
  if ($('crewRosterMsg')) $('crewRosterMsg').textContent = '';
  renderDownPreview();
  renderCrewChangeLists();
  renderTemporaryEntryOptions();
  renderTemporaryEntryList();
  renderExitStampOptions();
  renderExitStampList();
  renderSeafarerVerification();
}

function clearTonnageForm() {
  const form = $('tonnageForm');
  if (form) form.reset();
  if ($('purchaseDateManual')) $('purchaseDateManual').value = '';
  if ($('tonnageAutoInfo')) $('tonnageAutoInfo').textContent = '选择航次后自动带入船舶、航次和净吨位信息。';
  if ($('tonnageTextPreview')) $('tonnageTextPreview').textContent = '选择起购日期和购买时长后自动生成。';
  if ($('tonnageQuoteDetails')) $('tonnageQuoteDetails').innerHTML = '';
}

function renderTonnageQuoteDetails(data = null) {
  const target = $('tonnageQuoteDetails');
  if (!target) return;
  if (!data) {
    target.innerHTML = '';
    return;
  }
  const details = [
    ['船舶国籍', data.vessel_nationality || '待填写'],
    ['是否优惠国家', data.preferential ? '是' : '否'],
    ['净吨位', data.net_tonnage ?? '待填写'],
    ['购买天数', data.duration_text || `${data.duration_days || ''}天`],
    ['吨税单价', `${data.unit_price || ''} 元/净吨`],
  ];
  target.innerHTML = details.map(([label, value]) => `<div class="tonnage-quote-detail"><span class="tonnage-quote-detail-label">${label}</span><span class="tonnage-quote-detail-value">${escapeHtml(value)}</span></div>`).join('');
}

function renderTonnageRateTable(data = null) {
  const box = $('tonnageRateTableBox');
  const mark = $('tonnageRateTableMark');
  if (!box || !mark) return;
  box.querySelectorAll('.rate-selected').forEach(cell => cell.classList.remove('rate-selected'));
  box.querySelectorAll('.tier-selected').forEach(row => row.classList.remove('tier-selected'));
  if (!data) {
    mark.textContent = '待计算';
    return;
  }
  const tierIndex = Number(data.tier_index);
  const duration = Number(data.duration_days);
  const rateType = data.preferential ? 'preferential' : 'ordinary';
  const row = box.querySelector(`tbody tr[data-tier-index="${tierIndex}"]`);
  const cell = row?.querySelector(`td[data-rate-type="${rateType}"][data-duration="${duration}"]`);
  row?.classList.add('tier-selected');
  cell?.classList.add('rate-selected');
  mark.textContent = `本次适用：${data.preferential ? '优惠税率' : '原价税率'}｜${data.duration_text}｜${data.unit_price} 元/净吨`;
}

let preferentialCountryItems = [];

function renderPreferentialCountryList() {
  const target = $('preferentialCountryList');
  if (!target) return;
  target.innerHTML = preferentialCountryItems.length
    ? preferentialCountryItems.map(item => `<div class="preferential-country-item"><span>${escapeHtml(item.name)}</span><button class="danger small-button" type="button" data-delete-preferential-country="${item.id}">删除</button></div>`).join('')
    : '<p class="muted">当前没有优惠国家，所有船籍将按原价税率计算。</p>';
}

async function loadPreferentialCountries() {
  const target = $('preferentialCountryList');
  if (target) target.innerHTML = '<span class="muted">正在读取优惠国家名单…</span>';
  const res = await fetch('/api/settings/preferential-countries');
  if (!res.ok) {
    if (target) target.innerHTML = '<span class="message error">优惠国家名单读取失败</span>';
    return;
  }
  preferentialCountryItems = await res.json();
  renderPreferentialCountryList();
}

function setupPreferentialCountrySettings() {
  const card = document.querySelector('.tonnage-card');
  const title = card?.querySelector('h2');
  if (!card || !title || $('preferentialCountriesBtn')) return;
  const header = document.createElement('div');
  header.className = 'tonnage-header';
  header.append(title);
  const openButton = document.createElement('button');
  openButton.id = 'preferentialCountriesBtn';
  openButton.className = 'secondary';
  openButton.type = 'button';
  openButton.textContent = '优惠国家设置';
  header.append(openButton);
  card.prepend(header);
  const panel = document.createElement('div');
  panel.id = 'preferentialCountryPanel';
  panel.className = 'settings-overlay';
  panel.hidden = true;
  panel.innerHTML = '<section class="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="preferentialCountryTitle"><div class="settings-dialog-header"><h2 id="preferentialCountryTitle">优惠国家设置</h2><button id="closePreferentialCountryBtn" class="secondary" type="button">关闭</button></div><p class="helper">吨税计算会实时读取以下名单。国家名可直接输入中文，也会自动去除代码括号。</p><form id="preferentialCountryForm" class="settings-add-form"><input name="name" placeholder="例如：新加坡" maxlength="128" required /><button class="primary" type="submit">新增国家</button></form><p id="preferentialCountryMsg" class="message"></p><div id="preferentialCountryList" class="preferential-country-list"></div></section>';
  document.body.append(panel);
  openButton.addEventListener('click', async () => { panel.hidden = false; await loadPreferentialCountries(); });
  $('closePreferentialCountryBtn').addEventListener('click', () => { panel.hidden = true; });
  $('preferentialCountryForm').addEventListener('submit', async event => {
    event.preventDefault();
    const input = event.target.elements.name;
    const res = await fetch('/api/settings/preferential-countries', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name: input.value})});
    if (!res.ok) return setMsg('preferentialCountryMsg', await res.text(), true);
    input.value = '';
    await loadPreferentialCountries();
    await updateTonnageQuote();
    setMsg('preferentialCountryMsg', '优惠国家已新增');
  });
  panel.addEventListener('click', async event => {
    const button = event.target.closest('[data-delete-preferential-country]');
    if (!button) return;
    const res = await fetch(`/api/settings/preferential-countries/${button.dataset.deletePreferentialCountry}`, {method:'DELETE'});
    if (!res.ok) return setMsg('preferentialCountryMsg', await res.text(), true);
    await loadPreferentialCountries();
    await updateTonnageQuote();
    setMsg('preferentialCountryMsg', '优惠国家已删除');
  });
}

function renderTonnageBaseInfo(voyage = null) {
  const vessel = currentVessels.find(item => item.id === Number(voyage?.vessel_id || selectedVessel()?.id));
  const info = $('tonnageAutoInfo');
  if (!info) return;
  if (!vessel || !voyage) {
    info.textContent = currentVoyageId ? '正在读取当前航次吨税信息。' : '选择航次后自动带入船舶、航次和净吨位信息。';
    return;
  }
  info.textContent = `船舶：${vessel.chinese_name || ''} / ${vessel.english_name || ''}｜船籍：${vessel.nationality || '待填写'}｜进港航次号：${voyage.inbound_voyage_no || '待填写'}｜净吨位：${vessel.net_tonnage ?? '待填写'}`;
}

async function updateTonnageQuote() {
  const voyageId = Number(currentVoyageId) || null;
  if (!voyageId) return clearTonnageForm();
  const form = $('tonnageForm');
  const duration = Number(form.elements.duration_days.value || 0);
  const purchaseDate = normalizeDateInput(form.elements.purchase_date.value || $('purchaseDateManual').value);
  const voyage = currentVoyages.find(item => Number(item.id) === Number(currentVoyageId));
  renderTonnageBaseInfo(voyage);
  if (!duration || !purchaseDate || !/^\d{4}-\d{2}-\d{2}$/.test(purchaseDate)) {
    form.elements.amount.value = '';
    if ($('tonnageTextPreview')) $('tonnageTextPreview').textContent = '选择起购日期和购买时长后自动生成。';
    renderTonnageQuoteDetails();
    renderTonnageRateTable();
    return;
  }
  const res = await fetch(`/api/voyages/${voyageId}/tonnage-quote?duration_days=${duration}&purchase_date=${encodeURIComponent(purchaseDate)}`);
  if (Number(currentVoyageId) !== voyageId) return;
  if (!res.ok) {
    form.elements.amount.value = '';
    if ($('tonnageTextPreview')) $('tonnageTextPreview').textContent = '当前船舶资料不足，暂时无法计算吨税。';
    renderTonnageQuoteDetails();
    renderTonnageRateTable();
    return;
  }
  const data = await res.json();
  form.elements.amount.value = data.total_amount || '';
  if ($('tonnageAutoInfo')) $('tonnageAutoInfo').textContent = `船舶：${data.vessel_chinese_name || ''} / ${data.vessel_english_name || ''}｜船籍：${data.vessel_nationality || '待填写'}｜进港航次号：${data.inbound_voyage_no || '待填写'}｜净吨位：${data.net_tonnage ?? ''}｜${data.tax_type}｜${data.tonnage_tier}｜单价：${data.unit_price} 元/净吨`;
  if ($('tonnageTextPreview')) $('tonnageTextPreview').textContent = data.generated_text || '吨税说明文字待生成。';
  renderTonnageQuoteDetails(data);
  renderTonnageRateTable(data);
}

async function loadTonnageApplication(expectedVoyageId = currentVoyageId) {
  const voyageId = Number(expectedVoyageId) || null;
  if (!voyageId) return clearTonnageForm();
  const res = await fetch(`/api/voyages/${voyageId}/tonnage`);
  if (Number(currentVoyageId) !== voyageId) return;
  if (!res.ok) return clearTonnageForm();
  const data = await res.json();
  const form = $('tonnageForm');
  if (data.exists) {
    form.elements.amount.value = data.amount || '';
    form.elements.pre_entry_no.value = data.pre_entry_no || '';
    form.elements.duration_days.value = data.duration_days || '';
    form.elements.purchase_date.value = data.purchase_date || '';
    $('purchaseDateManual').value = data.purchase_date || '';
    form.elements.charter_relation.value = data.charter_relation || '其他';
    if ($('tonnageTextPreview')) $('tonnageTextPreview').textContent = data.generated_text || '选择起购日期和购买时长后自动生成。';
    renderTonnageRateTable();
  } else {
    form.elements.amount.value = '';
    form.elements.pre_entry_no.value = '';
    form.elements.duration_days.value = '';
    form.elements.purchase_date.value = '';
    $('purchaseDateManual').value = '';
    form.elements.charter_relation.value = '其他';
    if ($('tonnageTextPreview')) $('tonnageTextPreview').textContent = '选择起购日期和购买时长后自动生成。';
    renderTonnageQuoteDetails();
    renderTonnageRateTable();
  }
  await updateTonnageQuote();
}

async function loadCrewOptions() {
  const voyageId = Number(currentVoyageId) || null;
  const requestToken = ++crewLoadToken;
  if (!voyageId) {
    clearCrewViewForVoyageSwitch();
    if ($('downCrewSelect')) $('downCrewSelect').innerHTML = '<option value="">请先选择航次</option>';
    await loadTonnageApplication();
    await loadSeafarerVerification();
    updateToolAvailability();
    return;
  }
  if (loadedCrewVoyageId !== voyageId) clearCrewViewForVoyageSwitch();
  const res = await fetch(`/api/voyages/${voyageId}/summary`);
  if (requestToken !== crewLoadToken || Number(currentVoyageId) !== voyageId) return;
  if (!res.ok) {
    $('downCrewSelect').innerHTML = '<option value="">无法读取船员名单</option>';
    $('downCrewSelect').disabled = true;
    return;
  }
  const data = await res.json();
  if (requestToken !== crewLoadToken || Number(currentVoyageId) !== voyageId) return;
  currentCrew = data.crew || []; savedCrewChanges = data.crew_change || [];
  temporaryEntryApplicants = data.temporary_entry || [];
  exitStampApplicants = data.exit_stamp || [];
  $('downCrewSelect').innerHTML = currentCrew.length
    ? currentCrew.map(c => `<option value="${c.id}">${c.name}｜${c.nationality || ''}｜${c.rank || ''}</option>`).join('')
    : '<option value="">请先导入船员名单</option>';
  $('downCrewSelect').disabled = currentCrew.length === 0;
  $('crewRosterMsg').textContent = currentCrew.length ? `当前名单 ${currentCrew.length} 人` : '尚未导入名单';
  renderDownPreview();
  renderCrewChangeLists();
  renderTemporaryEntryOptions();
  renderTemporaryEntryList();
  renderExitStampOptions();
  renderExitStampList();
  loadedCrewVoyageId = voyageId;
  await loadTonnageApplication(voyageId);
  if (requestToken !== crewLoadToken || Number(currentVoyageId) !== voyageId) return;
  await loadSeafarerVerification();
  if (requestToken !== crewLoadToken || Number(currentVoyageId) !== voyageId) return;
  updateToolAvailability();
}

function renderTemporaryEntryOptions() {
  const select = $('temporaryEntryCrewSelect');
  if (!select) return;
  const selectedIds = new Set(temporaryEntryApplicants.map(item => Number(item.crew_member_id)));
  const available = currentCrew.filter(person => !selectedIds.has(Number(person.id)));
  select.innerHTML = available.length
    ? available.map(person => `<option value="${person.id}">${escapeHtml(person.name)}｜${escapeHtml(person.nationality || '')}｜${escapeHtml(person.rank || '')}</option>`).join('')
    : '<option value="">没有可添加的船员</option>';
  select.disabled = available.length === 0;
  $('temporaryEntryAddBtn').disabled = available.length === 0;
}

function renderTemporaryEntryList() {
  const target = $('temporaryEntryList');
  if (!target) return;
  target.innerHTML = temporaryEntryApplicants.length
    ? temporaryEntryApplicants.map(item => `<div class="change-item"><span>${escapeHtml(item.name)}｜${escapeHtml(item.nationality || '')}｜${escapeHtml(item.rank || '')}｜出生：${escapeHtml(humanDate(item.birth_date))}｜证件号：${escapeHtml(item.document_no || '')}</span><button class="danger small-button" type="button" data-delete-temporary-entry="${item.id}">移除</button></div>`).join('')
    : '<p class="muted">暂未添加申请临入人员</p>';
  $('temporaryEntryCount').textContent = temporaryEntryApplicants.length ? `已选择 ${temporaryEntryApplicants.length} 人` : '';
  $('temporaryEntryBtn').disabled = !currentVoyageId || temporaryEntryApplicants.length === 0;
}

function renderExitStampOptions() {
  const select = $('exitStampCrewSelect');
  if (!select) return;
  const selectedIds = new Set(exitStampApplicants.map(item => Number(item.crew_member_id)));
  const available = currentCrew.filter(person => !selectedIds.has(Number(person.id)));
  select.innerHTML = available.length
    ? available.map(person => `<option value="${person.id}">${escapeHtml(person.name)}｜${escapeHtml(person.nationality || '')}｜${escapeHtml(person.rank || '')}</option>`).join('')
    : '<option value="">没有可添加的船员</option>';
  select.disabled = available.length === 0;
  $('exitStampAddBtn').disabled = available.length === 0;
}

function renderExitStampList() {
  const target = $('exitStampList');
  if (!target) return;
  target.innerHTML = exitStampApplicants.length
    ? exitStampApplicants.map(item => `<div class="change-item"><span>${escapeHtml(item.name)}｜${escapeHtml(item.nationality || '')}｜出生：${escapeHtml(humanDate(item.birth_date))}｜证件号：${escapeHtml(item.document_no || '')}</span><button class="danger small-button" type="button" data-delete-exit-stamp="${item.id}">移除</button></div>`).join('')
    : '<p class="muted">暂未添加申请出境章人员</p>';
  $('exitStampCount').textContent = exitStampApplicants.length ? `已选择 ${exitStampApplicants.length} 人` : '';
  $('exitStampBtn').disabled = !currentVoyageId || exitStampApplicants.length === 0;
}

function selectedCrew() {
  return currentCrew.find(c => String(c.id) === $('downCrewSelect').value);
}

function renderDownPreview() {
  const selected = selectedCrew();
  $('downCrewPreview').textContent = selected
    ? `姓名：${selected.name || ''}　国籍：${selected.nationality || ''}　性别：${selected.gender || ''}　出生日期：${humanDate(selected.birth_date)}　证件号：${selected.document_no || ''}　职务：${selected.rank || ''}`
    : '请先导入船员名单。';
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
}

function humanDate(value) {
  const raw = String(value ?? '').trim();
  if (!raw) return '';
  const normalized = normalizeDateInput(raw);
  return /^\d{4}-\d{2}-\d{2}$/.test(normalized) ? normalized : raw;
}

function humanDateTime(value) {
  const raw = String(value ?? '').trim();
  if (!raw) return '';
  const normalized = normalizeDateTimeInput(raw);
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(normalized)
    ? normalized.replace('T', ' ').slice(0, 16)
    : raw.replace('T', ' ').slice(0, 16);
}

function changeLabel(person) {
  const extras = person.direction === 'down'
    ? `事由：${person.reason || '未填写'}｜临入：${person.temporary_entry_permit ? '是' : '否'}${person.flight_no ? `｜航班：${person.flight_no}` : ''}${person.flight_time ? `｜时间：${humanDateTime(person.flight_time)}` : ''}${person.route ? `｜航线：${person.route}` : ''}`
    : `职务：${person.rank || '未填写'}`;
  return `${person.name || ''}｜${person.nationality || ''}｜${person.gender || ''}｜出生：${humanDate(person.birth_date) || '未填写'}｜证件号：${person.document_no || ''}｜${extras}`;
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
    if (form.elements[key] && voyage[key] !== undefined && voyage[key] !== null && voyage[key] !== '') {
      if (key !== 'vessel_id' || !lockedVesselId || Number(voyage[key]) === Number(lockedVesselId)) form.elements[key].value = voyage[key];
    }
  }
  for (const key of ['arrival_time', 'departure_time', 'previous_port_departure_time']) {
    if (form.elements[key] && voyage[key]) form.elements[key].value = voyageFormDateTime(voyage[key]);
  }
  if (voyage.vessel_id && (!lockedVesselId || Number(voyage.vessel_id) === Number(lockedVesselId))) syncVesselSearch(voyage.vessel_id);
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
  if (!isNewVesselMode) return setMsg('vesselTextExtractMsg', '首页已有船舶档案只允许查看；如需录入识别结果，请先点击“新建船舶”。', true);
  const vessel = parsedVesselTextExtraction.vessel || {};
  fillVesselFormPartial(vessel);
  extractedVesselExtra = vessel.extra || {};
  setMsg('vesselTextExtractMsg', '船舶文字已填入船舶档案表单，请检查后保存。');
}

function applyVoyageTextExtraction() {
  if (!parsedVoyageTextExtraction) return;
  const vessel = parsedVoyageTextExtraction.vessel || {};
  const voyage = parsedVoyageTextExtraction.voyage || {};
  if (vesselHasData(vessel) && isNewVesselMode) fillVesselFormPartial(vessel);
  if (vesselHasData(vessel) && isNewVesselMode) extractedVesselExtra = {...extractedVesselExtra, ...(vessel.extra || {})};
  extractedVoyageExtra = voyage.extra || {};
  const matchedVessel = extractedVesselMatch(vessel);
  if (matchedVessel) {
    lockedVesselId = matchedVessel.id;
    voyage.vessel_id = matchedVessel.id;
    $('vesselSelect').value = String(matchedVessel.id);
    syncVesselSearch(matchedVessel.id);
    fillVesselForm(matchedVessel);
  } else if (Object.keys(vessel).some(key => key !== 'extra' && vessel[key])) {
    lockedVesselId = null;
    $('vesselSelect').value = '';
    $('vesselSearch').value = '';
  }
  fillVoyageFormPartial(voyage);
  const message = matchedVessel
    ? '航次文字已填入航次管理表单，请检查后保存。'
    : '航次文字已填入表单；未匹配到历史船舶，请先保存船舶档案，再选择船舶保存航次。';
  voyageDirty = true;
  updateVesselLockUI();
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
  if (!isNewVesselMode) return setMsg('vesselMsg', '首页已有船舶档案不允许直接修改，请进入船舶档案管理页面编辑', true);
  const body = formJSON(event.target);
  body.extra = {...extractedVesselExtra};
  for (const key of ['imo','chinese_name','english_name','nationality','call_sign','shipping_company','mmsi']) body[key] = nullable(body[key]);
  for (const key of ['net_tonnage','gross_tonnage']) body[key] = body[key] ? Number(body[key]) : null;
  const res = await fetch('/api/vessels', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (!res.ok) return setMsg('vesselMsg', await res.text(), true);
  const savedVessel = await res.json();
  extractedVesselExtra = {};
  isNewVesselMode = false;
  await refresh();
  $('vesselSelect').value = String(savedVessel.id);
  syncVesselSearch(savedVessel.id);
  fillVesselForm(savedVessel);
  updateVesselLockUI();
  setMsg('vesselMsg', `船舶档案已新增：${savedVessel.chinese_name || savedVessel.english_name || savedVessel.imo || ''}；现在可以选择该船并新建航次`);
});

$('voyageForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!lockedVesselId) return setMsg('voyageMsg', '请先在船舶档案中选择船舶档案', true);
  const body = formJSON(event.target);
  body.extra = {...extractedVoyageExtra};
  body.vessel_id = Number(lockedVesselId); body.crew_change = false; body.customs_inspection = event.target.elements.customs_inspection.checked;
  for (const key of ['arrival_time','departure_time','previous_port_departure_time']) body[key] = normalizeDateTimeInput(body[key]);
  body.entry_type = nullable(body.entry_type);
  const url = editingVoyageId ? `/api/voyages/${editingVoyageId}` : '/api/voyages';
  const method = editingVoyageId ? 'PUT' : 'POST';
  const res = await fetch(url, {method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (res.ok) { const item = await res.json(); currentVoyageId = item.id; voyageDirty = false; await refresh(item.id); $('voyageSelect').value = String(item.id); }
  setMsg('voyageMsg', res.ok ? '航次已保存' : await res.text(), !res.ok); if (res.ok) extractedVoyageExtra = {};
});

$('newVoyageBtn').addEventListener('click', async () => {
  if (voyageDirty) return setMsg('voyageMsg', '当前航次有未保存修改，请先保存后再新建航次', true);
  requestedVoyageId = null;
  lockedVesselId = null; currentVoyageId = null; editingVoyageId = null; voyageDirty = false; isNewVesselMode = false;
  $('vesselForm').reset(); $('vesselSelect').value = ''; $('vesselSearch').value = '';
  pendingDownCrew = []; pendingUpCrew = []; resetDownEdit(); resetUpEdit();
  await refresh();
  setMsg('voyageMsg', '已清空船舶和航次信息，请重新选择船舶后填写新航次');
});

$('newVesselBtn').addEventListener('click', async () => {
  if (isNewVesselMode) {
    isNewVesselMode = false;
    $('vesselForm').reset();
    await refresh();
    setMsg('vesselMsg', '已取消新建船舶');
    return;
  }
  if (lockedVesselId && !window.confirm('当前已选择船舶，切换到新建船舶会清空当前航次操作状态，是否继续？')) return;
  requestedVoyageId = null;
  lockedVesselId = null; currentVoyageId = null; editingVoyageId = null; voyageDirty = false; isNewVesselMode = true;
  $('vesselForm').reset();
  extractedVesselExtra = {};
  pendingDownCrew = []; pendingUpCrew = []; resetDownEdit(); resetUpEdit();
  $('vesselSelect').value = '';
  $('vesselSearch').value = '';
  await refresh();
  updateVesselFormMode();
  setMsg('vesselMsg', '已切换到新建船舶模式，请填写后保存');
  $('vesselForm').elements.imo.focus();
});

function updateCustomsInspectionText() {
  const input = $('customsInspection');
  const text = $('customsInspectionText');
  if (!input || !text) return;
  text.textContent = input.checked ? '查船：系统中控' : '不查船：系统允许放行';
}

$('customsInspection').addEventListener('change', updateCustomsInspectionText);

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

$('seafarerVerifyBtn').addEventListener('click', async () => {
  if (!currentVoyageId) return setMsg('seafarerVerificationMsg', '请先保存并选择航次', true);
  const button = $('seafarerVerifyBtn');
  button.disabled = true;
  try {
    if (localSeafarerTaskId) {
      setMsg('seafarerVerificationMsg', '正在停止本机海员证核验，请稍候');
      await stopLocalSeafarerTask();
      await pollLocalSeafarerTask();
    } else {
      await startLocalSeafarerTask();
    }
  } catch (error) {
    button.disabled = false;
    setMsg('seafarerVerificationMsg', `无法启动本地核验工具：${error.message}`, true);
  }
});

$('seafarerAgentCheckBtn').addEventListener('click', () => checkSeafarerAgent());

$('tonnageForm').addEventListener('submit', async (event) => {
  event.preventDefault(); if (!currentVoyageId) return setMsg('tonnageMsg', '请先保存航次', true);
  const body = formJSON(event.target); body.duration_days = body.duration_days ? Number(body.duration_days) : null;
  body.purchase_date = nullable(normalizeDateInput(body.purchase_date || $('purchaseDateManual').value));
  const res = await fetch(`/api/voyages/${currentVoyageId}/tonnage`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (!res.ok) return setMsg('tonnageMsg', await res.text(), true);
  const result = await res.json();
  event.target.elements.amount.value = result.amount || '';
  $('tonnageTextPreview').textContent = result.generated_text || '';
  setMsg('tonnageMsg', `吨税信息已保存，${result.tax_type} ${result.unit_price}元/净吨，总金额${result.amount}`);
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
  const body = {direction:'up', name:form.name, nationality:form.nationality, gender:form.gender, birth_date:normalizeDateInput(form.birth_date), document_no:form.document_no, rank:nullable(form.rank), temporary_entry_permit:null, flight_no:null, flight_time:null, route:null};
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
  const nextVoyageId = Number(event.target.value);
  if (voyageDirty && nextVoyageId && nextVoyageId !== Number(currentVoyageId)) {
    event.target.value = currentVoyageId ? String(currentVoyageId) : '';
    return setMsg('voyageMsg', '当前航次有未保存修改，请先保存后再切换历史航次', true);
  }
  if (!nextVoyageId) {
    currentVoyageId = null; editingVoyageId = null; setVoyageEditMode(null); clearCrewViewForVoyageSwitch(); await loadCrewOptions(); updateVesselLockUI(); return;
  }
  const selectedVoyage = currentVoyages.find(item => item.id === nextVoyageId);
  if (!selectedVoyage || Number(selectedVoyage.vessel_id) !== Number(lockedVesselId)) {
    return setMsg('voyageMsg', '该航次不属于当前选中船舶，已阻止切换', true);
  }
  requestedVoyageId = null;
  currentVoyageId = nextVoyageId; pendingDownCrew = []; pendingUpCrew = []; resetDownEdit(); resetUpEdit();
  clearCrewViewForVoyageSwitch();
  const voyage = currentVoyages.find(item => item.id === currentVoyageId);
  if (voyage) {
    setVoyageEditMode(voyage);
    await fetch(`/api/voyages/${currentVoyageId}/touch`, {method:'POST'});
  }
  await loadCrewOptions();
  updateVesselLockUI();
});
$('voyageForm').addEventListener('input', () => {
  voyageDirty = true;
  updateVesselLockUI();
});
$('voyageForm').addEventListener('change', () => {
  voyageDirty = true;
  updateVesselLockUI();
});
$('refreshBtn').addEventListener('click', refresh);
$('vesselSearch').addEventListener('input', event => renderVesselOptions(event.target.value));
 $('vesselSelect').addEventListener('change', async event => {
  const vessel = currentVessels.find(item => item.id === Number(event.target.value));
  if (vessel) {
    if (voyageDirty) {
      event.target.value = lockedVesselId ? String(lockedVesselId) : '';
      return setMsg('vesselMsg', '当前航次有未保存修改，请先保存后再切换船舶', true);
    }
    requestedVoyageId = null;
    lockedVesselId = vessel.id;
    currentVoyageId = null;
    editingVoyageId = null;
    voyageDirty = false;
    isNewVesselMode = false;
    pendingDownCrew = []; pendingUpCrew = []; resetDownEdit(); resetUpEdit();
    clearCrewViewForVoyageSwitch();
    syncVesselSearch(vessel.id);
    fillVesselForm(vessel);
    await refresh();
    updateVesselLockUI();
    setMsg('vesselMsg', `已选择船舶档案：${vesselDisplay(vessel)}；保存航次时将自动绑定该船`);
  }
});
function renderSummary(data) {
  const summary = data.summary_keywords || {};
  const female = Number(summary.female_count || 0);
  return [
    `船名：${summary.vessel_chinese_name || '待人工填写'} / ${summary.vessel_english_name || '待人工填写'}`,
    `IMO：${summary.imo || '待人工填写'}｜船舶国籍：${summary.vessel_nationality || '待人工填写'}`,
    `进港航次号：${summary.inbound_voyage_no || '待人工填写'}`,
    `出港航次号：${summary.outbound_voyage_no || '待人工填写'}`,
    `泊位：${summary.berth || '待人工填写'}`,
    `港序：${summary.port_sequence || '待人工填写'}`,
    `靠泊时间：${summary.arrival_time || '待人工填写'}`,
    `离泊时间：${summary.departure_time || '待人工填写'}`,
    `船员总数：${summary.crew_count ?? 0}名`,
    `船员国籍分布：${summary.nationality_distribution || '待人工填写'}`,
    `女性船员：${female ? `${female}名` : '无'}`,
  ].join('\n');
}
$('summaryBtn').addEventListener('click', async () => { if (currentVoyageId) $('output').textContent = renderSummary(await fetch(`/api/voyages/${currentVoyageId}/summary`).then(r => r.json())); });
$('forecastBtn').addEventListener('click', async () => { if (currentVoyageId) { const data = await fetch(`/api/voyages/${currentVoyageId}/forecast`, {method:'POST'}).then(r => r.json()); $('output').textContent = data.content + `\n\n待补字段：${data.missing_fields.join('、') || '无'}`; } });
$('tonnageBtn').addEventListener('click', () => { if (currentVoyageId) window.open(`/api/voyages/${currentVoyageId}/export/tonnage`, '_blank'); });
$('strongGeneralBtn').addEventListener('click', () => { if (currentVoyageId) window.open(`/api/voyages/${currentVoyageId}/export/strong-general`, '_blank'); });
$('customsCargoBtn').addEventListener('click', () => { if (currentVoyageId) window.open(`/api/voyages/${currentVoyageId}/export/customs-cargo`, '_blank'); });
$('crewChangeBtn').addEventListener('click', () => { if (currentVoyageId) window.open(`/api/voyages/${currentVoyageId}/export/crew-change`, '_blank'); });
$('crewChangeCustomsBtn').addEventListener('click', () => { if (currentVoyageId) window.open(`/api/voyages/${currentVoyageId}/export/crew-change-customs`, '_blank'); });
$('healthDeclarationBtn').addEventListener('click', () => { if (currentVoyageId) window.open(`/api/voyages/${currentVoyageId}/export/health-declaration`, '_blank'); });
$('outerFieldReceiptBtn').addEventListener('click', () => { if (currentVoyageId) window.open('/api/voyages/' + currentVoyageId + '/export/outer-field-receipt', '_blank'); });
$('borderInspectionBtn').addEventListener('click', () => { if (currentVoyageId) window.open('/api/voyages/' + currentVoyageId + '/export/border-inspection', '_blank'); });
$('maritimePreapprovalBtn').addEventListener('click', () => { if (currentVoyageId) window.open('/api/voyages/' + currentVoyageId + '/export/maritime-preapproval', '_blank'); });
$('temporaryEntryBtn').addEventListener('click', () => { if (currentVoyageId && temporaryEntryApplicants.length) window.open('/api/voyages/' + currentVoyageId + '/export/temporary-entry', '_blank'); });
$('exitStampBtn').addEventListener('click', () => { if (currentVoyageId && exitStampApplicants.length) window.open('/api/voyages/' + currentVoyageId + '/export/exit-stamp', '_blank'); });
$('temporaryEntryForm').addEventListener('submit', async event => {
  event.preventDefault();
  const crewMemberId = Number($('temporaryEntryCrewSelect').value);
  if (!currentVoyageId || !crewMemberId) return setMsg('temporaryEntryMsg', '请先选择船员', true);
  const res = await fetch(`/api/voyages/${currentVoyageId}/temporary-entry`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({crew_member_id: crewMemberId})});
  if (!res.ok) return setMsg('temporaryEntryMsg', await res.text(), true);
  await loadCrewOptions();
  setMsg('temporaryEntryMsg', '已加入临入申请名单');
});
$('exitStampForm').addEventListener('submit', async event => {
  event.preventDefault();
  const crewMemberId = Number($('exitStampCrewSelect').value);
  if (!currentVoyageId || !crewMemberId) return setMsg('exitStampMsg', '请先选择船员', true);
  const res = await fetch(`/api/voyages/${currentVoyageId}/exit-stamp`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({crew_member_id: crewMemberId})});
  if (!res.ok) return setMsg('exitStampMsg', await res.text(), true);
  await loadCrewOptions();
  setMsg('exitStampMsg', '已加入出境章申请名单');
});
bindDateInput('#purchaseDateManual, #upCrewForm input[name="birth_date"]');
const purchaseDateCalendar = $('tonnageForm').elements.purchase_date;
const purchaseDateManual = $('purchaseDateManual');
const tonnageDuration = $('tonnageForm').elements.duration_days;
purchaseDateCalendar.addEventListener('change', () => {
  purchaseDateManual.value = normalizeDateInput(purchaseDateCalendar.value);
  updateTonnageQuote();
});
purchaseDateManual.addEventListener('input', () => {
  const value = normalizeDateInput(purchaseDateManual.value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) purchaseDateCalendar.value = value;
  updateTonnageQuote();
});
tonnageDuration.addEventListener('change', updateTonnageQuote);
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-delete-temporary-entry]');
  if (!button) return;
  const res = await fetch(`/api/temporary-entry/${button.dataset.deleteTemporaryEntry}`, {method:'DELETE'});
  if (!res.ok) return setMsg('temporaryEntryMsg', await res.text(), true);
  await loadCrewOptions();
  setMsg('temporaryEntryMsg', '已从临入申请名单移除');
});
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-delete-exit-stamp]');
  if (!button) return;
  const res = await fetch(`/api/exit-stamp/${button.dataset.deleteExitStamp}`, {method:'DELETE'});
  if (!res.ok) return setMsg('exitStampMsg', await res.text(), true);
  await loadCrewOptions();
  setMsg('exitStampMsg', '已从出境章申请名单移除');
});
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
setupPreferentialCountrySettings();
refresh();
checkSeafarerAgent(true);
