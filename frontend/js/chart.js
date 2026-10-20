// Minimal SVG bar chart (no libraries). data: [{label, value, title, highlight}]
const NS = 'http://www.w3.org/2000/svg';

function svg(tag, attrs, text) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  if (text !== undefined) el.textContent = text;
  return el;
}

export function compact(n) {
  const v = Number(n) || 0;
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(1).replace(/\.0$/, '')}M`;
  if (Math.abs(v) >= 1e3) return `${(v / 1e3).toFixed(Math.abs(v) >= 1e4 ? 0 : 1).replace(/\.0$/, '')}k`;
  return String(Math.round(v));
}

function niceMax(max) {
  if (max <= 0) return 1;
  const pow = Math.pow(10, Math.floor(Math.log10(max)));
  const n = max / pow;
  const nice = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
  return nice * pow;
}

export function barChart(data, { labelEvery = 5 } = {}) {
  const W = 800, H = 240, left = 46, right = 8, top = 10, bottom = 26;
  const innerW = W - left - right, innerH = H - top - bottom;
  const chart = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'chart', role: 'img', preserveAspectRatio: 'none' });
  const yMax = niceMax(Math.max(0, ...data.map((d) => d.value)));

  for (let i = 0; i <= 4; i++) {
    const y = top + innerH - (innerH * i) / 4;
    chart.append(svg('line', { x1: left, x2: W - right, y1: y, y2: y, class: 'grid-line' }));
    chart.append(svg('text', { x: left - 6, y: y + 4, 'text-anchor': 'end' }, compact((yMax * i) / 4)));
  }

  const slot = innerW / Math.max(1, data.length);
  const barW = Math.max(2, slot * 0.68);
  data.forEach((d, i) => {
    const h = yMax ? (d.value / yMax) * innerH : 0;
    const x = left + i * slot + (slot - barW) / 2;
    const bar = svg('rect', {
      x, y: top + innerH - h, width: barW, height: Math.max(h, d.value > 0 ? 1 : 0), rx: 2,
      class: `bar${d.highlight ? ' event' : ''}`,
    });
    bar.append(svg('title', {}, d.title || `${d.label}: ${d.value}`));
    chart.append(bar);
    const last = data.length - 1;
    if (i === last || (i % labelEvery === 0 && last - i >= Math.ceil(labelEvery * 0.6))) {
      chart.append(svg('text', { x: x + barW / 2, y: H - 8, 'text-anchor': 'middle' }, d.label));
    }
  });
  return chart;
}
