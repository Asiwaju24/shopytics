// Reusable searchable, filterable, paginated table card backed by a DRF list endpoint.
import { Api } from './api.js';
import { h, table, pager, spinner, errorBox, replaceContent, debounce, empty } from './ui.js';

/**
 * @param {object} o
 * @param {string} o.path            API list endpoint, e.g. '/products/'
 * @param {Array}  o.columns         [{label, render(row)|key, align}]
 * @param {Array}  [o.filters]       [{name, label, options:[{value,label}], value, map?(value)->params}] — sent as query params
 * @param {Function} [o.params]      () => extra query params (dynamic)
 * @param {string} [o.searchPlaceholder] set to null to hide search
 * @param {Node}   [o.emptyNode]
 * @param {Node[]} [o.toolbarExtra]  extra nodes placed on the right of the toolbar
 */
export function listView(o) {
  const pageSize = o.pageSize || 20;
  const status = { page: 1, search: '', filters: {}, seq: 0 };
  (o.filters || []).forEach((f) => { status.filters[f.name] = f.value ?? ''; });

  const body = h('div');
  const foot = h('div');
  const toolbarChildren = [];

  if (o.searchPlaceholder !== null) {
    const input = h('input', { class: 'input grow', type: 'search', placeholder: o.searchPlaceholder || 'Search…', 'aria-label': 'Search' });
    input.addEventListener('input', debounce(() => { status.search = input.value.trim(); status.page = 1; reload(); }, 300));
    toolbarChildren.push(input);
  }
  (o.filters || []).forEach((f) => {
    const select = h('select', { class: 'select', 'aria-label': f.label },
      f.options.map((opt) => h('option', { value: opt.value, selected: String(opt.value) === String(f.value ?? '') }, opt.label)));
    select.addEventListener('change', () => { status.filters[f.name] = select.value; status.page = 1; reload(); });
    toolbarChildren.push(select);
  });
  if (o.toolbarExtra) toolbarChildren.push(...o.toolbarExtra);

  const root = h('div', { class: 'card' },
    toolbarChildren.length ? h('div', { class: 'toolbar' }, ...toolbarChildren) : null,
    body, foot,
  );

  async function reload() {
    const seq = ++status.seq;
    replaceContent(body, spinner());
    replaceContent(foot);
    try {
      const filterParams = {};
      (o.filters || []).forEach((f) => {
        const value = status.filters[f.name];
        if (f.map) Object.assign(filterParams, f.map(value)); else filterParams[f.name] = value;
      });
      const params = { page: status.page, page_size: pageSize, search: status.search, ...filterParams, ...(o.params ? o.params() : {}) };
      const data = await Api.get(o.path, params);
      if (seq !== status.seq) return;           // a newer request superseded this one
      const rows = Array.isArray(data) ? data : data.results;
      const count = Array.isArray(data) ? data.length : data.count;
      o.onLoad && o.onLoad(rows, count);
      if (!rows.length && status.page > 1) { status.page -= 1; return reload(); }
      replaceContent(body, table(o.columns, rows, o.emptyNode || empty('Nothing found', status.search ? 'Try a different search.' : '')));
      if (count > pageSize) {
        replaceContent(foot, pager({ page: status.page, count, pageSize, onPage: (p) => { status.page = p; reload(); } }));
      }
    } catch (e) {
      if (seq !== status.seq) return;
      replaceContent(body, errorBox(e.message));
    }
  }

  reload();
  return { el: root, reload };
}
