import { Api } from '../api.js';
import {
  h, icon, money, num, fmtDate, fmtDateTime, badge, card, table, empty, spinner, errorBox,
  replaceContent, pageHead, statCard, toast, toastError,
} from '../ui.js';
import { barChart } from '../chart.js';
import { state, can } from '../state.js';

function deltaText(kpi) {
  if (kpi.key === 'money_at_risk') return 'Value of stock that is running low';
  const d = Number(kpi.delta_percent) || 0;
  const arrow = d > 0 ? '▲' : d < 0 ? '▼' : '–';
  const cls = d > 0 ? 'delta-up' : d < 0 ? 'delta-down' : 'muted';
  return h('span', null, h('span', { class: cls }, `${arrow} ${Math.abs(d)}%`), ' vs yesterday');
}

function kpiCard(kpi) {
  let extra = null;
  let sub = deltaText(kpi);
  if (kpi.key === 'today_revenue' && kpi.target && Number(kpi.target) > 0) {
    const pct = Math.min(100, Number(kpi.progress_percent) || 0);
    sub = h('span', null, deltaText(kpi), h('br'), `${Math.round(Number(kpi.progress_percent) || 0)}% of ${money(kpi.target)} daily target`);
    extra = h('div', { class: 'progress' }, h('span', { style: { width: `${pct}%` } }));
  }
  return statCard(kpi.label, kpi.display_value, sub, extra);
}

function tier(t) {
  return t === 'critical' ? badge('Critical', 'danger') : t === 'low' ? badge('Low', 'warning') : badge('OK', 'success');
}

function scoreBar(label, value) {
  const color = value >= 80 ? 'var(--success)' : value >= 60 ? 'var(--primary)' : value >= 40 ? 'var(--warning)' : 'var(--danger)';
  return h('div', null,
    h('div', { class: 'row' }, h('span', null, label), h('strong', null, value)),
    h('div', { class: 'progress', style: { marginTop: '0' } }, h('span', { style: { width: `${value}%`, background: color } })));
}

