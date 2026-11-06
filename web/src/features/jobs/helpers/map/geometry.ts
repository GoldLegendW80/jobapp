/**
 * The maths of the map, ported from the Map view of engine/dashboard.html: the Natural Earth
 * projection on a 1000 by 520 plane, the land, sphere and graticule paths, the region boxes, the
 * pin sizes, plus what the engine did not have: zoom about a point, pan, and clamping, all on an
 * SVG viewBox [x, y, width, height] that keeps the plane's aspect ratio.
 *
 * The land outlines (world.json) are Natural Earth 110m land, public domain
 * (https://www.naturalearthdata.com/about/terms-of-use/), as bundled by the engine: a list of
 * polygons, each a list of rings, each a flat [lon, lat, lon, lat, ...] list.
 */
import type { JobStatus } from "../labels";

export const W = 1000;
export const H = 520;
const S = W / (2 * Math.PI * 0.8707);

export type Point = readonly [number, number];
/** [x, y, width, height] in plane units; width / height is always W / H. */
export type ViewBox = readonly [number, number, number, number];
export type World = number[][][];

export const FULL: ViewBox = [0, 0, W, H];
/** The deepest zoom: a sixteenth of the world's width. */
export const MAX_ZOOM = 16;

/** Natural Earth (Šavrič 2011) in the polynomial form d3 uses, as the engine draws it. */
export function project(lon: number, lat: number): Point {
  const l = (lon * Math.PI) / 180;
  const p = (lat * Math.PI) / 180;
  const p2 = p * p;
  const p4 = p2 * p2;
  return [
    W / 2 +
      S *
        l *
        (0.8707 -
          0.131979 * p2 +
          p4 * (-0.013791 + p4 * (0.003971 * p2 - 0.001529 * p4))),
    H / 2 -
      S *
        p *
        (1.007226 +
          p2 * (0.015085 + p4 * (-0.044475 + 0.028874 * p2 - 0.005916 * p4))),
  ];
}

const pt = (lon: number, lat: number) =>
  project(lon, lat)
    .map((v) => v.toFixed(1))
    .join(" ");
const range = (a: number, b: number, step: number) =>
  Array.from(
    { length: Math.round((b - a) / step) + 1 },
    (_, i) => a + i * step,
  );
const line = (points: Array<[number, number]>) =>
  "M" + points.map(([lon, lat]) => pt(lon, lat)).join("L");

/** The outline of the whole globe. */
export function spherePath(): string {
  return (
    line(
      range(-90, 90, 2)
        .map((lat): [number, number] => [-180, lat])
        .concat(range(90, -90, -2).map((lat): [number, number] => [180, lat])),
    ) + "Z"
  );
}

/** Meridians every 30 degrees, parallels every 30 degrees up to 60. */
export function graticulePath(): string {
  return range(-150, 150, 30)
    .map((lon) => line(range(-90, 90, 2).map((lat) => [lon, lat])))
    .concat(
      range(-60, 60, 30).map((lat) =>
        line(range(-180, 180, 2).map((lon) => [lon, lat])),
      ),
    )
    .join("");
}

/** Every land ring as one path (draw with fill-rule evenodd, as the engine does). */
export function landPath(world: World): string {
  let d = "";
  for (const polygon of world)
    for (const ring of polygon) {
      for (let i = 0; i + 1 < ring.length; i += 2)
        d += (i ? "L" : "M") + pt(ring[i] ?? 0, ring[i + 1] ?? 0);
      if (ring.length >= 2) d += "Z";
    }
  return d;
}

export const REGIONS = {
  world: { label: "World", bounds: null },
  europe: { label: "Europe", bounds: [-12, 34, 34, 62] },
  na: { label: "North America", bounds: [-128, 22, -60, 54] },
  apac: { label: "Asia Pacific", bounds: [68, -46, 156, 46] },
} as const satisfies Record<
  string,
  { label: string; bounds: readonly [number, number, number, number] | null }
>;
export type Region = keyof typeof REGIONS;

/** The engine's region box: the projected bounds, widened to the plane's aspect ratio. */
export function regionBox(region: Region): ViewBox {
  const bounds = REGIONS[region].bounds;
  if (!bounds) return FULL;
  const [a, b, c, d] = bounds;
  const m = [(a + c) / 2, (b + d) / 2] as const;
  const pts = (
    [
      [a, b],
      [c, b],
      [a, d],
      [c, d],
      [m[0], b],
      [m[0], d],
      [a, m[1]],
      [c, m[1]],
    ] as const
  ).map(([lon, lat]) => project(lon, lat));
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const y0 = Math.min(...ys);
  const y1 = Math.max(...ys);
  let w = x1 - x0;
  let h = y1 - y0;
  if (w / h < W / H) w = (h * W) / H;
  else h = (w * H) / W;
  return [(x0 + x1 - w) / 2, (y0 + y1 - h) / 2, w, h];
}

const clampNum = (v: number, lo: number, hi: number) =>
  Math.min(hi, Math.max(lo, v));

