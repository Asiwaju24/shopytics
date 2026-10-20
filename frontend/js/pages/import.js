import { Api } from '../api.js';
import { h, num, badge, pageHead, card, table, empty, toast, toastError, replaceContent, withBusy, icon } from '../ui.js';

export async function render(container) {
  const fileInput = h('input', { type: 'file', class: 'input', accept: '.csv,.xlsx', style: { height: 'auto', padding: '7px' }, 'aria-label': 'Sales file' });
  const stockBox = h('input', { type: 'checkbox' });
  const result = h('div');
  let previewed = false;

  const preview = h('button', { class: 'btn btn-primary' }, 'Check the file');
  const commit = h('button', { class: 'btn btn-primary', disabled: true }, icon('upload'), 'Import now');

  const send = async (dryRun) => {
    const file = fileInput.files[0];
    if (!file) { toast('Choose a file first.', 'error'); return null; }
    const form = new FormData();
    form.append('file', file);
    form.append('source', 'CSV_IMPORT');
    form.append('dry_run', dryRun ? 'true' : 'false');
    form.append('update_stock', stockBox.checked ? 'true' : 'false');
    return Api.upload('/transactions/upload/', form);
  };

  const show = (r) => {
    replaceContent(result, card(r.dry_run ? 'Preview — nothing has been saved yet' : 'Import finished',
      h('div', null,
        h('div', { class: 'card-body', style: { display: 'flex', gap: '28px', flexWrap: 'wrap' } },
          [['Rows read', r.rows_parsed], ['Ready to import', r.valid_rows], ['Problems', r.invalid_rows], ['Already imported', r.duplicate_rows], ...(r.dry_run ? [] : [['Sales added', r.created_transactions]])]
            .map(([label, value]) => h('div', null, h('div', { class: 'muted small' }, label), h('div', { style: { fontSize: '20px', fontWeight: 650 } }, num(value))))),
        r.estimated_prices ? h('div', { class: 'card-body muted small' }, `${num(r.estimated_prices)} row(s) had no price, so the product's selling price was used.`) : null,
        r.issues.length ? table([{ label: 'Row', render: (i) => i.row }, { label: 'Issue', render: (i) => i.reason }], r.issues) : null)));
  };

  preview.addEventListener('click', () => withBusy(preview, async () => {
    try {
      const r = await send(true);
      if (!r) return;
      show(r);
      previewed = r.valid_rows > 0;
      commit.disabled = !previewed;
    } catch (e) { toastError(e); }
  }));
  commit.addEventListener('click', () => withBusy(commit, async () => {
    try {
      const r = await send(false);
      if (!r) return;
      show(r);
      toast(`${r.created_transactions} sales imported`, 'success');
      commit.disabled = true;
    } catch (e) { toastError(e); }
  }));
  fileInput.addEventListener('change', () => { commit.disabled = true; replaceContent(result); });

  container.append(
    pageHead('Import sales', 'Bring in sales from a spreadsheet or your old till system.'),
    h('div', { class: 'grid main-side' },
      h('div', { class: 'stack' },
        card('1. Choose a file', h('div', { class: 'card-body' },
          h('div', { class: 'field' }, h('label', null, 'CSV or Excel (.xlsx) file'), fileInput),
          h('label', { class: 'check', style: { marginBottom: '14px' } }, stockBox, 'Also reduce current stock by the quantities in this file'),
          h('div', { class: 'toolbar', style: { border: 0, padding: 0 } }, preview, commit))),
        result),
      card('What the file needs', h('div', { class: 'card-body' },
        h('p', { style: { marginTop: 0 } }, 'One row per item sold, with a header row. Recognised columns:'),
        h('ul', { class: 'small', style: { paddingLeft: '18px' } },
          h('li', null, h('strong', null, 'Date'), ' (e.g. 2026-03-14)'),
          h('li', null, h('strong', null, 'Product'), ' name or ', h('strong', null, 'SKU'), ' — must match a product you already added'),
          h('li', null, h('strong', null, 'Quantity')),
          h('li', null, h('strong', null, 'Unit price'), ' (optional)'),
          h('li', null, h('strong', null, 'Reference'), ' / receipt number (optional — stops the same sale being imported twice)')),
        h('p', { class: 'muted small' }, 'Leave the stock box ticked off for old, historical sales — your stock count already reflects them. Tick it only for sales that have not yet come off your shelf count.')))));
}