function view(data, trend, ctx) {
  const root = h('div', { class: 'stack' });

  if (data.insight_banner) {
    root.append(h('div', { class: 'alert warning' }, h('strong', null, data.insight_banner.title), ' — ', data.insight_banner.body));
  }

  root.append(h('div', { class: 'grid cols-4' }, data.kpis.map(kpiCard)));

  // Trend + health
  const points = trend.series.map((d) => ({
    label: d.date.slice(8) + '/' + d.date.slice(5, 7),
    value: d.revenue,
    highlight: d.is_event,
    title: `${fmtDate(d.date)}: ${money(d.revenue)} (${d.transaction_count} line${d.transaction_count === 1 ? '' : 's'})`,
  }));
  const hasSales = points.some((p) => p.value > 0);
  const health = data.health;
  root.append(h('div', { class: 'grid main-side' },
    card('Sales — last 30 days', h('div', { class: 'card-body' }, hasSales ? barChart(points, { labelEvery: 5 }) : empty('No sales yet', 'Record a sale and it will show up here.'))),
    card('Business health', h('div', { class: 'card-body' },
      h('div', { style: { display: 'flex', alignItems: 'baseline', gap: '10px', marginBottom: '16px' } },
        h('span', { class: 'big-score' }, health.overall_score), h('span', { class: 'muted' }, `/ 100 · ${health.label}`)),
      h('div', { class: 'score-bars' },
        scoreBar('Inventory', health.inventory_score), scoreBar('Revenue', health.revenue_score),
        scoreBar('Customers', health.customer_score), scoreBar('Operations', health.operations_score)),
      h('ul', { class: 'muted small', style: { margin: '4px 0 0', paddingLeft: '18px' } }, health.insights.map((t) => h('li', null, t)))),
    )));

  // Stock alerts + top sellers
  const alertRows = data.stock_alerts;
  root.append(h('div', { class: 'grid cols-2' },
    card('Stock alerts', alertRows.length ? table([
      { label: 'Product', render: (a) => h('div', null, h('div', { class: 'title' }, a.product_name), h('div', { class: 'muted small' }, a.sku)) },
      { label: 'In stock', align: 'right', render: (a) => num(a.current_stock) },
      { label: 'Days left', align: 'right', render: (a) => (a.days_remaining === null ? '—' : num(a.days_remaining, 1)) },
      { label: 'Status', render: (a) => tier(a.tier) },
      { label: '', align: 'right', render: (a) => (can('purchases')
        ? h('button', { class: 'btn btn-sm', onclick: () => ctx.navigate(`purchases?product=${a.product_id}`) }, 'Reorder') : '') },
    ], alertRows) : empty('Stock levels look fine', 'Nothing is running low right now.')),
    card('Top sellers — last 7 days', data.top_movers.length ? table([
      { label: 'Product', render: (m) => m.product_name },
      { label: 'Units', align: 'right', render: (m) => num(m.units_sold) },
      { label: 'Revenue', align: 'right', render: (m) => money(m.revenue) },
      { label: 'vs prior week', align: 'right', render: (m) => h('span', { class: m.delta_percent > 0 ? 'delta-up' : m.delta_percent < 0 ? 'delta-down' : 'muted' }, `${m.delta_percent > 0 ? '+' : ''}${m.delta_percent}%`) },
    ], data.top_movers) : empty('No sales in the last 7 days')),
  ));

  // Debts + suggestions
  const debt = data.debt_summary;
  root.append(h('div', { class: 'grid cols-2' },
    card('Customer debts', h('div', null,
      h('div', { class: 'card-body', style: { display: 'flex', gap: '28px', flexWrap: 'wrap' } },
        h('div', null, h('div', { class: 'muted small' }, 'Outstanding'), h('div', { class: 'value', style: { fontSize: '20px', fontWeight: 650 } }, money(debt.total_outstanding))),
        h('div', null, h('div', { class: 'muted small' }, 'Customers owing'), h('div', { style: { fontSize: '20px', fontWeight: 650 } }, debt.debtor_count)),
        h('div', null, h('div', { class: 'muted small' }, 'Overdue'), h('div', { style: { fontSize: '20px', fontWeight: 650 }, class: debt.overdue_count ? 'delta-down' : '' }, debt.overdue_count))),
      debt.top_debts.length ? table([
        { label: 'Customer', render: (d) => d.customer_name },
        { label: 'Owes', align: 'right', render: (d) => money(d.outstanding_amount) },
        { label: 'Due', render: (d) => (d.days_overdue > 0 ? badge(`${d.days_overdue}d overdue`, 'danger') : fmtDate(d.due_date)) },
      ], debt.top_debts) : null,
      h('div', { class: 'card-body' }, h('a', { href: '#/debts' }, 'View all debts →')))),
    card('Suggestions', data.priority_actions.length ? suggestionList(data.priority_actions) : empty('No open suggestions', 'We will flag things like low stock and late payments here.')),
  ));

  // Forecast + activity
  root.append(h('div', { class: 'grid cols-2' },
    card('Expected sales — next 7 days', data.upcoming_forecast.length ? h('div', null,
      table([
        { label: 'Product', render: (f) => f.product_name },
        { label: 'Units', align: 'right', render: (f) => num(f.predicted_quantity, 0) },
        { label: 'Likely range', align: 'right', render: (f) => `${num(f.confidence_lower, 0)}–${num(f.confidence_upper, 0)}` },
        { label: 'Revenue', align: 'right', render: (f) => money(f.predicted_revenue) },
      ], data.upcoming_forecast),
      h('div', { class: 'card-body muted small' }, 'A simple estimate from the average of the last 28 days of sales — not a guarantee.')) : empty('Not enough sales history yet', 'Forecasts appear after a few days of sales.')),
    card('Recent activity', data.recent_activity.length ? h('ul', { class: 'list' }, data.recent_activity.map((a) => h('li', null,
      h('div', null, h('div', { class: 'title' }, a.title), h('div', { class: 'muted small' }, a.subtitle)),
      h('div', { class: 'muted small nowrap' }, fmtDateTime(a.timestamp))))) : empty('No activity yet')),
  ));
  return root;
}

function suggestionList(items) {
  const list = h('ul', { class: 'list' });
  items.forEach((rec) => {
    const li = h('li', null,
      h('div', null,
        h('div', { class: 'title' }, rec.title, ' ', rec.priority === 'CRITICAL' ? badge('Urgent', 'danger') : rec.priority === 'WARNING' ? badge('Warning', 'warning') : badge('Info', 'info')),
        h('div', { class: 'muted small' }, rec.body)),
      h('div', { class: 'row-actions', style: { alignItems: 'flex-start' } },
        h('button', { class: 'btn btn-sm', onclick: () => resolve(rec, 'ACCEPTED', li) }, 'Done'),
        h('button', { class: 'btn btn-sm btn-ghost', onclick: () => resolve(rec, 'DISMISSED', li) }, 'Dismiss')));
    list.append(li);
  });
  return list;
}

async function resolve(rec, action, li) {
  try {
    await Api.post(`/recommendations/${rec.id}/resolve/`, { action });
    li.remove();
  } catch (e) { toastError(e); }
}

export async function render(container, ctx) {
  const body = h('div');
  const load = async () => {
    replaceContent(body, spinner());
    try {
      const [data, trend] = await Promise.all([Api.get('/dashboard/'), Api.get('/analytics/sales-trend/', { days: 30 })]);
      replaceContent(body, view(data, trend, ctx));
    } catch (e) {
      replaceContent(body, errorBox(e.message));
    }
  };
  container.append(
    pageHead('Dashboard', `${state.store.name} — ${new Date().toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' })}`,
      h('button', { class: 'btn', onclick: load }, icon('refresh'), 'Refresh'),
      can('sell') ? h('a', { class: 'btn btn-primary', href: '#/pos', style: { textDecoration: 'none' } }, icon('cart'), 'New sale') : null),
    body);
  await load();
}
