import { describe, expect, it } from "vitest";

import {
  clampView,
  dominantStatus,
  FULL,
  H,
  landPath,
  MAX_ZOOM,
  panBy,
  pinchZoom,
  pinRadius,
  placeLabels,
  project,
  regionBox,
  spherePath,
  toView,
  W,
  zoomAt,
  zoomLevel,
  type ViewBox,
} from "./geometry";

const close = (a: readonly number[], b: readonly number[], digits = 6) => {
  expect(a).toHaveLength(b.length);
  a.forEach((v, i) => {
    expect(v).toBeCloseTo(b[i] ?? Number.NaN, digits);
  });
};

describe("projection (the engine's Natural Earth)", () => {
  it("puts the origin at the centre and the antimeridian at the edges", () => {
    close(project(0, 0), [W / 2, H / 2]);
    close(project(180, 0), [W, H / 2]);
    close(project(-180, 0), [0, H / 2]);
  });

  it("is symmetric about the equator and the prime meridian", () => {
    const [x, y] = project(37, 52);
    close(project(-37, -52), [W - x, H - y]);
  });

  it("gives the engine's coordinates for Paris and Sydney", () => {
    // Computed once with engine/dashboard.html proj().
    close(project(2.35, 48.86), [505.757905, 102.881476], 5);
    close(project(151.21, -33.87), [896.975574, 369.24335], 5);
  });

  it("keeps the poles inside the plane", () => {
    const [, north] = project(0, 90);
    const [, south] = project(0, -90);
    expect(north).toBeGreaterThan(0);
    expect(south).toBeLessThan(H);
    expect(north + south).toBeCloseTo(H, 6);
  });
});

describe("paths", () => {
  it("draws every ring of every polygon, closed", () => {
    const d = landPath([[[0, 0, 10, 0, 10, 10]], [[20, 20, 30, 20], []]]);
    expect(d.match(/M/g)).toHaveLength(2);
    expect(d.match(/Z/g)).toHaveLength(2);
    expect(
      d.startsWith(
        `M${project(0, 0)
          .map((v) => v.toFixed(1))
          .join(" ")}`,
      ),
    ).toBe(true);
  });

  it("draws nothing for no land, and ignores a dangling coordinate", () => {
    expect(landPath([])).toBe("");
    expect(landPath([[[5]]])).toBe("");
  });

  it("closes the sphere", () => {
    expect(spherePath().endsWith("Z")).toBe(true);
  });
});

describe("regions", () => {
  it("is the whole plane for the world", () => {
    expect(regionBox("world")).toEqual(FULL);
  });

  it.each(["europe", "na", "apac"] as const)(
    "keeps the plane's aspect ratio and holds its centre for %s",
    (region) => {
      const [x, y, w, h] = regionBox(region);
      expect(w / h).toBeCloseTo(W / H, 6);
      expect(w).toBeLessThan(W);
      expect(x).toBeGreaterThanOrEqual(0);
      expect(y).toBeGreaterThanOrEqual(0);
    },
  );

  it("shows Paris in Europe and Sydney in Asia Pacific", () => {
    const inside = (view: ViewBox, lon: number, lat: number) => {
      const [fx, fy] = toView(view, project(lon, lat));
      return fx > 0 && fx < 1 && fy > 0 && fy < 1;
    };
    expect(inside(regionBox("europe"), 2.35, 48.86)).toBe(true);
    expect(inside(regionBox("europe"), 151.21, -33.87)).toBe(false);
    expect(inside(regionBox("apac"), 151.21, -33.87)).toBe(true);
    expect(inside(regionBox("na"), -74, 40.7)).toBe(true);
  });
});

