/**
 * The physics of a deck card's motion, pure: a spring that settles a value on its target with
 * the speed it had, and the easing curves of the timed moves. hooks/deck/use-card-motion.ts runs
 * them frame by frame.
 */

/** Where a card is drawn: offset in pixels, tilt in degrees, scale, opacity. */
export interface Pose {
  x: number;
  y: number;
  rotate: number;
  scale: number;
  opacity: number;
}

export const REST: Pose = { x: 0, y: 0, rotate: 0, scale: 1, opacity: 1 };

/** The pose of the card `depth` places behind the top one, risen by `progress` (0 to 1). */
export function behindPose(depth: number, progress: number): Pose {
  const level = Math.max(0, depth - progress);
  return {
    x: 0,
    y: 18 * level,
    rotate: 0,
    scale: 1 - 0.055 * level,
    opacity: level > 1.5 ? 0.6 : 1,
  };
}

/** A value on its way: where it is and how fast it moves (units per second). */
export interface Moving {
  value: number;
  velocity: number;
}

export interface Spring {
  stiffness: number;
  damping: number;
}

/** A light overshoot: the card settles back in the middle like something with weight. */
export const SETTLE: Spring = { stiffness: 420, damping: 28 };
/** Softer, for a card that comes back from off the screen (an undo, a refused swipe). */
export const RETURN: Spring = { stiffness: 260, damping: 24 };

const STEP = 1 / 240;

/** Advances `moving` by `seconds` toward `target`, in small fixed steps so it never explodes. */
export function springStep(
  moving: Moving,
  target: number,
  spring: Spring,
  seconds: number,
): Moving {
  let { value, velocity } = moving;
  let left = Math.min(seconds, 0.1);
  while (left > 0) {
    const dt = Math.min(STEP, left);
    const force =
      -spring.stiffness * (value - target) - spring.damping * velocity;
    velocity += force * dt;
    value += velocity * dt;
    left -= dt;
  }
  return { value, velocity };
}

/** Whether a spring has come to rest on its target (within `epsilon` units). */
export function settled(
  moving: Moving,
  target: number,
  epsilon = 0.1,
): boolean {
  return (
    Math.abs(moving.value - target) < epsilon &&
    Math.abs(moving.velocity) < epsilon * 10
  );
}

/** The CSS `cubic-bezier(x1, y1, x2, y2)` curve as a function of time from 0 to 1. */
export function cubicBezier(
  x1: number,
  y1: number,
  x2: number,
  y2: number,
): (t: number) => number {
  const at = (a: number, b: number, s: number) =>
    3 * a * s * (1 - s) ** 2 + 3 * b * s * s * (1 - s) + s ** 3;
  return (t) => {
    if (t <= 0) return 0;
    if (t >= 1) return 1;
    let lo = 0;
    let hi = 1;
    let s = t;
    for (let i = 0; i < 24; i += 1) {
      s = (lo + hi) / 2;
      if (at(x1, x2, s) < t) lo = s;
      else hi = s;
    }
    return at(y1, y2, s);
  };
}

/** A card thrown by the hand keeps its speed and slows as it leaves. */
export const THROWN = cubicBezier(0.2, 0.7, 0.4, 1);
/** A card sent by a button or a key gathers speed, as if pushed. */
export const PUSHED = cubicBezier(0.5, 0, 0.75, 0.6);

/** How long a card takes to leave: as fast as it was thrown, never a crawl nor a blink. */
export function leaveDuration(distance: number, speed: number): number {
  if (speed <= 0) return 340;
  return Math.max(180, Math.min(420, distance / speed));
}
