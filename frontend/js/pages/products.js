import { Api } from '../api.js';
import { h, money, num, badge, pageHead, formModal, confirmDialog, toast, toastError, opt, icon } from '../ui.js';
import { listView } from '../listview.js';
import { can } from '../state.js';

const tierBadge = (t) => (t === 'critical' ? badge('Critical', 'danger') : t === 'low' ? badge('Low', 'warning') : badge('In stock', 'success'));

export async function render(container, ctx) {
  const manage = can('products');
  let lv;

  const productFields = (p, creating) => [
    { name: 'name', label: 'Product name', required: true, full: true, value: p.name },
    { name: 'sku', label: 'SKU / barcode', required: true, value: p.sku, maxlength: 80, hint: 'Scan or type the barcode here to sell by scanning.' },
    { name: 'category', label: 'Category', value: p.category ?? 'General' },
    { name: 'cost_price', label: 'Cost price', type: 'number', required: true, min: '0', step: '0.01', value: p.cost_price ?? '' },
    { name: 'selling_price', label: 'Selling price', type: 'number', required: true, min: '0', step: '0.01', value: p.selling_price ?? '' },
    { name: 'current_stock', label: creating ? 'Opening stock' : 'Stock on hand', type: 'number', min: '0', step: '1', value: p.current_stock ?? 0, hint: creating ? '' : 'Changing this is recorded as a stock count correction.' },
    { name: 'reorder_point', label: 'Reorder level', type: 'number', min: '0', step: '1', value: p.reorder_point ?? 5, hint: 'Warn me when stock falls to this.' },
    { name: 'unit_of_measure', label: 'Unit', value: p.unit_of_measure ?? 'unit', maxlength: 20 },
    { name: 'description', label: 'Description (optional)', type: 'textarea', full: true, value: p.description ?? '' },
  ];

  const toPayload = (v) => ({
    name: v.name.trim(), sku: v.sku.trim(), category: v.category.trim() || 'General',
    cost_price: v.cost_price, selling_price: v.selling_price,
    current_stock: parseInt(v.current_stock || '0', 10), reorder_point: parseInt(v.reorder_point || '0', 10),
    unit_of_measure: v.unit_of_measure.trim() || 'unit', description: v.description.trim(),
  });

  const addProduct = () => formModal({
    title: 'Add product', wide: true, submitText: 'Add product', fields: productFields({}, true),
    onSubmit: async (v) => { await Api.post('/products/', toPayload(v)); toast('Product added', 'success'); lv.reload(); },
  });

  const editProduct = (p) => formModal({
    title: `Edit ${p.name}`, wide: true, fields: productFields(p, false),
    onSubmit: async (v) => { await Api.patch(`/products/${p.id}/`, toPayload(v)); toast('Product updated', 'success'); lv.reload(); },
  });

  const adjust = (p) => formModal({
    title: `Adjust stock — ${p.name}`,
    intro: h('p', { class: 'muted', style: { marginTop: 0 } }, `Currently ${num(p.current_stock)} in stock. Every change is recorded in Stock History.`),
    fields: [
      { name: 'mode', label: 'What do you want to do?', type: 'select', full: true, value: 'delta', options: [opt('delta', 'Add or remove units (use − to remove)'), opt('set', 'Set to an exact counted number')] },
      { name: 'quantity', label: 'Quantity', type: 'number', required: true, step: '1', full: true },
      { name: 'reason', label: 'Reason', type: 'select', full: true, value: 'ADJUSTMENT', options: [opt('ADJUSTMENT', 'Correction'), opt('COUNT', 'Stock count'), opt('DAMAGE', 'Damaged (enter units lost)'), opt('EXPIRED', 'Expired (enter units lost)')] },
      { name: 'note', label: 'Note (optional)', full: true, maxlength: 255 },
    ],
    submitText: 'Save adjustment',
    onSubmit: async (v) => {
      let quantity = parseInt(v.quantity, 10);
      // "2 damaged" means 2 units leave stock — don't make people type a minus sign.
      if (v.mode === 'delta' && (v.reason === 'DAMAGE' || v.reason === 'EXPIRED')) quantity = -Math.abs(quantity);
      await Api.post(`/products/${p.id}/adjust-stock/`, { mode: v.mode, quantity, reason: v.reason, note: v.note.trim() });
      toast('Stock updated', 'success');
      lv.reload();
    },
  });

  const remove = async (p) => {
    if (!(await confirmDialog({ title: 'Delete product?', message: `"${p.name}" will be removed from your catalog. Past sales and history are kept.`, confirmText: 'Delete', danger: true }))) return;
    try { await Api.del(`/products/${p.id}/`); toast('Product deleted', 'success'); lv.reload(); } catch (e) { toastError(e); }
  };

  const columns = [
    { label: 'Product', render: (p) => h('div', null, h('div', { class: 'title' }, p.name), h('div', { class: 'muted small' }, `${p.sku} · ${p.category}`)) },
    { label: 'Price', align: 'right', render: (p) => money(p.selling_price) },
  ];
  if (manage) {
    columns.push({ label: 'Cost', align: 'right', render: (p) => money(p.cost_price) });
    columns.push({ label: 'Margin', align: 'right', render: (p) => `${num(p.margin_percent, 0)}%` });
  }
  columns.push(
    { label: 'In stock', align: 'right', render: (p) => `${num(p.current_stock)}${p.unit_of_measure && p.unit_of_measure !== 'unit' ? ` ${p.unit_of_measure}` : ''}` },
    { label: 'Days left', align: 'right', render: (p) => (p.days_of_stock_remaining === null || p.days_of_stock_remaining === undefined ? '—' : num(p.days_of_stock_remaining, 1)) },
    { label: 'Status', render: (p) => tierBadge(p.stock_tier) },
    { label: '', align: 'right', render: (p) => h('div', { class: 'row-actions' },
      h('button', { class: 'btn btn-sm btn-ghost', title: 'Stock history', onclick: () => ctx.navigate(`stock?product=${p.id}`) }, icon('clock')),
      manage ? h('button', { class: 'btn btn-sm', onclick: () => adjust(p) }, 'Adjust stock') : null,
      manage ? h('button', { class: 'btn btn-sm', onclick: () => editProduct(p) }, icon('edit')) : null,
      manage ? h('button', { class: 'btn btn-sm btn-ghost', 'aria-label': `Delete ${p.name}`, onclick: () => remove(p) }, icon('trash')) : null) },
  );

  lv = listView({
    path: '/products/', searchPlaceholder: 'Search name, SKU or category…',
    filters: [{ name: 'status', label: 'Stock status', options: [opt('', 'All stock levels'), opt('critical', 'Critical'), opt('low', 'Low'), opt('healthy', 'Healthy')] }],
    columns,
  });

  container.append(
    pageHead('Products', 'Your catalog and current stock levels.',
      manage ? h('button', { class: 'btn btn-primary', onclick: addProduct }, icon('plus'), 'Add product') : null),
    lv.el);
}
