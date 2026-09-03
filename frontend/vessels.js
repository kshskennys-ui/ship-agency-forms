const $ = (id) => document.getElementById(id);
const formJSON = (form) => Object.fromEntries(new FormData(form).entries());
const nullable = (value) => value === '' ? null : value;
let vessels = [];
let editingId = null;
let editingExtra = {};

function setMsg(id, text, error = false) {
  const node = $(id); node.textContent = text; node.className = `message ${error ? 'error' : 'success'}`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
}

function filteredVessels() {
  const keyword = $('searchInput').value.trim().toLowerCase();
  if (!keyword) return vessels;
  return vessels.filter(v => [v.imo, v.chinese_name, v.english_name, v.shipping_company].some(value => String(value ?? '').toLowerCase().includes(keyword)));
}

function renderTable() {
  const rows = filteredVessels();
  $('recordCount').textContent = `${rows.length} / ${vessels.length} 条档案`;
  $('vesselTableBody').innerHTML = rows.length ? rows.map(v => `<tr>
    <td>${escapeHtml(v.imo || '未填写')}</td><td>${escapeHtml(v.chinese_name)}</td><td>${escapeHtml(v.english_name)}</td>
    <td>${escapeHtml(v.nationality)}</td><td>${escapeHtml(v.call_sign)}</td><td>${escapeHtml(v.shipping_company)}</td>
    <td>${escapeHtml(v.net_tonnage ?? '')} / ${escapeHtml(v.gross_tonnage ?? '')}</td>
    <td><div class="table-actions"><button class="ghost small-button" data-action="edit" data-id="${v.id}">编辑</button><button class="danger small-button" data-action="delete" data-id="${v.id}">删除</button></div></td>
  </tr>`).join('') : '<tr><td colspan="8">暂无匹配船舶档案</td></tr>';
}

async function loadVessels() {
  const res = await fetch('/api/vessels');
  if (!res.ok) return setMsg('listMsg', '船舶档案读取失败', true);
  vessels = await res.json(); renderTable(); setMsg('listMsg', '船舶档案已刷新');
}

function resetForm() {
  editingId = null; editingExtra = {}; $('vesselForm').reset(); $('formTitle').textContent = '新增船舶档案'; setMsg('formMsg', '');
}

function editVessel(id) {
  const vessel = vessels.find(item => item.id === id);
  if (!vessel) return;
  editingId = id; $('formTitle').textContent = `编辑船舶档案 #${id}`;
  for (const key of ['imo','chinese_name','english_name','nationality','call_sign','shipping_company','net_tonnage','gross_tonnage','mmsi']) $('vesselForm').elements[key].value = vessel[key] ?? '';
  editingExtra = {...(vessel.extra || {})};
  $('vesselForm').elements.nationality_certificate_no.value = editingExtra.nationality_certificate_no
    || editingExtra.nationality_certificate_number
    || editingExtra.registry_certificate_no
    || '';
  window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});
}

async function deleteVessel(id) {
  const vessel = vessels.find(item => item.id === id);
  if (!vessel || !window.confirm(`确定删除船舶档案“${vessel.chinese_name || vessel.english_name || vessel.imo || id}”吗？`)) return;
  let res = await fetch(`/api/vessels/${id}`, {method:'DELETE'});
  if (res.status === 409) {
    const confirmed = window.confirm(`该船舶已有历史航次。继续删除将同时删除其航次、船员、换班、吨税和预报数据，且无法恢复。确定继续吗？`);
    if (!confirmed) return setMsg('listMsg', '已取消删除');
    res = await fetch(`/api/vessels/${id}?cascade=true`, {method:'DELETE'});
  }
  if (!res.ok) return setMsg('listMsg', await res.text(), true);
  if (editingId === id) resetForm(); await loadVessels();
}

$('vesselForm').addEventListener('submit', async event => {
  event.preventDefault(); const body = formJSON(event.target);
  body.extra = {...editingExtra};
  const certificateNo = body.nationality_certificate_no.trim();
  delete body.nationality_certificate_no;
  delete body.extra.registry_certificate_no;
  delete body.extra.nationality_certificate_number;
  if (certificateNo) body.extra.nationality_certificate_no = certificateNo;
  for (const key of ['imo','chinese_name','english_name','nationality','call_sign','shipping_company','mmsi']) body[key] = nullable(body[key]);
  for (const key of ['net_tonnage','gross_tonnage']) body[key] = body[key] ? Number(body[key]) : null;
  const url = editingId ? `/api/vessels/${editingId}` : '/api/vessels';
  const res = await fetch(url, {method: editingId ? 'PUT' : 'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (!res.ok) return setMsg('formMsg', await res.text(), true);
  setMsg('formMsg', editingId ? '船舶档案已更新' : '船舶档案已新增'); resetForm(); await loadVessels();
});

$('vesselTableBody').addEventListener('click', event => { const button = event.target.closest('button[data-action]'); if (!button) return; const id = Number(button.dataset.id); button.dataset.action === 'edit' ? editVessel(id) : deleteVessel(id); });
$('searchInput').addEventListener('input', renderTable);
$('refreshBtn').addEventListener('click', loadVessels);
$('resetBtn').addEventListener('click', resetForm);
loadVessels();