/** A viewBox inside the plane, between the whole world and MAX_ZOOM, aspect kept. */
export function clampView([x, y, w]: ViewBox): ViewBox {
  const width = clampNum(Number.isFinite(w) ? w : W, W / MAX_ZOOM, W);
  const height = (width * H) / W;
  return [
    clampNum(Number.isFinite(x) ? x : 0, 0, W - width),
    clampNum(Number.isFinite(y) ? y : 0, 0, H - height),
    width,
    height,
  ];
}

/**
 * Zoom by `factor` (2 doubles the magnification) keeping the plane point under (fx, fy) in
 * place; fx and fy are fractions of the view (0.5, 0.5 is its centre).
 */
export function zoomAt(
  view: ViewBox,
  factor: number,
  fx = 0.5,
  fy = 0.5,
): ViewBox {
  const [x, y, w, h] = view;
  const px = x + fx * w;
  const py = y + fy * h;
  const width = clampNum(w / factor, W / MAX_ZOOM, W);
  const height = (width * H) / W;
  return clampView([px - fx * width, py - fy * height, width, height]);
}

/** Move the view by (dx, dy) fractions of its own size (0.1 is a tenth of the view). */
export function panBy(view: ViewBox, dx: number, dy: number): ViewBox {
  const [x, y, w, h] = view;
  return clampView([x + dx * w, y + dy * h, w, h]);
}

export interface ScreenPoint {
  x: number;
  y: number;
}

/**
 * Two fingers moved from (a0, b0) to (a1, b1), in px relative to a frame of width by height:
 * zoom by the change of their distance about their new midpoint. Unchanged when a distance is 0.
 */
export function pinchZoom(
  view: ViewBox,
  [a0, b0]: readonly [ScreenPoint, ScreenPoint],
  [a1, b1]: readonly [ScreenPoint, ScreenPoint],
  width: number,
  height: number,
): ViewBox {
  const d0 = Math.hypot(a0.x - b0.x, a0.y - b0.y);
  const d1 = Math.hypot(a1.x - b1.x, a1.y - b1.y);
  if (!d0 || !d1 || !width || !height) return view;
  return zoomAt(
    view,
    d1 / d0,
    (a1.x + b1.x) / 2 / width,
    (a1.y + b1.y) / 2 / height,
  );
}

/** The magnification of a view: 1 for the whole world. */
export const zoomLevel = (view: ViewBox) => W / view[2];

/** Where a plane point sits in the view, as fractions (outside 0 to 1 when out of view). */
export function toView(view: ViewBox, [px, py]: Point): Point {
  return [(px - view[0]) / view[2], (py - view[1]) / view[3]];
}

/** The engine's pin radius in screen pixels for a view the map draws `scale` px per unit. */
export function pinRadius(count: number, scale = 1): number {
  const r = (2.5 + 1.9 * Math.sqrt(Math.max(1, count))) * Math.max(0.75, scale);
  return Math.min(r, 26);
}

/** Who wins a tie: the furthest open step first, the closed outcomes last. */
const TIE_ORDER: readonly JobStatus[] = [
  "interview",
  "applied",
  "staged",
  "cv_ready",
  "shortlisted",
  "found",
  "rejected",
  "closed",
  "skipped",
];

/**
 * The status most postings of a place have. A tie goes to the posting that went further (an
 * interview over an application over a shortlist), with rejected, closed and skipped last.
 * Null when there is none.
 */
export function dominantStatus(
  counts: Partial<Record<string, number>>,
): JobStatus | null {
  let best: JobStatus | null = null;
  let most = 0;
  for (const status of TIE_ORDER) {
    const n = counts[status] ?? 0;
    if (n > most) {
      best = status;
      most = n;
    }
  }
  return best;
}

export interface LabelBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}
const hit = (a: LabelBox, b: LabelBox) =>
  a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0;

/**
 * The engine's label placement, in screen pixels: biggest pins first, right of the pin, else
 * left of it, else no label; a label never covers another label or a pin with several postings.
 * Pins with one posting get a label only when zoomed in (zoom above 1.5).
 */
export function placeLabels(
  pins: ReadonlyArray<{
    name: string;
    x: number;
    y: number;
    r: number;
    count: number;
  }>,
  zoom: number,
  charWidth = 6.3,
): Map<string, 1 | -1> {
  const boxes: LabelBox[] = [];
  const pinBox = (p: (typeof pins)[number]): LabelBox => ({
    x0: p.x - p.r,
    y0: p.y - p.r,
    x1: p.x + p.r,
    y1: p.y + p.r,
  });
  const sides = new Map<string, 1 | -1>();
  for (const pin of pins) {
    if (!(zoom > 1.5 || pin.count > 1)) continue;
    for (const side of [1, -1] as const) {
      const w = charWidth * pin.name.length;
      const lx = pin.x + side * (pin.r + 3);
      const box: LabelBox = {
        x0: side > 0 ? lx : lx - w,
        y0: pin.y - 6.5,
        x1: side > 0 ? lx + w : lx,
        y1: pin.y + 6.5,
      };
      if (
        boxes.some((other) => hit(box, other)) ||
        pins.some((o) => o !== pin && o.count > 1 && hit(box, pinBox(o)))
      )
        continue;
      boxes.push(box, pinBox(pin));
      sides.set(pin.name, side);
      break;
    }
  }
  return sides;
}
