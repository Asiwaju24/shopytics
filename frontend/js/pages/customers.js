import { Api } from '../api.js';
import { h, money, num, badge, pageHead, formModal, confirmDialog, fmtDate, toast, toastError, icon } from '../ui.js';
import { listView } from '../listview.js';
import { can } from '../state.js';

export async function render(container) {
  const manage = can('customers');
  let lv;
  const fields = (c) => [
    { name: 'name', label: 'Name', required: true, full: true, value: c.name },
    { name: 'phone_number', label: 'Phone number', required: true, maxlength: 20, value: c.phone_number ?? '' },
    { name: 'email', label: 'Email (optional)', type: 'email', value: c.email ?? '' },
    { name: 'notes', label: 'Notes (optional)', type: 'textarea', full: true, value: c.notes ?? '' },
  ];
  const payload = (v) => ({ name: v.name.trim(), phone_number: v.phone_number.trim(), email: v.email.trim(), notes: v.notes.trim() });
  const add = () => formModal({ title: 'Add customer', fields: fields({}), submitText: 'Add customer', onSubmit: async (v) => { await Api.post('/customers/', payload(v)); toast('Customer added', 'success'); lv.reload(); } });
  const edit = (c) => formModal({ title: `Edit ${c.name}`, fields: fields(c), onSubmit: async (v) => { await Api.patch(`/customers/${c.id}/`, payload(v)); toast('Customer updated', 'success'); lv.reload(); } });
  const remove = async (c) => {
    if (!(await confirmDialog({ title: 'Remove customer?', message: `"${c.name}" will be removed from your list. Their past sales are kept.`, confirmText: 'Remove', danger: true }))) return;
    try { await Api.del(`/customers/${c.id}/`); toast('Customer removed', 'success'); lv.reload(); } catch (e) { toastError(e); }
  };
  lv = listView({
    path: '/customers/', searchPlaceholder: 'Search name or phone…',
    columns: [
      { label: 'Customer', render: (c) => h('div', null, h('div', { class: 'title' }, c.name), h('div', { class: 'muted small' }, c.phone_number)) },
      { label: 'Orders', align: 'right', render: (c) => num(c.total_orders) },
      { label: 'Total spent', align: 'right', render: (c) => money(c.total_lifetime_value) },
      { label: 'Owes', align: 'right', render: (c) => (Number(c.outstanding_debt) > 0 ? badge(money(c.outstanding_debt), 'warning') : h('span', { class: 'muted' }, '—')) },
      { label: 'Last purchase', render: (c) => (c.last_purchase_at ? fmtDate(c.last_purchase_at.slice(0, 10)) : '—') },
      { label: '', align: 'right', render: (c) => (manage ? h('div', { class: 'row-actions' },
        h('button', { class: 'btn btn-sm', onclick: () => edit(c) }, icon('edit')),
        h('button', { class: 'btn btn-sm btn-ghost', 'aria-label': `Remove ${c.name}`, onclick: () => remove(c) }, icon('trash'))) : '') },
    ],
  });
  container.append(pageHead('Customers', 'Who buys from you, and who owes you.', manage ? h('button', { class: 'btn btn-primary', onclick: add }, icon('plus'), 'Add customer') : null), lv.el);
}
