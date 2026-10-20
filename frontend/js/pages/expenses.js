import { Api } from '../api.js';
import { h, money, fmtDate, badge, pageHead, formModal, confirmDialog, statCard, toast, toastError, opt, icon, isoDate } from '../ui.js';
import { listView } from '../listview.js';

const CATEGORIES = [
  ['RENT', 'Rent'], ['SALARIES', 'Salaries & wages'], ['UTILITIES', 'Utilities (power, water, data)'], ['TRANSPORT', 'Transport & delivery'],
  ['MARKETING', 'Marketing'], ['SUPPLIES', 'Supplies & packaging'], ['MAINTENANCE', 'Repairs & maintenance'], ['TAXES', 'Taxes & licences'], ['OTHER', 'Other'],
];

export async function render(container) {
  let lv;
  const monthBox = h('div', { class: 'grid cols-3', style: { marginBottom: '16px' } });

  async function loadMonth() {
    const now = new Date();
    const first = isoDate(new Date(now.getFullYear(), now.getMonth(), 1));
    try {
      const s = await Api.get('/reports/summary/', { date_from: first, date_to: isoDate(now) });
      monthBox.replaceChildren(
        statCard('Spent this month', money(s.expenses_total)),
        statCard('Gross profit this month', money(s.gross_profit)),
        statCard('Profit after expenses', money(s.net_profit), 'Gross profit minus expenses'));
    } catch (e) { monthBox.replaceChildren(); }
  }

  const fields = (x) => [
    { name: 'description', label: 'What was it for?', required: true, full: true, maxlength: 255, value: x.description },
    { name: 'category', label: 'Category', type: 'select', value: x.category || 'OTHER', options: CATEGORIES.map(([v, l]) => opt(v, l)) },
    { name: 'amount', label: 'Amount', type: 'number', required: true, min: '0.01', step: '0.01', value: x.amount ?? '' },
    { name: 'expense_date', label: 'Date', type: 'date', required: true, value: x.expense_date || isoDate() },
    { name: 'payment_method', label: 'Paid by', type: 'select', value: x.payment_method || 'CASH', options: [opt('CASH', 'Cash'), opt('TRANSFER', 'Bank transfer'), opt('CARD', 'Card / POS')] },
  ];
  const payload = (v) => ({ description: v.description.trim(), category: v.category, amount: Number(v.amount).toFixed(2), expense_date: v.expense_date, payment_method: v.payment_method });
  const refresh = () => { lv.reload(); loadMonth(); };
  const add = () => formModal({ title: 'Add expense', fields: fields({}), submitText: 'Add expense', onSubmit: async (v) => { await Api.post('/expenses/', payload(v)); toast('Expense added', 'success'); refresh(); } });
  const edit = (x) => formModal({ title: 'Edit expense', fields: fields(x), onSubmit: async (v) => { await Api.patch(`/expenses/${x.id}/`, payload(v)); toast('Expense updated', 'success'); refresh(); } });
  const remove = async (x) => {
    if (!(await confirmDialog({ title: 'Delete expense?', message: `"${x.description}" (${money(x.amount)}) will be removed.`, confirmText: 'Delete', danger: true }))) return;
    try { await Api.del(`/expenses/${x.id}/`); toast('Expense deleted', 'success'); refresh(); } catch (e) { toastError(e); }
  };

  lv = listView({
    path: '/expenses/', searchPlaceholder: 'Search description…',
    filters: [{ name: 'category', label: 'Category', options: [opt('', 'All categories'), ...CATEGORIES.map(([v, l]) => opt(v, l))] }],
    columns: [
      { label: 'Date', render: (x) => fmtDate(x.expense_date), class: 'nowrap' },
      { label: 'Description', render: (x) => x.description },
      { label: 'Category', render: (x) => badge(x.category_label) },
      { label: 'Amount', align: 'right', render: (x) => h('strong', null, money(x.amount)) },
      { label: 'Recorded by', render: (x) => x.recorded_by_name || '—' },
      { label: '', align: 'right', render: (x) => h('div', { class: 'row-actions' },
        h('button', { class: 'btn btn-sm', onclick: () => edit(x) }, icon('edit')),
        h('button', { class: 'btn btn-sm btn-ghost', 'aria-label': 'Delete expense', onclick: () => remove(x) }, icon('trash'))) },
    ],
  });

  container.append(pageHead('Expenses', 'Rent, wages, power and everything else you spend on the business.', h('button', { class: 'btn btn-primary', onclick: add }, icon('plus'), 'Add expense')), monthBox, lv.el);
  loadMonth();
}
