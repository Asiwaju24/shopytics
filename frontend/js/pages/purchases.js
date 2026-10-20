import { Api } from '../api.js';
import {
  h, money, num, fmtDate, badge, pageHead, formModal, openModal, confirmDialog, table, toast, toastError,
  opt, icon, spinner, errorBox, replaceContent, withBusy,
} from '../ui.js';
import { listView } from '../listview.js';
import { state, can } from '../state.js';

const STATUS = {
  DRAFT: ['Draft', ''], SENT: ['Sent', 'info'], CONFIRMED: ['Confirmed', 'info'],
  PARTIAL: ['Partly received', 'warning'], DELIVERED: ['Delivered', 'success'], CANCELLED: ['Cancelled', 'danger'],
};

export async function render(container, ctx) {
  const manage = can('purchases');
  let lv;

  // ── Create ───────────────────────────────────────────────
  async function newOrder(prefillProductId) {
    let suppliers, products;
    try { [suppliers, products] = await Promise.all([Api.all('/suppliers/'), Api.all('/products/', { ordering: 'name' })]); } catch (e) { toastError(e); return; }
    if (!suppliers.length) { toast('Add a supplier first (Inventory → Suppliers).', 'error'); return; }
    if (!products.length) { toast('Add some products first.', 'error'); return; }

    const supplierSelect = h('select', { class: 'select', 'aria-label': 'Supplier' }, suppliers.map((s) => h('option', { value: s.id }, s.name)));
    const lines = [];
    const linesBox = h('div');
    const totalEl = h('strong');

    const recalc = () => {
      const total = lines.reduce((sum, l) => sum + (Number(l.qty.value) || 0) * (Number(l.cost.value) || 0), 0);
      totalEl.textContent = money(total);
    };
    const addLine = (productId, qty) => {
      const product = products.find((p) => p.id === productId) || products[0];
      const productSelect = h('select', { class: 'select', 'aria-label': 'Product' }, products.map((p) => h('option', { value: p.id, selected: p.id === product.id }, `${p.name} (${p.current_stock} in stock)`)));
      const qtyInput = h('input', { class: 'input', type: 'number', min: '1', step: '1', value: String(qty || 1), 'aria-label': 'Quantity', style: { width: '90px' } });
      const costInput = h('input', { class: 'input', type: 'number', min: '0', step: '0.01', value: String(product.cost_price), 'aria-label': 'Unit cost', style: { width: '110px' } });
      const row = h('div', { style: { display: 'flex', gap: '8px', marginBottom: '8px', alignItems: 'center' } }, productSelect, qtyInput, costInput,
        h('button', { class: 'btn btn-sm btn-ghost', type: 'button', 'aria-label': 'Remove line', onclick: () => { lines.splice(lines.indexOf(entry), 1); row.remove(); recalc(); } }, icon('trash')));
      const entry = { product: productSelect, qty: qtyInput, cost: costInput };
      productSelect.addEventListener('change', () => { costInput.value = String(products.find((p) => p.id === productSelect.value).cost_price); recalc(); });
      qtyInput.addEventListener('input', recalc);
      costInput.addEventListener('input', recalc);
      lines.push(entry);
      linesBox.append(row);
      recalc();
    };

    let prefill = prefillProductId ? products.find((p) => p.id === prefillProductId) : null;
    if (prefill) {
      const link = (prefill.supplier_links || []).find((l) => l.is_preferred) || (prefill.supplier_links || [])[0];
      if (link && suppliers.some((s) => s.id === link.supplier)) supplierSelect.value = link.supplier;
      addLine(prefill.id, Math.max(1, prefill.reorder_point * 2 - prefill.current_stock));
    } else {
      addLine();
    }

    const errorEl = h('div', { class: 'form-error', hidden: true });
    const save = h('button', { class: 'btn btn-primary' }, 'Create order');
    const modal = openModal({
      title: 'New purchase order', wide: true,
      body: h('div', null, errorEl,
        h('div', { class: 'field' }, h('label', null, 'Supplier'), supplierSelect),
        h('div', { class: 'field' }, h('label', null, 'Items'),
          h('div', { class: 'muted small', style: { marginBottom: '6px' } }, 'Product · quantity · unit cost'), linesBox,
          h('button', { class: 'btn btn-sm', type: 'button', onclick: () => addLine() }, icon('plus'), 'Add item')),
        h('div', { style: { textAlign: 'right' } }, 'Order total: ', totalEl)),
      footer: [h('button', { class: 'btn', onclick: () => modal.close() }, 'Cancel'), save],
    });
    save.addEventListener('click', async () => {
      errorEl.hidden = true;
      const items = lines.map((l) => ({ product: l.product.value, quantity_ordered: parseInt(l.qty.value, 10), unit_cost: Number(l.cost.value).toFixed(2) }));
      if (items.some((i) => !(i.quantity_ordered >= 1) || Number.isNaN(Number(i.unit_cost)))) { errorEl.textContent = 'Every item needs a quantity of at least 1 and a cost.'; errorEl.hidden = false; return; }
      const supplier = suppliers.find((s) => s.id === supplierSelect.value);
      const message = `Hello ${supplier.name},\nPlease supply the following for ${state.store.name}:\n` +
        items.map((i) => `- ${products.find((p) => p.id === i.product).name}: ${i.quantity_ordered}`).join('\n') + '\nThank you.';
      await withBusy(save, async () => {
        try {
          await Api.post('/purchase-orders/', { supplier: supplier.id, items, whatsapp_message: message });
          modal.close(); toast('Purchase order created', 'success'); lv.reload();
        } catch (e) { errorEl.textContent = e.message; errorEl.hidden = false; }
      });
    });
  }

  // ── Actions ──────────────────────────────────────────────
  const sendWhatsApp = async (po) => {
    if (!po.whatsapp_url) { toast('This supplier has no valid phone number to message.', 'error'); return; }
    window.open(po.whatsapp_url, '_blank', 'noopener');   // inside the click so popup blockers allow it
    try { await Api.post(`/purchase-orders/${po.id}/send/`); toast('Marked as sent', 'success'); lv.reload(); } catch (e) { toastError(e); }
  };

  const confirmOrder = (po) => formModal({
    title: 'Supplier confirmed', submitText: 'Mark confirmed',
    fields: [{ name: 'expected_delivery', label: 'Expected delivery (optional)', type: 'date', full: true, hint: 'Leave blank to use the supplier’s usual lead time.' }],
    onSubmit: async (v) => { await Api.post(`/purchase-orders/${po.id}/confirm/`, v.expected_delivery ? { expected_delivery: v.expected_delivery } : {}); toast('Order confirmed', 'success'); lv.reload(); },
  });

  const receive = (po) => {
    const pending = po.items.filter((i) => i.quantity_ordered > i.quantity_received);
    const inputs = pending.map((i) => h('input', { class: 'input', type: 'number', min: '0', max: String(i.quantity_ordered - i.quantity_received), step: '1', value: String(i.quantity_ordered - i.quantity_received), 'aria-label': `Received ${i.product_name}`, style: { width: '90px' } }));
    const costBox = h('input', { type: 'checkbox' });
    const errorEl = h('div', { class: 'form-error', hidden: true });
    const save = h('button', { class: 'btn btn-primary' }, 'Add to stock');
    const modal = openModal({
      title: `Receive goods — ${po.supplier_name}`, wide: true,
      body: h('div', null, errorEl,
        h('p', { class: 'muted', style: { marginTop: 0 } }, 'Enter how many units actually arrived. They are added to your stock straight away.'),
        table([
          { label: 'Product', render: (i) => i.product_name },
          { label: 'Ordered', align: 'right', render: (i) => num(i.quantity_ordered) },
          { label: 'Already received', align: 'right', render: (i) => num(i.quantity_received) },
          { label: 'Receiving now', align: 'right', render: (i) => inputs[pending.indexOf(i)] },
        ], pending),
        h('label', { class: 'check', style: { marginTop: '12px' } }, costBox, 'Update product cost prices from this order')),
      footer: [h('button', { class: 'btn', onclick: () => modal.close() }, 'Cancel'), save],
    });
    save.addEventListener('click', async () => {
      errorEl.hidden = true;
      const items = pending.map((i, idx) => ({ item_id: i.id, quantity: parseInt(inputs[idx].value || '0', 10) || 0 }));
      await withBusy(save, async () => {
        try {
          await Api.post(`/purchase-orders/${po.id}/receive/`, { items, update_cost_price: costBox.checked });
          modal.close(); toast('Stock updated', 'success'); lv.reload();
        } catch (e) { errorEl.textContent = e.message; errorEl.hidden = false; }
      });
    });
  };

  const view = (po) => openModal({
    title: `Order to ${po.supplier_name}`, wide: true,
    body: h('div', null,
      h('p', { class: 'muted', style: { marginTop: 0 } }, `${STATUS[po.status][0]} · created ${fmtDate(po.created_at)}${po.expected_delivery ? ` · expected ${fmtDate(po.expected_delivery)}` : ''}`),
      table([
        { label: 'Product', render: (i) => i.product_name },
        { label: 'Ordered', align: 'right', render: (i) => num(i.quantity_ordered) },
        { label: 'Received', align: 'right', render: (i) => num(i.quantity_received) },
        { label: 'Unit cost', align: 'right', render: (i) => money(i.unit_cost) },
        { label: 'Line total', align: 'right', render: (i) => money(i.line_total) },
      ], po.items),
      h('div', { style: { textAlign: 'right', marginTop: '10px' } }, 'Total: ', h('strong', null, money(po.total_cost)))),
  });

  const cancelOrder = async (po) => {
    if (!(await confirmDialog({ title: 'Cancel this order?', message: 'Nothing has been received yet, so your stock is not affected.', confirmText: 'Cancel order', danger: true }))) return;
    try { await Api.post(`/purchase-orders/${po.id}/cancel/`); toast('Order cancelled', 'success'); lv.reload(); } catch (e) { toastError(e); }
  };
  const deleteOrder = async (po) => {
    if (!(await confirmDialog({ title: 'Delete this order?', message: 'This removes it permanently.', confirmText: 'Delete', danger: true }))) return;
    try { await Api.del(`/purchase-orders/${po.id}/`); toast('Order deleted', 'success'); lv.reload(); } catch (e) { toastError(e); }
  };

  lv = listView({
    path: '/purchase-orders/', searchPlaceholder: 'Search by supplier…',
    filters: [{ name: 'status', label: 'Status', options: [opt('', 'All orders'), ...Object.entries(STATUS).map(([v, [label]]) => opt(v, label))] }],
    columns: [
      { label: 'Supplier', render: (po) => h('a', { onclick: () => view(po) }, po.supplier_name) },
      { label: 'Created', render: (po) => fmtDate(po.created_at), class: 'nowrap' },
      { label: 'Items', align: 'right', render: (po) => po.items.length },
      { label: 'Total', align: 'right', render: (po) => money(po.total_cost) },
      { label: 'Status', render: (po) => badge(STATUS[po.status][0], STATUS[po.status][1]) },
      { label: 'Expected', render: (po) => (po.expected_delivery ? fmtDate(po.expected_delivery) : '—') },
      { label: '', align: 'right', render: (po) => !manage ? '' : h('div', { class: 'row-actions' },
        po.status === 'DRAFT' ? h('button', { class: 'btn btn-sm btn-primary', onclick: () => sendWhatsApp(po) }, icon('message'), 'Send') : null,
        po.status === 'SENT' ? h('button', { class: 'btn btn-sm', onclick: () => confirmOrder(po) }, 'Confirm') : null,
        ['DRAFT', 'SENT', 'CONFIRMED', 'PARTIAL'].includes(po.status) ? h('button', { class: 'btn btn-sm', onclick: () => receive(po) }, 'Receive') : null,
        ['DRAFT', 'SENT', 'CONFIRMED'].includes(po.status) ? h('button', { class: 'btn btn-sm btn-ghost', onclick: () => cancelOrder(po) }, 'Cancel') : null,
        ['DRAFT', 'CANCELLED'].includes(po.status) ? h('button', { class: 'btn btn-sm btn-ghost', 'aria-label': 'Delete order', onclick: () => deleteOrder(po) }, icon('trash')) : null) },
    ],
  });

  container.append(
    pageHead('Purchase orders', 'Order stock from suppliers and book it in when it arrives.',
      manage ? h('button', { class: 'btn btn-primary', onclick: () => newOrder() }, icon('plus'), 'New order') : null),
    lv.el);

  if (manage && ctx.query.get('product')) newOrder(ctx.query.get('product'));
}
