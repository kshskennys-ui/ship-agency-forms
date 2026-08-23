const $ = (id) => document.getElementById(id);
let voyages = [];

function setMsg(id, text, error = false) {
  const node = $(id); node.textContent = text; node.className = `message ${error ? 'error' : 'success'}`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
}

function formatDateTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 16) : '未填写';
}

function filteredVoyages() {
  const keyword = $('searchInput').value.trim().toLowerCase();
  if (!keyword) return voyages;
  return voyages.filter(v => [v.vessel_chinese_name, v.vessel_english_name, v.vessel_imo, v.inbound_voyage_no, v.outbound_voyage_no].some(value => String(value ?? '').toLowerCase().includes(keyword)));
}

function renderTable() {
  const rows = filteredVoyages();
  $('recordCount').textContent = `${rows.length} / ${voyages.length} 条航次记录`;
  $('voyageTableBody').innerHTML = rows.length ? rows.map(v => `<tr>
    <td>${v.id}</td><td>${escapeHtml(v.vessel_chinese_name || '')} ${escapeHtml(v.vessel_english_name || '')}<br><span class="muted">IMO ${escapeHtml(v.vessel_imo || '未填写')}</span></td>
    <td>${escapeHtml(v.inbound_voyage_no || '未填写')}</td><td>${escapeHtml(v.outbound_voyage_no || '未填写')}</td>
    <td>${formatDateTime(v.arrival_time)}</td><td>${formatDateTime(v.departure_time)}</td><td>${formatDateTime(v.updated_at)}</td>
    <td><div class="table-actions"><button class="primary small-button" type="button" data-action="resume" data-id="${v.id}">继续操作</button><button class="danger small-button" type="button" data-action="delete" data-id="${v.id}">删除</button></div></td>
  </tr>`).join('') : '<tr><td colspan="8">暂无匹配航次记录</td></tr>';
}

async function loadVoyages() {
  const res = await fetch('/api/voyages');
  if (!res.ok) return setMsg('listMsg', '航次历史读取失败', true);
  voyages = await res.json(); renderTable(); setMsg('listMsg', '航次历史已刷新');
}

async function deleteVoyage(id) {
  const voyage = voyages.find(item => item.id === id);
  if (!voyage || !window.confirm(`确定删除${voyage.vessel_chinese_name || voyage.vessel_english_name || ''}的航次记录“${voyage.inbound_voyage_no || ''} → ${voyage.outbound_voyage_no || ''}”吗？关联的船员、换班、吨税和预报数据也会一并删除。`)) return;
  const res = await fetch(`/api/voyages/${id}`, {method:'DELETE'});
  if (!res.ok) return setMsg('listMsg', await res.text(), true);
  await loadVoyages();
  setMsg('listMsg', '航次记录及其关联业务数据已删除');
}

$('voyageTableBody').addEventListener('click', event => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const id = Number(button.dataset.id);
  if (button.dataset.action === 'resume') window.location.href = `/?voyage=${id}`;
  if (button.dataset.action === 'delete') deleteVoyage(id);
});
$('searchInput').addEventListener('input', renderTable);
$('refreshBtn').addEventListener('click', loadVoyages);
loadVoyages();
