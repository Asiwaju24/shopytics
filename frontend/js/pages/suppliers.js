import { Api } from '../api.js';
import { h, num, badge, pageHead, formModal, confirmDialog, toast, toastError, icon } from '../ui.js';
import { listView } from '../listview.js';
import { can } from '../state.js';

export async function render(container) {
  const manage = can('suppliers');
  let lv;
  const fields = (s) => [
    { name: 'name', label: 'Supplier name', required: true, full: true, value: s.name },
    { name: 'contact_person', label: 'Contact person', value: s.contact_person ?? '' },
    { name: 'whatsapp_number', label: 'WhatsApp number', required: true, maxlength: 20, value: s.whatsapp_number ?? '', hint: 'Orders are sent to this number.' },
    { name: 'phone_number', label: 'Phone (optional)', maxlength: 20, value: s.phone_number ?? '' },
    { name: 'email', label: 'Email (optional)', type: 'email', value: s.email ?? '' },
    { name: 'lead_time_days', label: 'Delivery time (days)', type: 'number', min: '0', step: '1', value: s.lead_time_days ?? 3 },
    { name: 'address', label: 'Address (optional)', full: true, value: s.address ?? '' },
  ];
  const payload = (v) => ({
    name: v.name.trim(), contact_person: v.contact_person.trim(), whatsapp_number: v.whatsapp_number.trim(),
    phone_number: v.phone_number.trim(), email: v.email.trim(), address: v.address.trim(),
    lead_time_days: parseInt(v.lead_time_days || '0', 10),
  });
  const add = () => formModal({ title: 'Add supplier', fields: fields({}), submitText: 'Add supplier', onSubmit: async (v) => { await Api.post('/suppliers/', payload(v)); toast('Supplier added', 'success'); lv.reload(); } });
  const edit = (s) => formModal({ title: `Edit ${s.name}`, fields: fields(s), onSubmit: async (v) => { await Api.patch(`/suppliers/${s.id}/`, payload(v)); toast('Supplier updated', 'success'); lv.reload(); } });
  const remove = async (s) => {
    if (!(await confirmDialog({ title: 'Remove supplier?', message: `"${s.name}" will be archived. Their past orders are kept.`, confirmText: 'Remove', danger: true }))) return;
    try { await Api.del(`/suppliers/${s.id}/`); toast('Supplier removed', 'success'); lv.reload(); } catch (e) { toastError(e); }
  };
  lv = listView({
    path: '/suppliers/', searchPlaceholder: 'Search suppliers…',
    columns: [
      { label: 'Supplier', render: (s) => h('div', null, h('div', { class: 'title' }, s.name), s.contact_person ? h('div', { class: 'muted small' }, s.contact_person) : null) },
      { label: 'WhatsApp', render: (s) => s.whatsapp_number },
      { label: 'Delivery', align: 'right', render: (s) => `${num(s.lead_time_days)} days` },
      { label: '', align: 'right', render: (s) => (manage ? h('div', { class: 'row-actions' },
        h('button', { class: 'btn btn-sm', onclick: () => edit(s) }, icon('edit')),
        h('button', { class: 'btn btn-sm btn-ghost', 'aria-label': `Remove ${s.name}`, onclick: () => remove(s) }, icon('trash'))) : '') },
    ],
  });
  container.append(pageHead('Suppliers', 'The people you buy stock from.', manage ? h('button', { class: 'btn btn-primary', onclick: add }, icon('plus'), 'Add supplier') : null), lv.el);
}
