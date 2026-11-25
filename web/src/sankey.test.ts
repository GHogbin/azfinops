import { describe, expect, it } from "vitest";
import { layoutSankey, OTHER } from "./sankey";

const paths = [
  { keys: ["A", "VM", "app1"], value: 80 }, { keys: ["A", "VM", "app2"], value: 50 },
  { keys: ["A", "SQL", "app1"], value: 40 }, { keys: ["B", "VM", OTHER], value: 30 },
];

describe("sankey layout", () => {
  it("gives every column the same total height and keeps flows balanced", () => {
    const { nodes, links, scale } = layoutSankey(paths, 300, 10);
    expect(nodes.map((c) => c.map((n) => n.key))).toEqual([["A", "B"], ["VM", "SQL"], ["app1", "app2", OTHER]]);
    // the column with three nodes has two gaps: (300 - 20) / 200
    expect(scale).toBeCloseTo(1.4);
    for (const col of nodes) expect(col.reduce((a, n) => a + n.h, 0)).toBeCloseTo(200 * 1.4);
    const vm = nodes[1]![0]!;
    const into = links.filter((l) => l.to === vm).reduce((a, l) => a + l.value, 0);
    const out = links.filter((l) => l.from === vm).reduce((a, l) => a + l.value, 0);
    expect(into).toBe(160);
    expect(out).toBe(160);
  });
  it("stacks bands inside their nodes without overlap", () => {
    const { nodes, links } = layoutSankey(paths, 300, 10);
    for (const n of nodes.flat()) {
      const outs = links.filter((l) => l.from === n).sort((a, b) => a.sy - b.sy);
      if (outs.length) {
        expect(outs[0]!.sy).toBeCloseTo(n.y);
        expect(outs.reduce((a, l) => a + l.w, 0)).toBeCloseTo(n.h);
      }
    }
  });
  it("puts Other last and colours bands by the first level", () => {
    const { nodes, links } = layoutSankey([{ keys: [OTHER, "x"], value: 100 }, { keys: ["A", "x"], value: 1 }], 100);
    expect(nodes[0]!.map((n) => n.key)).toEqual(["A", OTHER]);
    expect(new Set(links.map((l) => l.group))).toEqual(new Set(["A", OTHER]));
  });
});
