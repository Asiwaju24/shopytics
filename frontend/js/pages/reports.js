import { Api } from '../api.js';
import {
  h, money, num, fmtDate, pageHead, card, table, empty, statCard, spinner, errorBox, replaceContent,
  toast, toastError, opt, icon, isoDate, daysAgo, withBusy,
} from '../ui.js';
import { barChart } from '../chart.js';

export async function render(container) {
  const from = h('input', { class: 'input', type: 'date', value: daysAgo(29), 'aria-label': 'From date' });
  const to = h('input', { class: 'input', type: 'date', value: isoDate(), 'aria-label': 'To date' });
  const body = h('div');

  const preset = (label, fn) => h('button', { class: 'btn btn-sm', onclick: () => { const [a, b] = fn(); from.value = a; to.value = b; load(); } }, label);
  const monthStart = () => { const n = new Date(); return isoDate(new Date(n.getFullYear(), n.getMonth(), 1)); };

  async function load() {
    replaceContent(body, spinner());
    try {
      const s = await Api.get('/reports/summary/', { date_from: from.value, date_to: to.value });
      replaceContent(body, view(s));
    } catch (e) { replaceContent(body, errorBox(e.message)); }
  }

  function view(s) {
    const pl = (label, value, { strong = false, sub = false } = {}) => h('tr', null,
      h('td', { style: { paddingLeft: sub ? '32px' : '16px', fontWeight: strong ? 650 : 400 } }, label),
      h('td', { class: 'right', style: { fontWeight: strong ? 650 : 400 } }, value));
    const days = s.daily.map((d) => ({
      label: d.date.slice(8) + '/' + d.date.slice(5, 7), value: Number(d.revenue),
      title: `${fmtDate(d.date)}: revenue ${money(d.revenue)}, profit ${money(d.profit)}`,
    }));
    return h('div', { class: 'stack' },
      h('div', { class: 'grid cols-4' },
        statCard('Revenue', money(s.revenue), `${num(s.orders)} sales · ${num(s.units_sold)} items`),
        statCard('Gross profit', money(s.gross_profit), 'Revenue minus cost of goods'),
        statCard('Expenses', money(s.expenses_total)),
        statCard('Net profit', money(s.net_profit), 'After expenses')),
      h('div', { class: 'grid main-side' },
        card('Revenue by day', h('div', { class: 'card-body' }, days.some((d) => d.value > 0) ? barChart(days, { labelEvery: Math.max(1, Math.ceil(days.length / 8)) }) : empty('No sales in this period'))),
        card('Profit & loss', h('table', { class: 'table' }, h('tbody', null,
          pl('Sales revenue', money(s.revenue)),
          pl('Cost of goods sold', `− ${money(s.cost_of_goods)}`, { sub: true }),
          pl('Gross profit', money(s.gross_profit), { strong: true }),
          pl('Expenses', `− ${money(s.expenses_total)}`, { sub: true }),
          pl('Net profit', money(s.net_profit), { strong: true }))))),
      h('div', { class: 'grid cols-2' },
        card('Best-selling products', table([
          { label: 'Product', render: (p) => p.product_name },
          { label: 'Units', align: 'right', render: (p) => num(p.units) },
          { label: 'Revenue', align: 'right', render: (p) => money(p.revenue) },
          { label: 'Profit', align: 'right', render: (p) => money(p.profit) },
        ], s.top_products, empty('No sales in this period'))),
        card('How customers paid', table([
          { label: 'Method', render: (p) => p.label },
          { label: 'Sales', align: 'right', render: (p) => num(p.count) },
          { label: 'Total', align: 'right', render: (p) => money(p.total) },
        ], s.payment_breakdown, empty('No sales in this period')))),
      h('div', { class: 'grid cols-2' },
        card('Where the money went', table([
          { label: 'Category', render: (c) => c.label },
          { label: 'Amount', align: 'right', render: (c) => money(c.total) },
        ], s.expenses_by_category, empty('No expenses in this period'))),
        card('Other figures', h('table', { class: 'table' }, h('tbody', null,
          pl('Average sale', money(s.average_order_value)),
          pl('Tax collected on sales', money(s.tax_collected)),
          pl('Owed by customers (all time)', money(s.debts_outstanding)),
          pl('…of which overdue', money(s.debts_overdue), { sub: true }),
          pl('Stock value at cost (today)', money(s.inventory_value)),
          pl('Voided sales', `${num(s.voided_count)} (${money(s.voided_total)})`))))),
      downloads());
  }

  function downloads() {
    const kind = h('select', { class: 'select', 'aria-label': 'Report' }, [
      opt('sales', 'Sales (one row per receipt)'), opt('sale-items', 'Sales (one row per item sold)'),
      opt('inventory', 'Stock list with values'), opt('debtors', 'Customers who owe'), opt('expenses', 'Expenses'),
    ].map((o) => h('option', { value: o.value }, o.label)));
    const fmt = h('select', { class: 'select', 'aria-label': 'Format' }, [h('option', { value: 'xlsx' }, 'Excel (.xlsx)'), h('option', { value: 'csv' }, 'CSV')]);
    const btn = h('button', { class: 'btn btn-primary' }, icon('download'), 'Download');
    btn.addEventListener('click', () => withBusy(btn, async () => {
      try { await Api.download(`/reports/export/${kind.value}/`, { fmt: fmt.value, date_from: from.value, date_to: to.value }, `${kind.value}.${fmt.value}`); }
      catch (e) { toastError(e); }
    }));
    return card('Download data', h('div', { class: 'card-body' },
      h('p', { class: 'muted', style: { marginTop: 0 } }, 'Uses the dates above. Stock list and debtors always show today’s position.'),
      h('div', { class: 'toolbar', style: { border: 0, padding: 0 } }, kind, fmt, btn)));
  }

  container.append(
    pageHead('Reports', 'How the business is doing over any period.'),
    h('div', { class: 'card', style: { marginBottom: '16px' } }, h('div', { class: 'toolbar', style: { border: 0 } },
      h('span', { class: 'muted small' }, 'From'), from, h('span', { class: 'muted small' }, 'to'), to,
      h('button', { class: 'btn btn-primary', onclick: load }, 'Update'),
      preset('Today', () => [isoDate(), isoDate()]),
      preset('Last 7 days', () => [daysAgo(6), isoDate()]),
      preset('Last 30 days', () => [daysAgo(29), isoDate()]),
      preset('This month', () => [monthStart(), isoDate()]))),
    body);
  await load();
}
