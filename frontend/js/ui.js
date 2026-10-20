// Small DOM helpers: element builder, icons, formatting, modal, toast, forms, tables.
import { ApiError } from './api.js';

// ── Element builder ────────────────────────────────────────────────────────
// h('div', {class: 'card', onclick: fn}, 'text', childNode, [more, children])
const BOOLEAN_PROPS = new Set(['disabled', 'checked', 'selected', 'readOnly', 'required', 'hidden', 'multiple', 'autofocus']);

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value === undefined || value === null || value === false) continue;
      if (key === 'class') el.className = value;
      else if (key === 'style' && typeof value === 'object') Object.assign(el.style, value);
      else if (key === 'dataset') Object.assign(el.dataset, value);
      else if (key.startsWith('on') && typeof value === 'function') el.addEventListener(key.slice(2).toLowerCase(), value);
      else if (key === 'value') el.value = value;
      else if (BOOLEAN_PROPS.has(key)) el[key] = true;
      else el.setAttribute(key, value === true ? '' : value);
    }
  }
  append(el, children);
  return el;
}

export function append(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function replaceContent(el, ...children) {
  clear(el);
  return append(el, children);
}

// ── Icons (inline SVG, Feather-style) ──────────────────────────────────────
const ICONS = {
  grid: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>',
  cart: '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.7 13.4a2 2 0 0 0 2 1.6h9.7a2 2 0 0 0 2-1.6L23 6H6"/>',
  receipt: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/>',
  package: '<path d="M16.5 9.4 7.5 4.2"/><path d="M21 16V8a2 2 0 0 0-1-1.7l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.7l7 4a2 2 0 0 0 2 0l7-4a2 2 0 0 0 1-1.7z"/><path d="M3.3 7 12 12l8.7-5M12 22V12"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  truck: '<path d="M1 3h15v13H1zM16 8h4l3 3v5h-7z"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/>',
  users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
  card: '<rect x="1" y="4" width="22" height="16" rx="2"/><path d="M1 10h22"/>',
  briefcase: '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
  dollar: '<path d="M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
  chart: '<path d="M18 20V10M12 20V4M6 20v-6"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
  sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
  menu: '<path d="M3 12h18M3 6h18M3 18h18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  printer: '<path d="M6 9V2h12v7M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><path d="M6 14h12v8H6z"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
  message: '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.5 8.5 0 0 1-3.8-.9L3 21l1.9-5.2A8.4 8.4 0 0 1 12.5 3 8.5 8.5 0 0 1 21 11.5z"/>',
  edit: '<path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  refresh: '<path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15"/>',
  trash: '<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
};

export function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('aria-hidden', 'true');
  svg.innerHTML = ICONS[name] || '';
  return svg;
}

// ── Formatting ─────────────────────────────────────────────────────────────
const SYMBOLS = { NGN: '₦', USD: '$', GHS: '₵', KES: 'KSh ', ZAR: 'R' };
let currentCurrency = 'NGN';
export function setCurrency(code) { currentCurrency = code || 'NGN'; }
export function currencyCode() { return currentCurrency; }

export function money(value, currency) {
  const n = Number(value);
  const safe = Number.isFinite(n) ? n : 0;
  const symbol = SYMBOLS[currency || currentCurrency] ?? `${currency || currentCurrency} `;
  const text = Math.abs(safe).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  return `${safe < 0 ? '-' : ''}${symbol}${text}`;
}

export function num(value, digits = 0) {
  const n = Number(value);
  return (Number.isFinite(n) ? n : 0).toLocaleString('en-US', { maximumFractionDigits: digits });
}

function parseDate(value) {
  if (!value) return null;
  // Date-only strings (YYYY-MM-DD) are local calendar dates — don't let UTC parsing shift them.
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [y, m, d] = value.split('-').map(Number);
    return new Date(y, m - 1, d);
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function fmtDate(value) {
  const d = parseDate(value);
  return d ? d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '—';
}

export function fmtDateTime(value) {
  const d = parseDate(value);
  return d ? `${d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })}, ${d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}` : '—';
}

export function isoDate(date) {
  const d = date || new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function daysAgo(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return isoDate(d);
}

export function debounce(fn, wait = 300) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), wait); };
}

