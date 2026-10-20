// Application shell: sign-in / register / first-store setup, sidebar layout, hash router.
import { Api, Session, ApiError } from './api.js';
import {
  h, icon, replaceContent, spinner, errorBox, buildForm, toast, toastError, setCurrency, opt,
} from './ui.js';
import { state, can, ROLE_LABELS } from './state.js';

const root = document.getElementById('root');

const PAGES = [
  { path: 'dashboard', label: 'Dashboard', icon: 'grid', group: 'Overview', load: () => import('./pages/dashboard.js') },
  { path: 'pos', label: 'New Sale', icon: 'cart', group: 'Sales', allowed: () => can('sell'), load: () => import('./pages/pos.js') },
  { path: 'sales', label: 'Sales History', icon: 'receipt', group: 'Sales', load: () => import('./pages/sales.js') },
  { path: 'products', label: 'Products', icon: 'package', group: 'Inventory', load: () => import('./pages/products.js') },
  { path: 'stock', label: 'Stock History', icon: 'clock', group: 'Inventory', load: () => import('./pages/stock.js') },
  { path: 'purchases', label: 'Purchase Orders', icon: 'truck', group: 'Inventory', load: () => import('./pages/purchases.js') },
  { path: 'suppliers', label: 'Suppliers', icon: 'briefcase', group: 'Inventory', load: () => import('./pages/suppliers.js') },
  { path: 'customers', label: 'Customers', icon: 'users', group: 'Customers', load: () => import('./pages/customers.js') },
  { path: 'debts', label: 'Debts', icon: 'card', group: 'Customers', load: () => import('./pages/debts.js') },
  { path: 'expenses', label: 'Expenses', icon: 'dollar', group: 'Finance', allowed: () => can('finance'), load: () => import('./pages/expenses.js') },
  { path: 'reports', label: 'Reports', icon: 'chart', group: 'Finance', allowed: () => can('finance'), load: () => import('./pages/reports.js') },
  { path: 'import', label: 'Import Sales', icon: 'upload', group: 'Admin', allowed: () => can('importData'), load: () => import('./pages/import.js') },
  { path: 'team', label: 'Team', icon: 'users', group: 'Admin', allowed: () => can('team'), load: () => import('./pages/team.js') },
  { path: 'settings', label: 'Settings', icon: 'sliders', group: 'Admin', load: () => import('./pages/settings.js') },
];

// ── Session ────────────────────────────────────────────────────────────────
function selectStore(store) {
  Session.storeId = store.id;
  state.store = store;
  const membership = (state.memberships || []).find((m) => m.store === store.id);
  state.role = membership ? membership.role : null;
  setCurrency(store.currency);
}

async function loadSession() {
  const me = await Api.me();
  state.user = me.user;
  state.memberships = me.memberships;
  const data = await Api.listStores();
  state.stores = Array.isArray(data) ? data : data.results;
  if (!state.stores.length) { showOnboarding(); return; }
  const saved = Session.storeId;
  selectStore(state.stores.find((s) => s.id === saved) || state.stores[0]);
  renderShell();
}

export async function refreshStores() {
  const me = await Api.me();                 // a newly created shop adds a membership
  state.memberships = me.memberships;
  const data = await Api.listStores();
  state.stores = Array.isArray(data) ? data : data.results;
  const fresh = state.stores.find((s) => state.store && s.id === state.store.id);
  if (fresh) selectStore(fresh);
}

let signingOut = false;
async function signOut(notice) {
  if (signingOut) return;            // the logout call itself can raise 'session-expired'
  signingOut = true;
  try { await Api.logout(); } finally { signingOut = false; }
  Object.assign(state, { user: null, stores: [], store: null, role: null, memberships: [] });
  showAuth('login', notice);
}

// ── Auth screens ───────────────────────────────────────────────────────────
function authCard(title, subtitle, ...children) {
  return h('div', { class: 'auth-wrap' },
    h('div', { class: 'auth-card' },
      h('div', { class: 'brand', style: { padding: 0 } }, h('div', { class: 'brand-mark' }, 'ST'), 'Shopytics'),
      h('h1', null, title),
      h('div', { class: 'sub' }, subtitle),
      ...children,
    ));
}

