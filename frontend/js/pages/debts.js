import { Api } from '../api.js';
import {
  h, money, fmtDate, fmtDateTime, badge, pageHead, formModal, openModal, table, toast, toastError,
  opt, icon, isoDate, daysAgo, spinner, errorBox, replaceContent, empty,
} from '../ui.js';
import { listView } from '../listview.js';
import { can } from '../state.js';

export async function render(container) {
  const manage = can('debts');
  const canPay = can('recordPayment');
  let lv;

  const recordPayment = (d) => formModal({
    title: `Record payment — ${d.customer_name}`,
    intro: h('p', { class: 'muted', style: { marginTop: 0 } }, `Outstanding balance: ${money(d.outstanding_amount)}`),
    fields: [
      { name: 'amount', label: 'Amount received', type: 'number', required: true, min: '0.01', step: '0.01', value: String(d.outstanding_amount), full: true },
      { name: 'method', label: 'Paid by', type: 'select', value: 'CASH', options: [opt('CASH', 'Cash'), opt('TRANSFER', 'Bank transfer'), opt('CARD', 'Card / POS'), opt('OTHER', 'Other')], full: true },
      { name: 'note', label: 'Note (optional)', maxlength: 255, full: true },
    ],
    submitText: 'Record payment',
    onSubmit: async (v) => {
      const res = await Api.post(`/debts/${d.id}/record-payment/`, { amount: Number(v.amount).toFixed(2), method: v.method, note: v.note.trim() });
      toast(res.debt.is_settled ? 'Debt fully paid' : 'Payment recorded', 'success');
      lv.reload();
    },
  });

  async function history(d) {
    const body = h('div', null, spinner());
    openModal({ title: `Payments — ${d.customer_name}`, body });
    try {
      const rows = await Api.get(`/debts/${d.id}/payments/`);
      replaceContent(body, table([
        { label: 'Date', render: (p) => fmtDateTime(p.paid_at) },
        { label: 'Amount', align: 'right', render: (p) => money(p.amount) },
        { label: 'Method', render: (p) => p.method },
        { label: 'Received by', render: (p) => p.received_by_name || '—' },
        { label: 'Note', render: (p) => p.note },
      ], rows, empty('No payments yet')));
    } catch (e) { replaceContent(body, errorBox(e.message)); }
  }

  // Reminders: we prepare the message + WhatsApp link; the person opens WhatsApp and presses send.
  async function remind(ids, tone = 'friendly') {
    try {
      const res = await Api.post('/debts/send-reminders/', { debt_ids: ids, tone });
      if (!res.reminders.length) { toast('Nothing to remind — those debts are already settled.', 'error'); return; }
      const modal = openModal({
        title: `Reminders ready (${res.reminders.length})`, wide: true,
        body: h('div', null,
          h('p', { class: 'muted', style: { marginTop: 0 } }, 'Nothing is sent automatically. Open each chat in WhatsApp and press send.'),
          table([
            { label: 'Customer', render: (r) => h('div', null, h('div', { class: 'title' }, r.customer_name), h('div', { class: 'muted small' }, r.phone_number)) },
            { label: 'Owes', align: 'right', render: (r) => money(r.outstanding_amount) },
            { label: '', align: 'right', render: (r) => (r.whatsapp_url
              ? h('a', { class: 'btn btn-sm btn-primary', href: r.whatsapp_url, target: '_blank', rel: 'noopener', style: { textDecoration: 'none' } }, icon('message'), 'Open WhatsApp')
              : badge('No valid number', 'danger')) },
          ], res.reminders)),
        onClose: () => lv.reload(),
      });
    } catch (e) { toastError(e); }
  }

  const remindAllOverdue = async () => {
    try {
      const overdue = await Api.all('/debts/', { overdue: 'true' });
      if (!overdue.length) { toast('No overdue debts — nice.', 'success'); return; }
      await remind(overdue.map((d) => d.id), 'firm');
    } catch (e) { toastError(e); }
  };

  const addDebt = async () => {
    let customers;
    try { customers = await Api.all('/customers/', { ordering: 'name' }); } catch (e) { toastError(e); return; }
    if (!customers.length) { toast('Add a customer first.', 'error'); return; }
    const due = new Date(); due.setDate(due.getDate() + 14);
    formModal({
      title: 'Record a debt', submitText: 'Save debt',
      intro: h('p', { class: 'muted', style: { marginTop: 0 } }, 'For money a customer owes from before — sales on credit are added automatically at the till.'),
      fields: [
        { name: 'customer', label: 'Customer', type: 'select', required: true, full: true, options: customers.map((c) => opt(c.id, `${c.name} (${c.phone_number})`)) },
        { name: 'amount', label: 'Amount owed', type: 'number', required: true, min: '0.01', step: '0.01' },
        { name: 'due_date', label: 'Due date', type: 'date', required: true, value: isoDate(due) },
        { name: 'notes', label: 'What is it for? (optional)', full: true },
      ],
      onSubmit: async (v) => { await Api.post('/debts/', { customer: v.customer, amount: Number(v.amount).toFixed(2), due_date: v.due_date, notes: v.notes.trim() }); toast('Debt recorded', 'success'); lv.reload(); },
    });
  };

  lv = listView({
    path: '/debts/', searchPlaceholder: 'Search customer…',
    filters: [{
      name: 'view', label: 'Show', value: 'open',
      options: [opt('open', 'Unpaid'), opt('overdue', 'Overdue only'), opt('settled', 'Paid'), opt('all', 'All')],
      map: (v) => (v === 'open' ? { settled: 'false' } : v === 'overdue' ? { overdue: 'true' } : v === 'settled' ? { settled: 'true' } : {}),
    }],
    columns: [
      { label: 'Customer', render: (d) => h('div', null, h('div', { class: 'title' }, d.customer_name), h('div', { class: 'muted small' }, d.customer_phone)) },
      { label: 'For', render: (d) => d.sale_receipt || h('span', { class: 'muted' }, 'Manual debt') },
      { label: 'Amount', align: 'right', render: (d) => money(d.amount) },
      { label: 'Paid', align: 'right', render: (d) => money(d.amount_paid) },
      { label: 'Balance', align: 'right', render: (d) => h('strong', null, money(d.outstanding_amount)) },
      { label: 'Due', render: (d) => (d.is_settled ? badge('Paid', 'success') : d.days_overdue > 0 ? badge(`${d.days_overdue} days overdue`, 'danger') : fmtDate(d.due_date)), class: 'nowrap' },
      { label: 'Reminders', align: 'right', render: (d) => d.reminder_count },
      { label: '', align: 'right', render: (d) => h('div', { class: 'row-actions' },
        d.is_settled ? null : [
          canPay ? h('button', { class: 'btn btn-sm btn-primary', onclick: () => recordPayment(d) }, 'Record payment') : null,
          manage ? h('button', { class: 'btn btn-sm', title: 'Prepare a WhatsApp reminder', onclick: () => remind([d.id], d.days_overdue > 0 ? 'firm' : 'friendly') }, icon('message')) : null,
        ],
        parseFloat(d.amount_paid) > 0 ? h('button', { class: 'btn btn-sm btn-ghost', onclick: () => history(d) }, 'History') : null) },
    ],
  });

  container.append(
    pageHead('Debts', 'Money customers owe you, with repayments and reminders.',
      manage ? h('button', { class: 'btn', onclick: remindAllOverdue }, icon('message'), 'Remind all overdue') : null,
      manage ? h('button', { class: 'btn btn-primary', onclick: addDebt }, icon('plus'), 'Record a debt') : null),
    lv.el);
}
