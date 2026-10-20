import { h, num, badge, pageHead, fmtDateTime, opt } from '../ui.js';
import { listView } from '../listview.js';

export async function render(container, ctx) {
  const product = ctx.query.get('product');
  const lv = listView({
    path: '/stock-movements/', searchPlaceholder: 'Search product or note…',
    params: () => (product ? { product } : {}),
    filters: [{ name: 'type', label: 'Type', options: [
      opt('', 'All movements'), opt('SALE', 'Sales'), opt('SALE_VOID', 'Voided sales'), opt('PURCHASE', 'Purchases received'),
      opt('ADJUSTMENT', 'Corrections'), opt('COUNT', 'Stock counts'), opt('DAMAGE', 'Damaged'), opt('EXPIRED', 'Expired'), opt('OPENING', 'Opening stock'),
    ] }],
    columns: [
      { label: 'When', render: (m) => fmtDateTime(m.created_at), class: 'nowrap' },
      { label: 'Product', render: (m) => h('div', null, h('div', { class: 'title' }, m.product_name), h('div', { class: 'muted small' }, m.sku)) },
      { label: 'Type', render: (m) => badge(m.movement_label, m.quantity_change < 0 ? 'warning' : 'info') },
      { label: 'Change', align: 'right', render: (m) => h('strong', { class: m.quantity_change < 0 ? 'delta-down' : 'delta-up' }, `${m.quantity_change > 0 ? '+' : ''}${num(m.quantity_change)}`) },
      { label: 'Stock after', align: 'right', render: (m) => `${num(m.stock_before)} → ${num(m.stock_after)}` },
      { label: 'Note', render: (m) => h('span', { class: 'muted' }, [m.note, m.created_by_name ? `by ${m.created_by_name}` : ''].filter(Boolean).join(' · ')) },
    ],
  });
  container.append(
    pageHead('Stock history', product ? 'Movements for one product.' : 'Every change to your stock — sales, deliveries, corrections.',
      product ? h('a', { class: 'btn', href: '#/stock', style: { textDecoration: 'none' } }, 'Show all products') : null),
    lv.el);
}