// ── Small components ───────────────────────────────────────────────────────
export function badge(text, kind = '') {
  return h('span', { class: `badge ${kind}`.trim() }, text);
}

export function spinner(text = 'Loading…') {
  return h('div', { class: 'loading' }, h('div', { class: 'spinner' }), text);
}

export function empty(title, text) {
  return h('div', { class: 'empty' }, h('strong', null, title), text || null);
}

export function errorBox(message) {
  return h('div', { class: 'alert danger' }, message);
}

export function pageHead(title, subtitle, ...actions) {
  return h('div', { class: 'page-head' },
    h('div', null, h('h1', null, title), subtitle ? h('p', null, subtitle) : null),
    h('div', { class: 'page-actions' }, ...actions),
  );
}

export function card(title, body, { actions, flush = true } = {}) {
  return h('div', { class: 'card' },
    title ? h('div', { class: 'card-head' }, h('h2', null, title), actions || null) : null,
    h('div', { class: `card-body${flush ? ' flush' : ''}` }, body),
  );
}

export function statCard(label, value, sub, extra) {
  return h('div', { class: 'card stat' },
    h('div', { class: 'label' }, label),
    h('div', { class: 'value' }, value),
    sub ? h('div', { class: 'sub' }, sub) : null,
    extra || null,
  );
}

export function table(columns, rows, emptyNode) {
  if (!rows.length) return emptyNode || empty('Nothing here yet');
  const head = h('tr', null, columns.map((c) => h('th', { class: c.align === 'right' ? 'right' : '' }, c.label)));
  const body = rows.map((row) => h('tr', null, columns.map((c) => {
    const content = c.render ? c.render(row) : row[c.key];
    return h('td', { class: [c.align === 'right' ? 'right' : '', c.class || ''].join(' ').trim() }, content === undefined || content === null ? '' : content);
  })));
  return h('div', { class: 'table-wrap' }, h('table', { class: 'table' }, h('thead', null, head), h('tbody', null, body)));
}

export function pager({ page, count, pageSize, onPage }) {
  const pages = Math.max(1, Math.ceil(count / pageSize));
  const from = count ? (page - 1) * pageSize + 1 : 0;
  const to = Math.min(count, page * pageSize);
  return h('div', { class: 'pager' },
    h('span', null, `${from}–${to} of ${count}`),
    h('div', { class: 'btns' },
      h('button', { class: 'btn btn-sm', disabled: page <= 1, onclick: () => onPage(page - 1) }, 'Previous'),
      h('span', { class: 'small', style: { alignSelf: 'center' } }, `Page ${page} of ${pages}`),
      h('button', { class: 'btn btn-sm', disabled: page >= pages, onclick: () => onPage(page + 1) }, 'Next'),
    ),
  );
}

// ── Toast ──────────────────────────────────────────────────────────────────
export function toast(message, type = '') {
  const stack = document.getElementById('toasts');
  const node = h('div', { class: `toast ${type}`.trim(), role: 'status' }, message);
  stack.append(node);
  setTimeout(() => node.remove(), type === 'error' ? 6000 : 3500);
}

export function toastError(error) {
  toast(error instanceof ApiError || error instanceof Error ? error.message : String(error), 'error');
}

