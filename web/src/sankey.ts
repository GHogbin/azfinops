/** Layout for a left-to-right Sankey of cost paths (2 or 3 levels). Pure, so it can be tested without a browser. */

export const OTHER = "__other__";

export interface FlowPathIn { keys: (string | null)[]; value: number }
export interface SNode { level: number; key: string; value: number; y: number; h: number }
export interface SLink { level: number; from: SNode; to: SNode; group: string; value: number; sy: number; ty: number; w: number }

const keyOf = (k: string | null) => (k === null ? "(none)" : k);

/** Largest first, with Other always last in its column. */
function order(a: SNode, b: SNode): number {
  if (a.key === OTHER) return 1;
  if (b.key === OTHER) return -1;
  return b.value - a.value || a.key.localeCompare(b.key);
}

/**
 * Nodes stacked per column with `pad` between them, heights proportional to value on one shared scale, and bands
 * between neighbouring columns. Bands are grouped by the first level's key (`group`) so they can be coloured by it.
 */
export function layoutSankey(paths: FlowPathIn[], height: number, pad = 10): { nodes: SNode[][]; links: SLink[]; scale: number } {
  const levels = paths[0]?.keys.length ?? 0;
  const cols: Map<string, SNode>[] = Array.from({ length: levels }, () => new Map());
  for (const p of paths) {
    p.keys.forEach((k, i) => {
      const key = keyOf(k);
      const n = cols[i]!.get(key) ?? { level: i, key, value: 0, y: 0, h: 0 };
      n.value += p.value;
      cols[i]!.set(key, n);
    });
  }
  const nodes = cols.map((c) => [...c.values()].sort(order));
  const total = paths.reduce((a, p) => a + p.value, 0);
  const scale = total <= 0 ? 0 : Math.min(...nodes.map((col) => Math.max(1, height - pad * (col.length - 1)) / total));
  for (const col of nodes) {
    let y = 0;
    for (const n of col) { n.h = n.value * scale; n.y = y; y += n.h + pad; }
  }
  const find = (i: number, k: string) => cols[i]!.get(k)!;
  const groups = nodes[0]?.map((n) => n.key) ?? [];
  const links: SLink[] = [];
  for (let i = 0; i + 1 < levels; i++) {
    const agg = new Map<string, SLink>();
    for (const p of paths) {
      const from = find(i, keyOf(p.keys[i] ?? null));
      const to = find(i + 1, keyOf(p.keys[i + 1] ?? null));
      const group = keyOf(p.keys[0] ?? null);
      const id = `${from.key}\u0000${to.key}\u0000${group}`;
      const l = agg.get(id) ?? { level: i, from, to, group, value: 0, sy: 0, ty: 0, w: 0 };
      l.value += p.value;
      agg.set(id, l);
    }
    const ls = [...agg.values()];
    const gi = (g: string) => groups.indexOf(g);
    // Leave each node towards lower targets first, and arrive from higher sources first, so bands cross as little as possible.
    for (const n of nodes[i]!) {
      let y = n.y;
      for (const l of ls.filter((x) => x.from === n).sort((a, b) => a.to.y - b.to.y || gi(a.group) - gi(b.group))) {
        l.w = l.value * scale; l.sy = y; y += l.w;
      }
    }
    for (const n of nodes[i + 1]!) {
      let y = n.y;
      for (const l of ls.filter((x) => x.to === n).sort((a, b) => a.from.y - b.from.y || gi(a.group) - gi(b.group))) {
        l.ty = y; y += l.w;
      }
    }
    links.push(...ls);
  }
  return { nodes, links, scale };
}

/** SVG path for a band of width w from (x0, sy) to (x1, ty). */
export function bandPath(x0: number, sy: number, x1: number, ty: number, w: number): string {
  const xm = (x0 + x1) / 2;
  return `M${x0},${sy}C${xm},${sy} ${xm},${ty} ${x1},${ty}L${x1},${ty + w}C${xm},${ty + w} ${xm},${sy + w} ${x0},${sy + w}Z`;
}
