import { Api } from '../api.js';
import {
  h, icon, money, num, isoDate, badge, card, empty, spinner, errorBox, replaceContent,
  pageHead, toast, toastError, debounce, openModal, formModal,
} from '../ui.js';
import { state } from '../state.js';
import { showReceipt } from '../receipt.js';

const cents = (n) => Math.round(Number(n) * 100);
const fromCents = (c) => c / 100;

export async function render(container) {
  const taxRate = Number(state.store.tax_rate_percent) || 0;
  const cart = [];                       // {product, quantity, price, priceEdited}
  let customer = null;
  let method = 'CASH';
  let paidInput = '';
  let dueDate = '';
  let note = '';

  const gridBox = h('div');
  const cartBox = h('div');
  const searchInput = h('input', { class: 'input', type: 'search', placeholder: 'Search or scan barcode…', 'aria-label': 'Find product', autofocus: true });

  // ── Product grid ─────────────────────────────────────────
  let lastResults = [];
  async function loadProducts() {
    replaceContent(gridBox, spinner());
    try {
      const data = await Api.get('/products/', { search: searchInput.value.trim(), page_size: 60, ordering: 'name' });
      lastResults = data.results;
      renderGrid(data);
    } catch (e) { replaceContent(gridBox, errorBox(e.message)); }
  }
  function renderGrid(data) {
    if (!data.results.length) { replaceContent(gridBox, empty('No products found', 'Check the spelling or add the product under Products.')); return; }
    const tiles = data.results.map((p) => h('button', {
      class: 'product-tile', disabled: p.current_stock <= 0, onclick: () => addToCart(p),
      title: p.current_stock <= 0 ? 'Out of stock' : 'Add to sale',
    },
      h('div', { class: 'name' }, p.name),
      h('div', { class: 'meta' }, h('span', null, money(p.selling_price)),
        p.current_stock <= 0 ? badge('Out', 'danger') : h('span', null, `${num(p.current_stock)} left`))));
    replaceContent(gridBox, h('div', { class: 'product-grid' }, tiles),
      data.count > data.results.length ? h('div', { class: 'card-body muted small' }, `Showing ${data.results.length} of ${data.count}. Search to narrow down.`) : null);
  }
  searchInput.addEventListener('input', debounce(loadProducts, 250));
  searchInput.addEventListener('keydown', async (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    const code = searchInput.value.trim();
    if (!code) return;
    try {
      const data = await Api.get('/products/', { search: code, page_size: 50 });
      const exact = data.results.find((p) => p.sku.toLowerCase() === code.toLowerCase());
      const pick = exact || (data.results.length === 1 ? data.results[0] : null);
      if (pick) { addToCart(pick); searchInput.value = ''; loadProducts(); }
      else if (!data.results.length) toast(`No product matches "${code}"`, 'error');
      else { lastResults = data.results; renderGrid(data); }
    } catch (err) { toastError(err); }
  });

  // ── Cart logic ───────────────────────────────────────────
  // Inputs (paid / due / note) and the Charge button are created ONCE and only updated,
  // never re-created — re-creating them on blur would swallow the click on "Charge".
  const linesBox = h('div');
  const totalsBox = h('div', { class: 'totals' });
  const customerBox = h('div', { class: 'field' });
  const methodBox = h('div', { class: 'segmented' });
  const paidField = h('input', { class: 'input', type: 'number', min: '0', step: '0.01', 'aria-label': 'Amount received', oninput: () => { paidInput = paidField.value; updateTotals(); } });
  const dueField = h('input', { class: 'input', type: 'date', 'aria-label': 'Due date', oninput: () => { dueDate = dueField.value; } });
  const noteField = h('input', { class: 'input', maxlength: '200', 'aria-label': 'Note', oninput: () => { note = noteField.value; } });
  const paidLabel = h('label', null, 'Amount received');
  const paidHint = h('div', { class: 'hint' });
  const debtText = h('strong');
  const needCustomer = h('div', null, 'Select a customer first.');
  const debtAlert = h('div', { class: 'alert warning', hidden: true },
    debtText, needCustomer,
    h('div', { class: 'field', style: { marginTop: '8px', marginBottom: 0 } }, h('label', null, 'Due date'), dueField));
  const chargeBtn = h('button', { class: 'btn btn-primary btn-block', onclick: () => checkout(chargeBtn) });
  const clearBtn = h('button', { class: 'btn btn-sm btn-ghost', onclick: () => { cart.length = 0; renderLines(); } }, 'Clear');

  function addToCart(product) {
    const line = cart.find((l) => l.product.id === product.id);
    if (line) {
      if (line.quantity >= product.current_stock) { toast(`Only ${product.current_stock} of ${product.name} in stock`, 'error'); return; }
      line.quantity += 1;
    } else {
      if (product.current_stock <= 0) { toast(`${product.name} is out of stock`, 'error'); return; }
      cart.push({ product, quantity: 1, price: Number(product.selling_price), priceEdited: false });
    }
    renderLines();
  }

  function totals() {
    const subtotal = cart.reduce((sum, l) => sum + cents(l.quantity * l.price), 0);
    const tax = Math.round(subtotal * taxRate / 100);
    return { subtotal, tax, total: subtotal + tax };
  }

  function paidCents(t) {
    if (paidInput !== '') return Math.max(0, cents(paidInput));
    return method === 'CREDIT' ? 0 : t.total;
  }

  function updateTotals() {
    const t = totals();
    const paid = paidCents(t);
    const balance = Math.max(0, t.total - paid);
    replaceContent(totalsBox,
      h('div', { class: 'row' }, h('span', null, 'Subtotal'), h('span', null, money(fromCents(t.subtotal)))),
      taxRate > 0 ? h('div', { class: 'row' }, h('span', null, `Tax (${taxRate}%)`), h('span', null, money(fromCents(t.tax)))) : null,
      h('div', { class: 'row grand' }, h('span', null, 'Total'), h('span', null, money(fromCents(t.total)))));
    paidField.placeholder = method === 'CREDIT' ? '0' : String(fromCents(t.total));
    paidLabel.textContent = method === 'CREDIT' ? 'Paid now (optional)' : 'Amount received';
    paidHint.textContent = method === 'CREDIT' ? 'Leave blank if nothing was paid today.' : 'Leave blank if paid in full. Enter less to record a part-payment.';
    debtAlert.hidden = !(balance > 0 && cart.length);
    debtText.textContent = `${money(fromCents(balance))} will be recorded as a debt`;
    needCustomer.hidden = !!customer;
    chargeBtn.disabled = !cart.length;
    replaceContent(chargeBtn, icon('check'), `Charge ${money(fromCents(t.total))}`);
    clearBtn.hidden = !cart.length;
  }

  function renderLines() {
    if (!cart.length) {
      replaceContent(linesBox, empty('The sale is empty', 'Tap a product or scan a barcode to add it.'));
      updateTotals();
      return;
    }
    replaceContent(linesBox, cart.map((l, i) => {
      const totalEl = h('div', { class: 'title' }, money(fromCents(cents(l.quantity * l.price))));
      const qtyInput = h('input', {
        class: 'input', type: 'number', min: '1', max: String(l.product.current_stock), value: String(l.quantity),
        'aria-label': `Quantity of ${l.product.name}`,
        onchange: (e) => {
          let v = parseInt(e.target.value, 10);
          if (!(v >= 1)) v = 1;
          if (v > l.product.current_stock) { v = l.product.current_stock; toast(`Only ${v} of ${l.product.name} in stock`, 'error'); }
          l.quantity = v; e.target.value = String(v);
          totalEl.textContent = money(fromCents(cents(l.quantity * l.price)));
          updateTotals();
        },
      });
      const priceInput = h('input', {
        class: 'input', type: 'number', min: '0', step: '0.01', value: String(l.price), 'aria-label': `Price for ${l.product.name}`,
        style: { width: '96px', height: '26px', display: 'inline-block' },
        onchange: (e) => {
          const v = Number(e.target.value);
          if (!(v >= 0) || e.target.value === '') { e.target.value = String(l.price); return; }
          l.price = v; l.priceEdited = true;
          totalEl.textContent = money(fromCents(cents(l.quantity * l.price)));
          updateTotals();
        },
      });
      return h('div', { class: 'cart-line' },
        h('div', null, h('div', { class: 'title' }, l.product.name), h('div', { class: 'muted small' }, 'Price ', priceInput)),
        h('div', { class: 'right' }, totalEl,
          h('div', { class: 'qty' },
            h('button', { class: 'btn btn-sm', 'aria-label': 'Decrease', onclick: () => { l.quantity -= 1; if (l.quantity <= 0) cart.splice(i, 1); renderLines(); } }, '−'),
            qtyInput,
            h('button', { class: 'btn btn-sm', 'aria-label': 'Increase', onclick: () => {
              if (l.quantity >= l.product.current_stock) { toast(`Only ${l.product.current_stock} in stock`, 'error'); return; }
              l.quantity += 1; renderLines();
            } }, '+'),
            h('button', { class: 'btn btn-sm btn-ghost', 'aria-label': 'Remove', onclick: () => { cart.splice(i, 1); renderLines(); } }, icon('trash')))));
    }));
    updateTotals();
  }

  function renderCustomer() {
    replaceContent(customerBox, h('label', null, 'Customer'),
      customer
        ? h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
          h('span', null, h('strong', null, customer.name), ' ', h('span', { class: 'muted' }, customer.phone_number)),
          h('button', { class: 'btn btn-sm btn-ghost', onclick: () => { customer = null; renderCustomer(); } }, 'Remove'))
        : h('button', { class: 'btn', onclick: pickCustomer }, icon('users'), 'Select customer (optional)'));
    updateTotals();
  }

  function renderMethods() {
    const methods = [['CASH', 'Cash'], ['TRANSFER', 'Transfer'], ['CARD', 'Card / POS'], ['CREDIT', 'On credit']];
    replaceContent(methodBox, methods.map(([value, label]) => h('button', {
      type: 'button', class: method === value ? 'active' : '',
      onclick: () => { method = value; if (value === 'CREDIT') { paidInput = ''; paidField.value = ''; } renderMethods(); updateTotals(); },
    }, label)));
  }

  const cartCard = h('div', { class: 'card' },
    h('div', { class: 'card-head' }, h('h2', null, 'Current sale'), clearBtn),
    linesBox, totalsBox,
    h('div', { class: 'card-body', style: { borderTop: '1px solid var(--border)' } },
      customerBox,
      h('div', { class: 'field' }, h('label', null, 'Payment method'), methodBox),
      h('div', { class: 'field' }, paidLabel, paidField, paidHint),
      debtAlert,
      h('div', { class: 'field' }, h('label', null, 'Note (optional)'), noteField),
      chargeBtn));
  replaceContent(cartBox, cartCard);

  // ── Customer picker ──────────────────────────────────────
  function pickCustomer() {
    const results = h('div', { style: { maxHeight: '260px', overflowY: 'auto', marginTop: '10px' } });
    const search = h('input', { class: 'input', type: 'search', placeholder: 'Search name or phone…' });
    const modal = openModal({
      title: 'Select customer',
      body: h('div', null, search, results,
        h('div', { style: { marginTop: '12px' } }, h('button', { class: 'btn', onclick: () => { modal.close(); newCustomer(); } }, icon('plus'), 'Add new customer'))),
    });
    const load = async () => {
      try {
        const data = await Api.get('/customers/', { search: search.value.trim(), page_size: 30 });
        replaceContent(results, data.results.length ? h('ul', { class: 'list' }, data.results.map((c) => h('li', { style: { cursor: 'pointer' }, onclick: () => { customer = c; modal.close(); renderCustomer(); } },
          h('div', null, h('div', { class: 'title' }, c.name), h('div', { class: 'muted small' }, c.phone_number)),
          Number(c.outstanding_debt) > 0 ? badge(`Owes ${money(c.outstanding_debt)}`, 'warning') : null))) : empty('No customers found'));
      } catch (e) { replaceContent(results, errorBox(e.message)); }
    };
    search.addEventListener('input', debounce(load, 250));
    load();
  }

  function newCustomer() {
    formModal({
      title: 'New customer',
      fields: [
        { name: 'name', label: 'Name', required: true, full: true },
        { name: 'phone_number', label: 'Phone number', required: true, full: true, maxlength: 20 },
      ],
      submitText: 'Add customer',
      onSubmit: async (v) => { customer = await Api.post('/customers/', { name: v.name.trim(), phone_number: v.phone_number.trim() }); renderCustomer(); },
    });
  }

  // ── Checkout ─────────────────────────────────────────────
  async function checkout(button) {
    const t = totals();
    const paid = paidCents(t);
    if (paid > t.total) { toast('Amount received is more than the total', 'error'); return; }
    const balance = t.total - paid;
    if (balance > 0 && !customer) { toast('Select a customer to record a credit or part-payment', 'error'); return; }
    const payload = {
      items: cart.map((l) => {
        const item = { product: l.product.id, quantity: l.quantity };
        if (l.priceEdited) item.unit_price = l.price.toFixed(2);
        return item;
      }),
      payment_method: method,
      notes: note.trim(),
    };
    if (customer) payload.customer = customer.id;
    if (paidInput !== '' || method === 'CREDIT') payload.amount_paid = fromCents(paid).toFixed(2);
    if (balance > 0 && dueDate) payload.due_date = dueDate;
    button.disabled = true;                    // prevent double-charging
    try {
      const sale = await Api.post('/sales/', payload);
      cart.length = 0; customer = null; paidInput = ''; dueDate = ''; note = ''; method = 'CASH';
      paidField.value = ''; dueField.value = ''; noteField.value = '';
      renderLines(); renderCustomer(); renderMethods();
      toast(`Sale ${sale.receipt_number} recorded`, 'success');
      showReceipt(sale);
      loadProducts();
    } catch (e) {
      toastError(e);
      loadProducts();                          // stock may have changed under us
    } finally {
      updateTotals();                          // re-applies the right disabled state
    }
  }

  container.append(
    pageHead('New sale', 'Add products, choose how the customer pays, then charge.'),
    h('div', { class: 'pos' },
      h('div', { class: 'card' }, h('div', { class: 'toolbar' }, searchInput), gridBox),
      cartBox));
  renderLines(); renderCustomer(); renderMethods();
  await loadProducts();
  searchInput.focus();
}