// ── Modal ──────────────────────────────────────────────────────────────────
export function openModal({ title, body, footer, wide = false, onClose }) {
  const backdrop = h('div', { class: 'modal-backdrop' });
  const close = () => {
    document.removeEventListener('keydown', onKey);
    backdrop.remove();
    if (onClose) onClose();
  };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  const modal = h('div', { class: `modal${wide ? ' wide' : ''}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
    h('div', { class: 'modal-head' }, h('h3', null, title),
      h('button', { class: 'btn btn-ghost btn-sm', 'aria-label': 'Close', onclick: close }, icon('x'))),
    h('div', { class: 'modal-body' }, body),
    footer && footer.length ? h('div', { class: 'modal-foot' }, ...footer) : null,
  );
  backdrop.append(modal);
  backdrop.addEventListener('mousedown', (e) => { if (e.target === backdrop) close(); });
  document.addEventListener('keydown', onKey);
  document.body.append(backdrop);
  const first = modal.querySelector('input, select, textarea');
  if (first) first.focus();
  return { close, el: modal };
}

export function confirmDialog({ title = 'Are you sure?', message, confirmText = 'Confirm', danger = false }) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value) => { if (!settled) { settled = true; resolve(value); } };
    const modal = openModal({
      title,
      body: h('p', { style: { margin: 0 } }, message),
      footer: [
        h('button', { class: 'btn', onclick: () => { modal.close(); } }, 'Cancel'),
        h('button', { class: `btn ${danger ? 'btn-danger' : 'btn-primary'}`, onclick: () => { finish(true); modal.close(); } }, confirmText),
      ],
      onClose: () => finish(false),
    });
  });
}

// ── Forms ──────────────────────────────────────────────────────────────────
// fields: [{name,label,type,required,options,hint,full,min,step,placeholder,value,maxlength}]
export function buildForm(fields) {
  const controls = {};
  const errorEl = h('div', { class: 'form-error', hidden: true });
  const form = h('form', { novalidate: false });
  const grid = h('div', { class: 'form-grid' });
  for (const f of fields) {
    let control;
    if (f.type === 'select') {
      control = h('select', { class: 'select', name: f.name, required: f.required },
        (f.options || []).map((o) => h('option', { value: o.value, selected: String(o.value) === String(f.value ?? '') }, o.label)));
    } else if (f.type === 'textarea') {
      control = h('textarea', { class: 'textarea', name: f.name, required: f.required, placeholder: f.placeholder, maxlength: f.maxlength }, f.value ?? '');
    } else if (f.type === 'checkbox') {
      control = h('input', { type: 'checkbox', name: f.name, checked: !!f.value });
    } else {
      control = h('input', {
        class: 'input', type: f.type || 'text', name: f.name, required: f.required,
        placeholder: f.placeholder, min: f.min, max: f.max, step: f.step, maxlength: f.maxlength,
        autocomplete: f.autocomplete || 'off', readOnly: f.readOnly, value: f.value ?? '',
      });
    }
    controls[f.name] = control;
    const wrapper = f.type === 'checkbox'
      ? h('div', { class: `field${f.full ? ' full' : ''}` }, h('label', { class: 'check' }, control, f.label))
      : h('div', { class: `field${f.full ? ' full' : ''}` }, h('label', null, f.label + (f.required ? ' *' : '')), control, f.hint ? h('div', { class: 'hint' }, f.hint) : null);
    grid.append(wrapper);
  }
  form.append(errorEl, grid);
  form.addEventListener('submit', (e) => e.preventDefault());
  return {
    el: form,
    controls,
    get() {
      const out = {};
      for (const f of fields) {
        const c = controls[f.name];
        out[f.name] = f.type === 'checkbox' ? c.checked : c.value;
      }
      return out;
    },
    valid: () => form.reportValidity(),
    error(message) { errorEl.textContent = message || ''; errorEl.hidden = !message; },
  };
}

export async function withBusy(button, fn) {
  button.disabled = true;
  try { return await fn(); } finally { button.disabled = false; }
}

// Opens a modal containing a form; `onSubmit(values)` may throw ApiError to show inline.
export function formModal({ title, fields, submitText = 'Save', onSubmit, wide = false, intro }) {
  const form = buildForm(fields);
  const submit = h('button', { class: 'btn btn-primary', type: 'button' }, submitText);
  const modal = openModal({
    title, wide,
    body: h('div', null, intro || null, form.el),
    footer: [h('button', { class: 'btn', type: 'button', onclick: () => modal.close() }, 'Cancel'), submit],
  });
  const run = async () => {
    if (!form.valid()) return;
    form.error('');
    submit.disabled = true;
    try {
      await onSubmit(form.get(), modal);
      modal.close();
    } catch (e) {
      form.error(e.message || 'Something went wrong.');
      submit.disabled = false;
    }
  };
  submit.addEventListener('click', run);
  form.el.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA') { e.preventDefault(); run(); }
  });
  return modal;
}

export function opt(value, label) { return { value, label }; }