describe("zoom and pan", () => {
  it("zooms about the centre by default", () => {
    const view = zoomAt(FULL, 2);
    close(view, [W / 4, H / 4, W / 2, H / 2]);
    expect(zoomLevel(view)).toBe(2);
  });

  it("keeps the point under the pointer in place", () => {
    const start: ViewBox = [100, 50, 500, 260];
    const fx = 0.2;
    const fy = 0.7;
    const before = [start[0] + fx * start[2], start[1] + fy * start[3]];
    const view = zoomAt(start, 1.5, fx, fy);
    close([view[0] + fx * view[2], view[1] + fy * view[3]], before);
  });

  it("never zooms out past the world nor in past the limit", () => {
    expect(zoomAt(FULL, 0.25)).toEqual(FULL);
    const deep = zoomAt(FULL, 1000);
    expect(deep[2]).toBeCloseTo(W / MAX_ZOOM, 6);
    expect(zoomLevel(deep)).toBeCloseTo(MAX_ZOOM, 6);
  });

  it("pans by fractions of the view and stops at the edges", () => {
    const view = zoomAt(FULL, 2);
    close(panBy(view, 0.1, 0), [W / 4 + 50, H / 4, W / 2, H / 2]);
    close(panBy(view, -5, -5), [0, 0, W / 2, H / 2]);
    close(panBy(view, 5, 5), [W / 2, H / 2, W / 2, H / 2]);
    // The whole world cannot pan.
    expect(panBy(FULL, 0.3, -0.3)).toEqual(FULL);
  });

  it("repairs a view that is not finite or out of range", () => {
    expect(clampView([Number.NaN, Number.NaN, Number.NaN, 1])).toEqual(FULL);
    expect(clampView([-50, 900, 2 * W, 0])).toEqual(FULL);
    const [, , w, h] = clampView([0, 0, 1, 1]);
    expect(w).toBeCloseTo(W / MAX_ZOOM, 6);
    expect(h).toBeCloseTo(H / MAX_ZOOM, 6);
  });

  it("pinches: fingers twice as far apart zoom in twice, about their midpoint", () => {
    const view = pinchZoom(
      FULL,
      [
        { x: 450, y: 260 },
        { x: 550, y: 260 },
      ],
      [
        { x: 400, y: 260 },
        { x: 600, y: 260 },
      ],
      W,
      H,
    );
    close(view, [W / 4, H / 4, W / 2, H / 2]);
  });

  it("ignores a pinch with fingers on one spot or a frame of no size", () => {
    const view: ViewBox = [100, 50, 500, 260];
    const same = { x: 10, y: 10 };
    expect(pinchZoom(view, [same, same], [same, { x: 50, y: 50 }], W, H)).toBe(
      view,
    );
    expect(
      pinchZoom(view, [same, { x: 20, y: 20 }], [same, { x: 50, y: 50 }], 0, 0),
    ).toBe(view);
  });
});

describe("pins", () => {
  it("grow with the square root of the postings, from one posting up to a cap", () => {
    expect(pinRadius(0)).toBe(pinRadius(1));
    expect(pinRadius(1)).toBeCloseTo(4.4, 6);
    expect(pinRadius(4)).toBeCloseTo(6.3, 6);
    expect(pinRadius(9)).toBeGreaterThan(pinRadius(4));
    expect(pinRadius(100_000)).toBe(26);
  });

  it("scale with the drawn width, never below three quarters", () => {
    expect(pinRadius(4, 2)).toBeCloseTo(12.6, 6);
    expect(pinRadius(4, 0.1)).toBeCloseTo(6.3 * 0.75, 6);
  });

  it("take the colour of the status most postings have, ties to the furthest open step", () => {
    expect(dominantStatus({ found: 3, applied: 1 })).toBe("found");
    expect(dominantStatus({ found: 2, applied: 2 })).toBe("applied");
    expect(dominantStatus({ skipped: 1, interview: 1 })).toBe("interview");
    expect(dominantStatus({ shortlisted: 1, skipped: 1 })).toBe("shortlisted");
    expect(dominantStatus({ rejected: 2, closed: 2 })).toBe("rejected");
    expect(dominantStatus({})).toBeNull();
    expect(dominantStatus({ found: 0, on_hold: 5 })).toBeNull();
  });
});

describe("labels", () => {
  const pin = (name: string, x: number, y: number, count: number) => ({
    name,
    x,
    y,
    r: 6,
    count,
  });

  it("go right of the pin, else left, else nowhere", () => {
    const pins = [
      pin("Paris", 100, 100, 5),
      pin("Lyon", 130, 100, 4), // right of Paris: Paris's label would cover Lyon's pin
      pin("Nice", 300, 300, 3),
    ];
    const sides = placeLabels(pins, 1);
    expect(sides.get("Nice")).toBe(1);
    expect(sides.get("Paris")).toBe(-1);
    expect(sides.has("Lyon")).toBe(true);
  });

  it("skip single postings at the world view and show them zoomed in", () => {
    const pins = [pin("Sete", 100, 100, 1)];
    expect(placeLabels(pins, 1).size).toBe(0);
    expect(placeLabels(pins, 2).get("Sete")).toBe(1);
  });

  it("leave out a label with no room on either side", () => {
    const pins = [
      pin("Left", 50, 100, 3),
      pin("Middle", 100, 100, 3),
      pin("Right", 150, 100, 3),
    ];
    const sides = placeLabels(pins, 1);
    expect(sides.has("Middle")).toBe(false);
  });
});