function showAuth(mode = 'login', notice) {
  const isLogin = mode === 'login';
  const form = buildForm(isLogin
    ? [
      { name: 'username', label: 'Username', required: true, full: true, autocomplete: 'username' },
      { name: 'password', label: 'Password', type: 'password', required: true, full: true, autocomplete: 'current-password' },
    ]
    : [
      { name: 'full_name', label: 'Full name', required: true, full: true },
      { name: 'username', label: 'Username', required: true, full: true, autocomplete: 'username' },
      { name: 'email', label: 'Email (optional)', type: 'email', full: true },
      { name: 'phone_number', label: 'Phone (optional)', full: true, maxlength: 20 },
      { name: 'password', label: 'Password', type: 'password', required: true, full: true, hint: 'At least 8 characters.', autocomplete: 'new-password' },
    ]);
  if (notice) form.error(notice);
  const submit = h('button', { class: 'btn btn-primary btn-block', type: 'submit' }, isLogin ? 'Sign in' : 'Create account');
  form.el.append(submit);
  form.el.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!form.valid()) return;
    form.error('');
    submit.disabled = true;
    const v = form.get();
    try {
      if (isLogin) {
        await Api.login(v.username.trim(), v.password);
      } else {
        const payload = { username: v.username.trim(), password: v.password, full_name: v.full_name.trim() };
        if (v.email.trim()) payload.email = v.email.trim();
        if (v.phone_number.trim()) payload.phone_number = v.phone_number.trim();
        await Api.register(payload);
      }
    } catch (err) {
      form.error(err.status === 401 ? 'Wrong username or password.' : err.message);
      submit.disabled = false;
      return;
    }
    replaceContent(root, spinner());
    try {
      await loadSession();
    } catch (err) {
      Session.clear();
      showAuth('login', err.message);
    }
  });
  const switchLink = h('a', { onclick: () => showAuth(isLogin ? 'register' : 'login') }, isLogin ? 'Create one' : 'Sign in');
  replaceContent(root, authCard(
    isLogin ? 'Sign in' : 'Create your account',
    isLogin ? 'Manage your shop — sales, stock and customers.' : 'Set up Shopytics for your business.',
    form.el,
    h('div', { class: 'auth-switch' }, isLogin ? "Don't have an account? " : 'Already have an account? ', switchLink),
  ));
}

function showOnboarding() {
  const form = buildForm([
    { name: 'name', label: 'Shop name', required: true, full: true },
    { name: 'business_type', label: 'Business type', type: 'select', value: 'GROCERY', options: [
      opt('GROCERY', 'Retail Grocery'), opt('PHARMACY', 'Pharmacy'), opt('BOUTIQUE', 'Boutique / Fashion'),
      opt('SUPERMARKET', 'Supermarket'), opt('RESTAURANT', 'Restaurant / QSR'), opt('ELECTRONICS', 'Electronics'), opt('OTHER', 'Other'),
    ] },
    { name: 'currency', label: 'Currency', type: 'select', value: 'NGN', options: [
      opt('NGN', 'Naira (₦)'), opt('GHS', 'Cedi (₵)'), opt('KES', 'Shilling (KSh)'), opt('ZAR', 'Rand (R)'), opt('USD', 'US Dollar ($)'),
    ] },
    { name: 'whatsapp_number', label: 'Shop phone / WhatsApp', required: true, full: true, placeholder: '0803 000 0000', maxlength: 20 },
    { name: 'city', label: 'City', full: true },
  ]);
  const submit = h('button', { class: 'btn btn-primary btn-block', type: 'submit' }, 'Create shop');
  form.el.append(submit);
  form.el.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!form.valid()) return;
    form.error('');
    submit.disabled = true;
    try {
      const v = form.get();
      await Api.createStore({ name: v.name.trim(), business_type: v.business_type, currency: v.currency, whatsapp_number: v.whatsapp_number.trim(), city: v.city.trim() });
      replaceContent(root, spinner());
      await loadSession();
    } catch (err) {
      form.error(err.message);
      submit.disabled = false;
    }
  });
  replaceContent(root, authCard('Set up your shop', 'Tell us about your business to get started.', form.el,
    h('div', { class: 'auth-switch' }, h('a', { onclick: () => signOut() }, 'Sign out'))));
}

// ── Shell ──────────────────────────────────────────────────────────────────
let sidebar, content, titleEl, cleanup = null, navSeq = 0;

