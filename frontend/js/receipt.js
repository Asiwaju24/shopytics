// Receipt modal (printable). Used after checkout and from the sales history.
import { h, openModal, money, fmtDateTime, icon } from './ui.js';
import { state } from './state.js';

export function showReceipt(sale) {
  const store = state.store;
  const row = (label, value, strong) => h('div', { class: 'row', style: strong ? { fontWeight: 700 } : null }, h('span', null, label), h('span', null, value));
  const body = h('div', { class: 'receipt receipt-print' },
    h('div', { style: { textAlign: 'center' } },
      h('div', { style: { fontWeight: 700, fontSize: '15px' } }, store.name),
      store.address ? h('div', null, store.address) : null,
      h('div', null, [store.city, store.whatsapp_number].filter(Boolean).join(' · '))),
    h('hr'),
    row('Receipt', sale.receipt_number),
    row('Date', fmtDateTime(sale.sold_at)),
    sale.cashier_name ? row('Served by', sale.cashier_name) : null,
    sale.customer_name ? row('Customer', sale.customer_name) : null,
    sale.status === 'VOIDED' ? h('div', { style: { fontWeight: 700, color: '#c62828' } }, 'VOIDED') : null,
    h('hr'),
    sale.lines.map((l) => h('div', { style: { marginBottom: '4px' } },
      h('div', null, l.product_name || 'Item'),
      row(`  ${l.quantity_sold} × ${money(l.unit_price)}`, money(l.total_amount)))),
    h('hr'),
    row('Subtotal', money(sale.subtotal)),
    Number(sale.tax_amount) > 0 ? row(`Tax (${Number(store.tax_rate_percent)}%)`, money(sale.tax_amount)) : null,
    row('TOTAL', money(sale.total_amount), true),
    row(`Paid (${sale.payment_method_label})`, money(sale.amount_paid)),
    Number(sale.balance_due) > 0 ? row('Balance owed', money(sale.balance_due), true) : null,
    h('hr'),
    h('div', { style: { textAlign: 'center' } }, store.receipt_footer || ''),
  );
  const modal = openModal({
    title: `Receipt ${sale.receipt_number}`,
    body,
    footer: [
      h('button', { class: 'btn', onclick: () => modal.close() }, 'Close'),
      h('button', { class: 'btn btn-primary', onclick: () => window.print() }, icon('printer'), 'Print'),
    ],
  });
  return modal;
}
