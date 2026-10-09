import { Minus, Plus, RotateCcw } from "lucide-react";
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { STATUS_LABELS, STATUSES, type JobStatus } from "../labels";
import {
  dominantStatus,
  FULL,
  graticulePath,
  H,
  landPath,
  panBy,
  pinchZoom,
  pinRadius,
  placeLabels,
  project,
  REGIONS,
  regionBox,
  spherePath,
  toView,
  W,
  zoomAt,
  zoomLevel,
  type Region,
  type ScreenPoint,
  type ViewBox,
  type World,
} from "./geometry";
import type { Place } from "./places-api";
import { describePlace, plural, STATUS_FILL } from "./format";

const SPHERE = spherePath();
const GRATICULE = graticulePath();
const ZOOM_STEP = 1.5;
const PAN_STEP = 0.15;

/** The land outlines, loaded in their own chunk the first time a map is shown. */
function useWorld(): World | null {
  const [world, setWorld] = useState<World | null>(null);
  useEffect(() => {
    let live = true;
    void import("./world.json").then((module) => {
      if (live) setWorld(module.default);
    });
    return () => {
      live = false;
    };
  }, []);
  return world;
}

/** The width the map is drawn at, in CSS pixels (W before the first measure). */
function useWidth(ref: React.RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(W);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const measure = () => {
      const w = node.getBoundingClientRect().width;
      if (w > 0) setWidth(w);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => {
      observer.disconnect();
    };
  }, [ref]);
  return width;
}

const sameView = (a: ViewBox, b: ViewBox) =>
  a.every((v, i) => Math.abs(v - (b[i] ?? 0)) < 0.5);

/**
 * The world map: land outlines on the engine's Natural Earth projection, one pin per place
 * sized by its postings and coloured by its dominant status. Pins are buttons (Tab reaches them,
 * Enter or a click selects the place, hover or focus shows the tooltip). Zoom and pan: the
 * wheel, a drag, two fingers, the buttons, the region presets, or the keyboard on the focused
 * map (plus and minus zoom, arrows pan, 0 resets).
 */
