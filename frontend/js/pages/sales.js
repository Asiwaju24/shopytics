import { Api } from '../api.js';
import { h, money, fmtDateTime, badge, pageHead, formModal, confirmDialog, toast, toastError, opt, icon, daysAgo, isoDate } from '../ui.js';
import { listView } from '../listview.js';
import { can } from '../state.js';
import { showReceipt } from '../receipt.js';

export async function render(container) {
  const from = h('input', { class: 'input', type: 'date', 'aria-label': 'From date', value: daysAgo(29) });
  const to = h('input', { class: 'input', type: 'date', 'aria-label': 'To date', value: isoDate() });
  let lv;

  const voidSale = (sale) => formModal({
    title: `Void ${sale.receipt_number}?`,
    intro: h('p', { class: 'muted', style: { marginTop: 0 } }, 'The items go back into stock and the sale is removed from your revenue and profit. This cannot be undone.'),
    fields: [{ name: 'reason', label: 'Reason', full: true, maxlength: 255, placeholder: 'e.g. Wrong item rung up' }],
    submitText: 'Void sale',
    onSubmit: async (v) => {
      await Api.post(`/sales/${sale.id}/void/`, { reason: v.reason.trim() });
      toast(`${sale.receipt_number} voided`, 'success');
      lv.reload();
    },
  });

  lv = listView({
    path: '/sales/',
    searchPlaceholder: 'Search receipt number or customer…',
    params: () => ({ date_from: from.value, date_to: to.value }),
    filters: [
      { name: 'status', label: 'Status', options: [opt('', 'All statuses'), opt('COMPLETED', 'Completed'), opt('VOIDED', 'Voided')] },
      { name: 'payment_method', label: 'Payment', options: [opt('', 'All payments'), opt('CASH', 'Cash'), opt('TRANSFER', 'Transfer'), opt('CARD', 'Card / POS'), opt('CREDIT', 'On credit')] },
    ],
    toolbarExtra: [h('span', { class: 'muted small' }, 'From'), from, h('span', { class: 'muted small' }, 'to'), to],
    columns: [
      { label: 'Receipt', render: (s) => h('a', { onclick: () => showReceipt(s) }, s.receipt_number) },
      { label: 'Date', render: (s) => fmtDateTime(s.sold_at), class: 'nowrap' },
      { label: 'Customer', render: (s) => s.customer_name || h('span', { class: 'muted' }, 'Walk-in') },
      { label: 'Items', align: 'right', render: (s) => s.item_count },
      { label: 'Payment', render: (s) => s.payment_method_label },
      { label: 'Total', align: 'right', render: (s) => money(s.total_amount) },
      { label: 'Status', render: (s) => (s.status === 'VOIDED' ? badge('Voided', 'danger') : Number(s.balance_due) > 0 ? badge(`Owes ${money(s.balance_due)}`, 'warning') : badge('Paid', 'success')) },
      { label: '', align: 'right', render: (s) => h('div', { class: 'row-actions' },
        h('button', { class: 'btn btn-sm', onclick: () => showReceipt(s) }, 'View'),
        can('void') && s.status === 'COMPLETED' ? h('button', { class: 'btn btn-sm btn-ghost', onclick: () => voidSale(s) }, 'Void') : null) },
    ],
  });
  from.addEventListener('change', () => lv.reload());
  to.addEventListener('change', () => lv.reload());

  container.append(
    pageHead('Sales history', 'Every receipt, with the option to void a mistake.',
      can('sell') ? h('a', { class: 'btn btn-primary', href: '#/pos', style: { textDecoration: 'none' } }, icon('plus'), 'New sale') : null),
    lv.el);
}