function renderShell() {
  const groups = [];
  PAGES.filter((p) => !p.allowed || p.allowed()).forEach((p) => {
    let g = groups.find((x) => x.name === p.group);
    if (!g) { g = { name: p.group, pages: [] }; groups.push(g); }
    g.pages.push(p);
  });

  const nav = h('nav', { class: 'nav' }, groups.map((g) =>
    h('div', { class: 'nav-group' },
      h('div', { class: 'nav-title' }, g.name),
      g.pages.map((p) => h('a', {
        class: 'nav-link', href: `#/${p.path}`, dataset: { path: p.path },
        onclick: () => sidebar.classList.remove('open'),
      }, icon(p.icon), p.label)))));

  sidebar = h('aside', { class: 'sidebar' },
    h('div', { class: 'brand' }, h('div', { class: 'brand-mark' }, 'ST'), 'Shopytics'),
    nav,
    h('div', { class: 'sidebar-foot muted' }, state.store.name, h('br'), ROLE_LABELS[state.role] || 'No access'),
  );

  const storeControl = state.stores.length > 1
    ? h('select', {
      class: 'select', 'aria-label': 'Switch shop',
      onchange: (e) => {
        selectStore(state.stores.find((s) => s.id === e.target.value));
        renderShell();
      },
    }, state.stores.map((s) => h('option', { value: s.id, selected: s.id === state.store.id }, s.name)))
    : h('strong', null, state.store.name);

  const initials = (state.user.full_name || state.user.username || '?').split(/\s+/).map((w) => w[0]).slice(0, 2).join('').toUpperCase();
  titleEl = h('span', { class: 'muted' });
  const topbar = h('header', { class: 'topbar' },
    h('button', { class: 'btn btn-ghost menu-btn', 'aria-label': 'Menu', onclick: () => sidebar.classList.toggle('open') }, icon('menu')),
    storeControl,
    h('div', { class: 'spacer' }),
    h('div', { class: 'user-chip' }, h('div', { class: 'avatar' }, initials), h('span', { class: 'user-name' }, state.user.full_name || state.user.username)),
    h('button', { class: 'btn btn-sm', onclick: () => signOut() }, icon('logout'), 'Sign out'),
  );

  content = h('main', { class: 'content', id: 'content' });
  replaceContent(root, h('div', { class: 'app' }, sidebar, h('div', { class: 'main' }, topbar, content)));
  route();
}

// ── Router ─────────────────────────────────────────────────────────────────
function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [name, query] = raw.split('?');
  return { name: name || 'dashboard', query: new URLSearchParams(query || '') };
}

async function route() {
  if (!content || !state.store) return;
  const seq = ++navSeq;
  const { name, query } = parseHash();
  let page = PAGES.find((p) => p.path === name);
  if (!page || (page.allowed && !page.allowed())) {
    if (location.hash !== '#/dashboard') { location.hash = '#/dashboard'; return; }
    page = PAGES[0];
  }
  sidebar.querySelectorAll('.nav-link').forEach((a) => a.classList.toggle('active', a.dataset.path === page.path));
  document.title = `${page.label} — Shopytics`;

  if (cleanup) { try { cleanup(); } catch (e) { /* page teardown must never block navigation */ } cleanup = null; }
  replaceContent(content, spinner());
  try {
    const mod = await page.load();
    if (seq !== navSeq) return;
    const holder = h('div');
    replaceContent(content, holder);
    const result = await mod.render(holder, { query, navigate: (p) => { location.hash = `#/${p}`; } });
    if (seq === navSeq && typeof result === 'function') cleanup = result;
  } catch (e) {
    if (seq !== navSeq) return;
    console.error(e);
    replaceContent(content, errorBox(e instanceof ApiError ? e.message : `This page failed to load: ${e.message}`));
  }
}

window.addEventListener('hashchange', route);
window.addEventListener('store-updated', async (e) => {
  const i = state.stores.findIndex((s) => s.id === e.detail.id);
  if (i >= 0) state.stores[i] = e.detail;
  selectStore(e.detail);
  renderShell();
});
window.addEventListener('stores-changed', async () => {
  try { await refreshStores(); renderShell(); } catch (err) { toastError(err); }
});
window.addEventListener('session-expired', () => { if (state.user) signOut('Your session expired. Please sign in again.'); });

// ── Boot ───────────────────────────────────────────────────────────────────
async function boot() {
  if (!Session.access) { showAuth('login'); return; }
  try {
    await loadSession();
  } catch (e) {
    if (e instanceof ApiError && e.status === 0) {
      replaceContent(root, h('div', { class: 'auth-wrap' }, h('div', { class: 'auth-card' },
        h('h1', null, 'Cannot connect'), h('div', { class: 'sub' }, e.message),
        h('button', { class: 'btn btn-primary btn-block', onclick: () => { replaceContent(root, spinner()); boot(); } }, 'Try again'))));
      return;
    }
    Session.clear();
    showAuth('login');
  }
}

boot();
