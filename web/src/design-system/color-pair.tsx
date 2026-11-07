import { useCallback, useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

/** Any CSS color (oklch, color-mix, translucent) as sRGB, painted over its backdrop like the browser does. */
function toRgb(color: string, backdrop = "#fff") {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 1;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) return null;
  context.fillStyle = backdrop;
  context.fillRect(0, 0, 1, 1);
  context.fillStyle = color;
  context.fillRect(0, 0, 1, 1);
  const [r = 0, g = 0, b = 0] = context.getImageData(0, 0, 1, 1).data;
  return [r, g, b];
}

const toHex = (rgb: number[]) =>
  `#${rgb.map((value) => value.toString(16).padStart(2, "0")).join("")}`;

function luminance(rgb: number[]) {
  const [r = 0, g = 0, b = 0] = rgb.map((value) => {
    const channel = value / 255;
    return channel <= 0.03928
      ? channel / 12.92
      : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: number[], b: number[]) {
  const [light = 0, dark = 0] = [luminance(a), luminance(b)].sort(
    (x, y) => y - x,
  );
  return (light + 0.05) / (dark + 0.05);
}

const level = (ratio: number) =>
  ratio >= 7 ? "AAA" : ratio >= 4.5 ? "AA" : ratio >= 3 ? "AA large" : "Fail";

/** Calls `measure` now and again whenever Storybook's toolbar switches the theme on <html>. */
function useOnThemeChange(measure: () => void) {
  useEffect(() => {
    measure();
    const observer = new MutationObserver(measure);
    observer.observe(document.documentElement, { attributes: true });
    return () => {
      observer.disconnect();
    };
  }, [measure]);
}

/** A surface token with its foreground token on it, and their measured contrast. */
export function ColorPair({
  name,
  use,
  background,
  foreground,
}: {
  name: string;
  use: string;
  background: string;
  foreground: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [info, setInfo] = useState<{ ratio: number; hex: string } | null>(null);
  const measure = useCallback(() => {
    const element = ref.current;
    if (!element) return;
    const style = getComputedStyle(element);
    const page = getComputedStyle(document.body).backgroundColor;
    const bg = toRgb(style.backgroundColor, page);
    const fg = bg && toRgb(style.color, toHex(bg));
    if (bg && fg) setInfo({ ratio: contrast(bg, fg), hex: toHex(bg) });
  }, []);
  useOnThemeChange(measure);

  return (
    <figure className="flex flex-col overflow-hidden rounded-lg border bg-card">
      <div
        ref={ref}
        style={{
          backgroundColor: `var(--${background})`,
          color: `var(--${foreground})`,
        }}
        className="flex h-24 items-end justify-between gap-2 p-4"
      >
        <span className="text-2xl font-semibold">Aa</span>
        {info && (
          <span
            className={cn(
              "rounded-full border border-current/25 px-2 py-0.5 text-xs tabular-nums",
              info.ratio < 4.5 && "font-semibold",
            )}
          >
            {info.ratio.toFixed(2)} {level(info.ratio)}
          </span>
        )}
      </div>
      <figcaption className="flex flex-col gap-1 bg-card p-3 text-card-foreground">
        <span className="text-sm font-medium">{name}</span>
        <span className="text-xs text-muted-foreground">{use}</span>
        <code className="text-xs text-muted-foreground">
          --{background} / --{foreground}
          {info ? ` ${info.hex}` : ""}
        </code>
      </figcaption>
    </figure>
  );
}

/** One token as a swatch, with its resolved hex value. */
export function Swatch({ token, use }: { token: string; use: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [hex, setHex] = useState("");
  const measure = useCallback(() => {
    const element = ref.current;
    if (!element) return;
    const page = getComputedStyle(document.body).backgroundColor;
    const rgb = toRgb(getComputedStyle(element).backgroundColor, page);
    if (rgb) setHex(toHex(rgb));
  }, []);
  useOnThemeChange(measure);

  return (
    <figure className="flex items-center gap-3">
      <div
        ref={ref}
        style={{ backgroundColor: `var(--${token})` }}
        className="size-12 shrink-0 rounded-md border"
      />
      <figcaption className="flex min-w-0 flex-col gap-0.5">
        <code className="text-sm">--{token}</code>
        <span className="text-xs text-muted-foreground">
          {use}
          {hex ? `, ${hex}` : ""}
        </span>
      </figcaption>
    </figure>
  );
}
