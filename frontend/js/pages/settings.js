import { Api } from '../api.js';
import { h, pageHead, card, buildForm, toast, toastError, opt, withBusy, formModal } from '../ui.js';
import { state, can } from '../state.js';

const TIMEZONES = ['Africa/Lagos', 'Africa/Accra', 'Africa/Nairobi', 'Africa/Johannesburg', 'Africa/Cairo', 'Europe/London', 'America/New_York', 'UTC'];

function formCard(title, fields, submitText, onSubmit) {
  const form = buildForm(fields);
  const submit = h('button', { class: 'btn btn-primary', type: 'submit' }, submitText);
  form.el.append(submit);
  form.el.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!form.valid()) return;
    form.error('');
    await withBusy(submit, async () => {
      try { await onSubmit(form.get(), form); } catch (err) { form.error(err.message); }
    });
  });
  return card(title, h('div', { class: 'card-body' }, form.el));
}

export async function render(container) {
  const user = state.user;
  const store = state.store;
  const stack = h('div', { class: 'stack', style: { maxWidth: '760px' } });

  stack.append(formCard('Your profile', [
    { name: 'full_name', label: 'Full name', value: user.full_name ?? '', full: true },
    { name: 'email', label: 'Email', type: 'email', value: user.email ?? '' },
    { name: 'phone_number', label: 'Phone', value: user.phone_number ?? '', maxlength: 20 },
  ], 'Save profile', async (v) => {
    const updated = await Api.updateProfile({ full_name: v.full_name.trim(), email: v.email.trim(), phone_number: v.phone_number.trim() });
    Object.assign(state.user, updated);
    toast('Profile saved', 'success');
  }));

  stack.append(formCard('Change password', [
    { name: 'current', label: 'Current password', type: 'password', required: true, full: true, autocomplete: 'current-password' },
    { name: 'next', label: 'New password', type: 'password', required: true, full: true, hint: 'At least 8 characters.', autocomplete: 'new-password' },
  ], 'Change password', async (v, form) => {
    await Api.changePassword(v.current, v.next);
    form.el.reset();
    toast('Password changed', 'success');
  }));

  if (can('settings')) {
    const tz = TIMEZONES.includes(store.timezone) ? TIMEZONES : [store.timezone, ...TIMEZONES];
    stack.append(formCard('Shop settings', [
      { name: 'name', label: 'Shop name', required: true, full: true, value: store.name },
      { name: 'business_type', label: 'Business type', type: 'select', value: store.business_type, options: [
        opt('GROCERY', 'Retail Grocery'), opt('PHARMACY', 'Pharmacy'), opt('BOUTIQUE', 'Boutique / Fashion'), opt('SUPERMARKET', 'Supermarket'),
        opt('RESTAURANT', 'Restaurant / QSR'), opt('ELECTRONICS', 'Electronics'), opt('OTHER', 'Other')] },
      { name: 'currency', label: 'Currency', type: 'select', value: store.currency, options: [
        opt('NGN', 'Naira (₦)'), opt('GHS', 'Cedi (₵)'), opt('KES', 'Shilling (KSh)'), opt('ZAR', 'Rand (R)'), opt('USD', 'US Dollar ($)')] },
      { name: 'whatsapp_number', label: 'Shop phone / WhatsApp', required: true, maxlength: 20, value: store.whatsapp_number },
      { name: 'timezone', label: 'Time zone', type: 'select', value: store.timezone, options: tz.map((z) => opt(z, z)), hint: 'Decides when “today” starts and ends.' },
      { name: 'address', label: 'Address (shown on receipts)', full: true, value: store.address ?? '' },
      { name: 'city', label: 'City', value: store.city ?? '' },
      { name: 'daily_revenue_target', label: 'Daily sales target', type: 'number', min: '0', step: '0.01', value: store.daily_revenue_target ?? '0' },
      { name: 'tax_rate_percent', label: 'VAT / sales tax (%)', type: 'number', min: '0', max: '100', step: '0.01', value: store.tax_rate_percent ?? '0', hint: 'Added on top of prices at the till. Use 0 if you do not charge tax.' },
      { name: 'receipt_footer', label: 'Receipt message', full: true, maxlength: 255, value: store.receipt_footer ?? '' },
    ], 'Save shop settings', async (v) => {
      const updated = await Api.updateStore(store.id, {
        name: v.name.trim(), business_type: v.business_type, currency: v.currency, whatsapp_number: v.whatsapp_number.trim(),
        timezone: v.timezone, address: v.address.trim(), city: v.city.trim(),
        daily_revenue_target: Number(v.daily_revenue_target || 0).toFixed(2),
        tax_rate_percent: Number(v.tax_rate_percent || 0).toFixed(2), receipt_footer: v.receipt_footer.trim(),
      });
      window.dispatchEvent(new CustomEvent('store-updated', { detail: updated }));
      toast('Shop settings saved', 'success');
    }));

    stack.append(card('Another shop', h('div', { class: 'card-body' },
      h('p', { style: { marginTop: 0 }, class: 'muted' }, 'Run more than one shop? Each has its own stock, sales and team.'),
      h('button', { class: 'btn', onclick: () => formModal({
        title: 'Create another shop', submitText: 'Create shop',
        fields: [
          { name: 'name', label: 'Shop name', required: true, full: true },
          { name: 'whatsapp_number', label: 'Shop phone / WhatsApp', required: true, full: true, maxlength: 20 },
          { name: 'city', label: 'City', full: true },
        ],
        onSubmit: async (v) => {
          const created = await Api.createStore({ name: v.name.trim(), whatsapp_number: v.whatsapp_number.trim(), city: v.city.trim(), currency: store.currency, business_type: store.business_type });
          window.dispatchEvent(new CustomEvent('stores-changed', { detail: created }));
          toast('Shop created — switch to it from the top bar', 'success');
        },
      }) }, 'Create another shop'))));
  }

  container.append(pageHead('Settings', 'Your account and your shop.'), stack);
}