export function WorldMap({
  places,
  selected,
  onSelect,
}: {
  places: readonly Place[];
  selected: string | null;
  onSelect: (name: string | null) => void;
}) {
  const world = useWorld();
  const land = useMemo(() => (world ? landPath(world) : ""), [world]);
  const frame = useRef<HTMLDivElement>(null);
  const width = useWidth(frame);
  const [view, setView] = useState<ViewBox>(FULL);
  const [tip, setTip] = useState<string | null>(null);
  const tipId = useId();
  const hintId = useId();
  const pointers = useRef(new Map<number, { x: number; y: number }>());

  const zoom = zoomLevel(view);
  const scale = width / W; // CSS px per plane unit at the world view
  const pins = useMemo(
    () =>
      places.map((place) => {
        const [fx, fy] = toView(view, project(place.lon, place.lat));
        const r = pinRadius(place.count, scale);
        return {
          place,
          name: place.name,
          count: place.count,
          fx,
          fy,
          r,
          x: fx * width,
          y: fy * width * (H / W),
          status: dominantStatus(place.counts_by_status),
          inView: fx >= 0 && fx <= 1 && fy >= 0 && fy <= 1,
        };
      }),
    [places, view, scale, width],
  );
  const visible = pins.filter((pin) => pin.inView);
  const labels = useMemo(() => placeLabels(visible, zoom), [visible, zoom]);
  const legend = STATUSES.filter((s) => pins.some((pin) => pin.status === s));
  const tipPin = visible.find((pin) => pin.name === tip);

  // The wheel zooms about the pointer. A native listener: React's is passive.
  useEffect(() => {
    const node = frame.current;
    if (!node) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = node.getBoundingClientRect();
      const fx = rect.width ? (event.clientX - rect.left) / rect.width : 0.5;
      const fy = rect.height ? (event.clientY - rect.top) / rect.height : 0.5;
      const factor = Math.exp(-event.deltaY * 0.0015);
      setView((v) => zoomAt(v, factor, fx, fy));
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      node.removeEventListener("wheel", onWheel);
    };
  }, []);

  const onPointerDown = useCallback((event: PointerEvent<HTMLDivElement>) => {
    if ((event.target as Element).closest("button")) return;
    pointers.current.set(event.pointerId, {
      x: event.clientX,
      y: event.clientY,
    });
    event.currentTarget.setPointerCapture(event.pointerId);
  }, []);

  const onPointerMove = useCallback((event: PointerEvent<HTMLDivElement>) => {
    const points = pointers.current;
    const last = points.get(event.pointerId);
    if (!last) return;
    const rect = event.currentTarget.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const before = [...points.values()];
    points.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const after = [...points.values()];
    const local = (p: ScreenPoint): ScreenPoint => ({
      x: p.x - rect.left,
      y: p.y - rect.top,
    });
    const [a0, b0] = before;
    const [a1, b1] = after;
    if (a1 && !b1) {
      const dx = (event.clientX - last.x) / rect.width;
      const dy = (event.clientY - last.y) / rect.height;
      setView((v) => panBy(v, -dx, -dy));
    } else if (a0 && b0 && a1 && b1 && after.length === 2) {
      // Pinch: the distance between the fingers zooms, about their midpoint.
      setView((v) =>
        pinchZoom(
          v,
          [local(a0), local(b0)],
          [local(a1), local(b1)],
          rect.width,
          rect.height,
        ),
      );
    }
  }, []);

  const onPointerEnd = useCallback((event: PointerEvent<HTMLDivElement>) => {
    pointers.current.delete(event.pointerId);
  }, []);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step: Record<string, () => ViewBox> = {
      "+": () => zoomAt(view, ZOOM_STEP),
      "=": () => zoomAt(view, ZOOM_STEP),
      "-": () => zoomAt(view, 1 / ZOOM_STEP),
      _: () => zoomAt(view, 1 / ZOOM_STEP),
      "0": () => FULL,
      ArrowLeft: () => panBy(view, -PAN_STEP, 0),
      ArrowRight: () => panBy(view, PAN_STEP, 0),
      ArrowUp: () => panBy(view, 0, -PAN_STEP),
      ArrowDown: () => panBy(view, 0, PAN_STEP),
    };
    if (event.key === "Escape") {
      setTip(null);
      return;
    }
    const next = step[event.key];
    if (!next || event.altKey || event.ctrlKey || event.metaKey) return;
    event.preventDefault();
    setView(next());
  };

  return (
    <section
      aria-labelledby={`${hintId}-title`}
      className="flex min-w-0 flex-col overflow-hidden rounded-lg border bg-card"
    >
      <h2 id={`${hintId}-title`} className="sr-only">
        World map
      </h2>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b p-2">
        <div
          role="group"
          aria-label="Region"
          className="inline-flex flex-wrap rounded-md bg-muted p-0.5 text-xs"
        >
          {(Object.keys(REGIONS) as Region[]).map((region) => {
            const pressed = sameView(view, regionBox(region));
            return (
              <button
                key={region}
                type="button"
                aria-pressed={pressed}
                onClick={() => {
                  setView(regionBox(region));
                }}
                className={cn(
                  "rounded px-2 py-1 font-medium text-muted-foreground hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none",
                  pressed && "bg-background text-foreground shadow-sm",
                )}
              >
                {REGIONS[region].label}
              </button>
            );
          })}
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="icon"
            className="size-8"
            aria-label="Zoom in"
            disabled={view[2] <= W / 16 + 0.01}
            onClick={() => {
              setView((v) => zoomAt(v, ZOOM_STEP));
            }}
          >
            <Plus aria-hidden="true" />
          </Button>
          <Button
            variant="outline"
            size="icon"
            className="size-8"
            aria-label="Zoom out"
            disabled={view[2] >= W - 0.01}
            onClick={() => {
              setView((v) => zoomAt(v, 1 / ZOOM_STEP));
            }}
          >
            <Minus aria-hidden="true" />
          </Button>
          <Button
            variant="outline"
            size="icon"
            className="size-8"
            aria-label="Show the whole world"
            disabled={sameView(view, FULL)}
            onClick={() => {
              setView(FULL);
            }}
          >
            <RotateCcw aria-hidden="true" />
          </Button>
        </div>
      </div>

      <div
        ref={frame}
        tabIndex={0}
        role="group"
        aria-label="World map"
        aria-describedby={hintId}
        data-zoom={zoom.toFixed(2)}
        onKeyDown={onKeyDown}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerEnd}
        onPointerCancel={onPointerEnd}
        className="relative aspect-[1000/520] w-full cursor-grab touch-none overflow-hidden outline-none select-none focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset active:cursor-grabbing"
      >
        <p id={hintId} className="sr-only">
          {`${plural(places.length, "place")} on the map. Plus and minus zoom, arrow keys pan, 0 shows the whole world. Tab moves through the pins.`}
        </p>
        <svg
          aria-hidden="true"
          viewBox={view.map((v) => v.toFixed(2)).join(" ")}
          className="absolute inset-0 size-full"
          data-testid="world-svg"
        >
          <path d={SPHERE} className="fill-muted/60" />
          <path
            d={GRATICULE}
            className="fill-none stroke-border"
            strokeWidth={0.6}
            vectorEffect="non-scaling-stroke"
          />
          <path
            d={land}
            fillRule="evenodd"
            className="fill-foreground/10 stroke-foreground/20"
            strokeWidth={0.7}
            vectorEffect="non-scaling-stroke"
            data-testid="land"
          />
        </svg>

        {visible.map((pin) => {
          const isSelected = selected === pin.name;
          return (
            <button
              key={pin.name}
              type="button"
              aria-label={`${pin.name}: ${describePlace(pin.place)}`}
              aria-pressed={isSelected}
              aria-describedby={tip === pin.name ? tipId : undefined}
              data-place={pin.name}
              data-status={pin.status ?? undefined}
              onClick={() => {
                onSelect(isSelected ? null : pin.name);
              }}
              onMouseEnter={() => {
                setTip(pin.name);
              }}
              onMouseLeave={() => {
                setTip((t) => (t === pin.name ? null : t));
              }}
              onFocus={() => {
                setTip(pin.name);
              }}
              onBlur={() => {
                setTip((t) => (t === pin.name ? null : t));
              }}
              style={{
                left: `${pin.fx * 100}%`,
                top: `${pin.fy * 100}%`,
                // The dot plus 3 px: wider targets would hide crowded neighbours (the
                // places list is the full-size alternative). Smaller pins come later, on top.
                width: `${pin.r * 2 + 6}px`,
                height: `${pin.r * 2 + 6}px`,
                zIndex: isSelected ? 3 : 2,
              }}
              className="group absolute grid -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full outline-none focus-visible:ring-[3px] focus-visible:ring-ring"
            >
              <span
                aria-hidden="true"
                style={{ width: `${pin.r * 2}px`, height: `${pin.r * 2}px` }}
                className={cn(
                  "block rounded-full opacity-90 ring-2 ring-card transition-[filter] group-hover:brightness-90",
                  pin.status ? STATUS_FILL[pin.status] : "bg-muted-foreground",
                  isSelected && "opacity-100 ring-foreground",
                )}
              />
            </button>
          );
        })}

        {visible.map((pin) => {
          const side = labels.get(pin.name);
          if (!side) return null;
          return (
            <span
              key={`label-${pin.name}`}
              aria-hidden="true"
              style={{
                left: `${pin.fx * 100}%`,
                top: `${pin.fy * 100}%`,
                transform: `translate(${side > 0 ? `${pin.r + 3}px` : `calc(-100% - ${pin.r + 3}px)`}, -50%)`,
              }}
              className="pointer-events-none absolute z-[1] hidden text-[11px] font-medium whitespace-nowrap text-foreground/80 [text-shadow:0_0_3px_var(--card),0_0_3px_var(--card)] sm:block"
            >
              {pin.name}
            </span>
          );
        })}

        {tipPin ? (
          <div
            id={tipId}
            role="tooltip"
            style={{
              left: `${Math.min(Math.max(tipPin.fx * 100, 12), 88)}%`,
              top: `${tipPin.fy * 100}%`,
              // Above the pin, or below it near the top edge.
              transform:
                tipPin.fy < 0.3
                  ? `translate(-50%, ${tipPin.r + 6}px)`
                  : `translate(-50%, calc(-100% - ${tipPin.r + 6}px))`,
            }}
            className="pointer-events-none absolute z-10 w-max max-w-56 rounded-md border bg-popover px-2.5 py-1.5 text-xs text-popover-foreground shadow-md"
          >
            <p className="font-medium">{tipPin.name}</p>
            <p className="text-muted-foreground">
              {describePlace(tipPin.place)}
            </p>
          </div>
        ) : null}
      </div>

      {legend.length ? (
        <ul
          aria-label="Pin colours: the status most postings of a place have"
          className="flex flex-wrap gap-x-3 gap-y-1 border-t px-3 py-2 text-xs text-muted-foreground"
        >
          {legend.map((status: JobStatus) => (
            <li key={status} className="flex items-center gap-1.5">
              <span
                aria-hidden="true"
                className={cn("size-2.5 rounded-full", STATUS_FILL[status])}
              />
              {STATUS_LABELS[status]}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
