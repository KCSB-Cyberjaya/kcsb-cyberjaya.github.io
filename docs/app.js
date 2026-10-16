'use strict';
let payload = null;
let selectedId = null;
const $ = id => document.getElementById(id);
const fmt = v => v == null ? 'Not scanned' : Number(v).toLocaleString();
const newEl = (tag, cls = '', text = '') => {
  const e = document.createElement(tag); if (cls) e.className = cls; e.textContent = text; return e;
};
const add = (parent, tag, cls, text) => { const e = newEl(tag, cls, text); parent.appendChild(e); return e; };
const errorCount = row => (row.corrupted_images || 0) + row.invalid_yolo_lines + row.orphan_label_files + row.image_list_missing + row.duplicate_image_keys + row.duplicate_label_keys;
const filtered = () => {
  if (!payload) return [];
  const cat = $('category').value, q = $('search').value.trim().toLowerCase(), qc = $('qcOnly').checked;
  const r = payload.datasets.filter(row =>
    (cat === 'all' || row.category === cat) && (!qc || errorCount(row) > 0) &&
    (!q || row.dataset_id.toLowerCase().includes(q) || Object.keys(row.class_counts || {}).some(k => k.toLowerCase().includes(q)))
  );
  const sort = $('sort').value;
  return r.sort((a, b) => sort === 'path' ? a.dataset_id.localeCompare(b.dataset_id) :
    (sort === 'images' ? b.images - a.images : sort === 'boxes' ? b.boxes - a.boxes :
    sort === 'errors' ? errorCount(b) - errorCount(a) : b.dataset_size_mb - a.dataset_size_mb)
    || a.dataset_id.localeCompare(b.dataset_id));
};
function renderMetrics(rows) {
  const n = (key) => rows.reduce((sum, d) => sum + (Number(d[key]) || 0), 0);
  const corrupt = rows.every(d => d.corrupted_images !== null) ? fmt(n('corrupted_images')) : 'Not scanned';
  const items = [
    ['Datasets', fmt(rows.length), 'Matching current filters'],
    ['Images', fmt(n('images')), 'Exported to TrueNAS'],
    ['YOLO boxes', fmt(n('boxes')), 'Valid bounding boxes'],
    ['Invalid labels', fmt(n('invalid_yolo_lines')), 'Invalid YOLO lines'],
    ['Corrupt images', corrupt, 'Full image decoding enabled if numeric'],
  ];
  $('metrics').replaceChildren(...items.map(([title, count, desc]) => {
    const e = newEl('div', 'metric'); add(e, 'h3', '', title); add(e, 'strong', '', count);
    add(e, 'small', '', desc); return e;
  }));
}
function drawBars(id, items, maxRows = 14) {
  const parent = $(id); parent.replaceChildren();
  const selected = items.sort((a,b) => b[1] - a[1]).slice(0, maxRows);
  if (!selected.length) { add(parent, 'p', 'muted', 'No data.'); return; }
  const max = Math.max(1, ...selected.map(x => x[1]));
  for (const [key, val] of selected) {
    const r = add(parent, 'div', 'barRow'); const label = add(r, 'span', 'barLabel', key); label.title = key;
    const track = add(r, 'div', 'barBg'); const fill = add(track, 'div', 'barFill');
    fill.style.width = `${100 * val / max}%`; fill.setAttribute('aria-label', `${key}: ${val}`);
    add(r, 'span', 'barNumber', fmt(val));
  }
}
function renderCharts(rows) {
  const cats = new Map(); const classes = new Map();
  for (const row of rows) {
    cats.set(row.category, (cats.get(row.category) || 0) + row.images);
    for (const [name, count] of Object.entries(row.class_counts || {})) classes.set(name, (classes.get(name) || 0) + count);
  }
  drawBars('categoryChart', Array.from(cats.entries()));
  drawBars('classChart', Array.from(classes.entries()));
}
function showDetail(row) {
  selectedId = row.dataset_id;
  const panel = $('detail'); panel.hidden = false; panel.replaceChildren();
  const close = add(panel, 'button', 'close', 'Close ×'); close.type='button'; close.onclick=()=>{ panel.hidden=true; selectedId=null; renderRows(filtered());};
  add(panel, 'h2', '', row.dataset_id);
  const summary = add(panel, 'div', 'details');
  for (const [k,v] of [['Category', row.category], ['Format', row.kind], ['Image MB', fmt(row.dataset_size_mb)], ['Missing labels (may be valid)', fmt(row.images_without_label_file)], ['Orphan labels', fmt(row.orphan_label_files)], ['Duplicate keys', fmt(row.duplicate_image_keys + row.duplicate_label_keys)], ['Train.txt missing', fmt(row.image_list_missing)]]) {
    const item=add(summary, 'span', '', `${k}: ${v}`); item.title=k;
  }
  const grid=add(panel, 'div', 'detailGrid'); const cls=add(grid,'div');add(cls,'h3','','Bounding boxes by class');
  for (const [label,value] of Object.entries(row.class_counts || {}).sort((a,b)=>b[1]-a[1])) {
    add(cls,'div','details',`${label}: ${fmt(value)} boxes`);
  }
  if (!Object.keys(row.class_counts || {}).length) add(cls,'p','muted','No recognized class counts.');
  const qc=add(grid,'div');add(qc,'h3','','QC samples (first 20)');
  const list=add(qc,'ul'); for (const msg of row.qc_samples || []) add(list,'li','',msg);
  if (row.qc_redacted) add(qc,'p','muted','File-level QC messages are withheld from the public dashboard. Check .reports on the Windows scanner.');
  else if (!(row.qc_samples || []).length) add(qc,'p','good','No sampled QC warnings.');
  panel.scrollIntoView({behavior:'smooth',block:'nearest'});
}
function renderRows(rows) {
  $('rowCount').textContent = `${fmt(rows.length)} dataset(s) match`;
  const tbody=$('datasetRows'); tbody.replaceChildren(); $('empty').hidden = rows.length !== 0;
  for (const d of rows) {
    const tr = add(tbody, 'tr', d.dataset_id===selectedId?'chosen':'');tr.tabIndex=0;
    add(tr,'td','pathCell',d.dataset_id);
    for (const key of ['images','label_files','boxes','corrupted_images','invalid_yolo_lines','image_list_missing']) {
      add(tr,'td', (key==='corrupted_images' && d[key]>0) || (key==='invalid_yolo_lines' && d[key]>0) ? 'negative' : '',fmt(d[key]));
    }
    tr.onclick=()=>{showDetail(d);renderRows(rows);};tr.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();tr.click();}};
  }
  if (selectedId && !rows.some(d=>d.dataset_id===selectedId)) { selectedId=null; $('detail').hidden=true; }
}
function refresh() { const rows=filtered();renderMetrics(rows);renderCharts(rows);renderRows(rows); }
function csvCell(value) { let s=String(value == null?'':value); if (/^[\s]*[=+@-]/.test(s)) s="'"+s; return `"${s.replace(/"/g,'""')}"`; }
function downloadCsv() {
  const cols=['category','dataset_id','images','label_files','boxes','images_with_boxes','corrupted_images','invalid_yolo_lines','images_without_label_file','orphan_label_files','image_list_missing','dataset_size_mb'];
  const lines=[cols.join(','),...filtered().map(d=>cols.map(k=>csvCell(d[k])).join(','))];
  const safe=lines.join('\r\n');
  const blob=new Blob(['\uFEFF',safe],{type:'text/csv;charset=utf-8'});const url=URL.createObjectURL(blob);
  const a=document.createElement('a');a.href=url;a.download='yolo-filtered-datasets.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function start() {
  try {
    const response=await fetch('data.json',{cache:'no-store'});
    if(!response.ok)throw new Error(`HTTP ${response.status} loading data.json`);
    payload=await response.json();
    $('updated').textContent = payload.scanned_at ? `Last scanned: ${new Date(payload.scanned_at).toLocaleString()} (your timezone)` : 'Awaiting first scan';
    $('notScanned').hidden = !!payload.scanned_at;
    $('privacy').hidden = !!payload.public_names_enabled;
    for (const key of Object.keys(payload.categories || {}).sort()) {
      const option=add($('category'),'option','',key);option.value=key;
    }
    for(const k of ['category','sort','qcOnly','search']) $(k).addEventListener('input',refresh);
    $('downloadCsv').addEventListener('click',downloadCsv);
    if ((payload.failures||[]).length) {
      $('scanErrors').hidden=false;add($('scanErrors'),'h2','','Scan failures');
      const ul=add($('scanErrors'),'ul');for(const f of payload.failures)add(ul,'li','',f);
    }
    refresh();
  } catch(e) {
    $('error').hidden=false;
    $('error').textContent=`Could not load dashboard data: ${e.message}. Run the Windows scan first, then view through a local web server (not file://).`;
  }
}
start();
