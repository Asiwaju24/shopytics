import { Api } from '../api.js';
import { h, fmtDate, badge, pageHead, formModal, confirmDialog, toast, toastError, opt, icon } from '../ui.js';
import { listView } from '../listview.js';
import { ROLE_LABELS } from '../state.js';

const ASSIGNABLE = [
  opt('MANAGER', 'Manager — products, purchasing, reports'),
  opt('CASHIER', 'Cashier — sells and takes customer payments'),
  opt('ACCOUNTANT', 'Accountant — expenses, debts and reports'),
  opt('VIEWER', 'Viewer — can look, cannot change anything'),
];

export async function render(container) {
  let lv;

  const addMember = () => formModal({
    title: 'Add a team member', wide: true, submitText: 'Add to team',
    intro: h('p', { class: 'muted', style: { marginTop: 0 } }, 'If the username already has an account it is simply added to your shop. Otherwise a new account is created with the password you set — share it with them and ask them to change it.'),
    fields: [
      { name: 'username', label: 'Username', required: true, full: true, maxlength: 150 },
      { name: 'role', label: 'Role', type: 'select', value: 'CASHIER', options: ASSIGNABLE, full: true },
      { name: 'full_name', label: 'Full name (new accounts)' },
      { name: 'phone_number', label: 'Phone (new accounts)', maxlength: 20 },
      { name: 'email', label: 'Email (new accounts)', type: 'email' },
      { name: 'password', label: 'Password (new accounts only)', type: 'password', hint: 'At least 8 characters, not too common.' },
    ],
    onSubmit: async (v) => {
      const res = await Api.post('/memberships/add-staff/', {
        username: v.username.trim(), role: v.role, full_name: v.full_name.trim(),
        phone_number: v.phone_number.trim(), email: v.email.trim(), password: v.password,
      });
      toast(res.account_created ? 'Account created and added to your team' : 'Added to your team', 'success');
      lv.reload();
    },
  });

  const changeRole = (m) => formModal({
    title: `Change role — ${m.user_name || m.username}`,
    fields: [{ name: 'role', label: 'Role', type: 'select', value: m.role, options: ASSIGNABLE, full: true }],
    onSubmit: async (v) => { await Api.patch(`/memberships/${m.id}/`, { role: v.role }); toast('Role updated', 'success'); lv.reload(); },
  });

  const remove = async (m) => {
    if (!(await confirmDialog({ title: 'Remove from team?', message: `${m.user_name || m.username} will lose access to this shop. Their past sales stay on record.`, confirmText: 'Remove', danger: true }))) return;
    try { await Api.del(`/memberships/${m.id}/`); toast('Removed from team', 'success'); lv.reload(); } catch (e) { toastError(e); }
  };

  const restore = async (m) => {
    try { await Api.patch(`/memberships/${m.id}/`, { is_active: true }); toast('Access restored', 'success'); lv.reload(); } catch (e) { toastError(e); }
  };

  lv = listView({
    path: '/memberships/', searchPlaceholder: null,
    columns: [
      { label: 'Person', render: (m) => h('div', null, h('div', { class: 'title' }, m.user_name || m.username), h('div', { class: 'muted small' }, `@${m.username}`)) },
      { label: 'Role', render: (m) => badge(ROLE_LABELS[m.role] || m.role, m.role === 'OWNER' ? 'info' : '') },
      { label: 'Status', render: (m) => (m.is_active ? badge('Active', 'success') : badge('No access', 'danger')) },
      { label: 'Joined', render: (m) => fmtDate(m.joined_at.slice(0, 10)) },
      { label: '', align: 'right', render: (m) => (m.role === 'OWNER' ? '' : h('div', { class: 'row-actions' },
        m.is_active ? [
          h('button', { class: 'btn btn-sm', onclick: () => changeRole(m) }, 'Change role'),
          h('button', { class: 'btn btn-sm btn-ghost', onclick: () => remove(m) }, 'Remove'),
        ] : h('button', { class: 'btn btn-sm', onclick: () => restore(m) }, 'Restore access'))) },
    ],
  });

  container.append(
    pageHead('Team', 'Who can use this shop and what they can do.', h('button', { class: 'btn btn-primary', onclick: addMember }, icon('plus'), 'Add team member')),
    lv.el);
}
