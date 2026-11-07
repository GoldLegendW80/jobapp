/**
 * The swipe gesture of a deck card, as numbers (SPEC-opportunities.md, "The gestures"): when a
 * release is a Like or a Pass, how far the card tilts, how strongly the stamps show. Pure, so the
 * thresholds are tested alone.
 */
import type { Swipe } from "@/features/opportunities/api/deck/use-deck";

/** How far, in pixels, a drag must go for the card to leave; under it the card springs back. */
export const SWIPE_THRESHOLD = 96;
/** A flick this fast (pixels per millisecond) leaves before the threshold... */
export const FLICK_VELOCITY = 0.6;
/** ...once the card has moved at least this far, so a tap that slips is never a swipe. */
export const FLICK_MIN_DISTANCE = 32;
/** The steepest tilt of a dragged card, in degrees. */
export const MAX_TILT = 18;
/** A press that moves less than this and lifts quickly is a tap: it opens the posting. */
export const TAP_DISTANCE = 6;
export const TAP_MS = 300;

/** One pointer position of a drag, with the event's time stamp in milliseconds. */
export interface Sample {
  x: number;
  y: number;
  t: number;
}

/** The window the release velocity is read over: the last tenth of a second of the drag. */
const VELOCITY_WINDOW_MS = 100;
/** Below this span the samples say nothing about speed (two events of one frame). */
const MIN_SPAN_MS = 16;

/** Pixels per millisecond over the end of the drag, on both axes; zero when too short to tell. */
export function releaseVelocity(samples: readonly Sample[]): {
  vx: number;
  vy: number;
} {
  const last = samples[samples.length - 1];
  if (!last) return { vx: 0, vy: 0 };
  const recent = samples.filter((s) => last.t - s.t <= VELOCITY_WINDOW_MS);
  const first = recent[0] ?? last;
  const span = last.t - first.t;
  if (span < MIN_SPAN_MS) return { vx: 0, vy: 0 };
  return { vx: (last.x - first.x) / span, vy: (last.y - first.y) / span };
}

/**
 * What a release does: past the threshold, the side the card is on, unless the person throws it
 * back toward the middle; under it, a fast enough flick in the direction of the drag. Null
 * springs the card back.
 */
export function releaseOutcome(dx: number, vx: number): Swipe | null {
  const side: Swipe = dx > 0 ? "like" : "pass";
  const thrownBack =
    Math.abs(vx) >= FLICK_VELOCITY && Math.sign(vx) === -Math.sign(dx);
  if (Math.abs(dx) >= SWIPE_THRESHOLD) return thrownBack ? null : side;
  const flick =
    Math.abs(vx) >= FLICK_VELOCITY &&
    Math.abs(dx) >= FLICK_MIN_DISTANCE &&
    Math.sign(vx) === Math.sign(dx);
  return flick ? side : null;
}

/**
 * The tilt of a card dragged `dx` pixels, in degrees: it leans the way it goes, and the other way
 * when held by its lower half, as a card pivots under the finger that holds it.
 */
export function tilt(dx: number, heldLow = false): number {
  const degrees = Math.max(-MAX_TILT, Math.min(MAX_TILT, dx / 12));
  return heldLow ? -degrees : degrees;
}

/** How far the card is toward a decision, from -1 (Pass) to 1 (Like); 1 at the threshold. */
export function lean(dx: number): number {
  return Math.max(-1, Math.min(1, dx / SWIPE_THRESHOLD));
}

/** The opacity of the Like stamp (positive `dx`) or the Pass stamp: none until a slight move. */
export function stampOpacity(dx: number): number {
  const start = 12;
  return Math.max(
    0,
    Math.min(1, (Math.abs(dx) - start) / (SWIPE_THRESHOLD - start)),
  );
}

/** "LF" for "Lumen Freight": the two first letters of a company's words, for its monogram. */
export function initials(company: string): string {
  const words = company
    .split(/[\s\-_.,&/]+/)
    .map((word) => word.replace(/[^\p{L}\p{N}]/gu, ""))
    .filter(Boolean);
  const letters = words.slice(0, 2).map((word) => word.charAt(0));
  if (letters.length === 1 && words[0] && words[0].length > 1)
    letters.push(words[0].charAt(1));
  return letters.join("").toUpperCase() || "?";
}
