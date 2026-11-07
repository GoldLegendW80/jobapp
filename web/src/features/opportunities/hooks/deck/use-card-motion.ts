import { useCallback, useEffect, useMemo, useRef, type RefObject } from "react";

import {
  REST,
  springStep,
  settled,
  type Moving,
  type Pose,
  type Spring,
} from "@/features/opportunities/helpers/deck/motion";

type Axis = keyof Pose;
const AXES: readonly Axis[] = ["x", "y", "rotate", "scale", "opacity"];

export function transformOf(pose: Pose): string {
  return `translate3d(${pose.x.toFixed(2)}px, ${pose.y.toFixed(2)}px, 0) rotate(${pose.rotate.toFixed(3)}deg) scale(${pose.scale.toFixed(4)})`;
}

export interface CardMotion {
  pose: () => Pose;
  /** Puts the card there at once, stopping any move under way. */
  set: (pose: Partial<Pose>) => void;
  /** Springs to `target`, starting with `velocity` (units per second) on the axes given. */
  spring: (
    target: Partial<Pose>,
    spring: Spring,
    velocity?: Partial<Pose>,
    done?: () => void,
  ) => void;
  /** Moves to `target` in `ms`, along `ease`. */
  tween: (
    target: Partial<Pose>,
    ms: number,
    ease: (t: number) => number,
    done?: () => void,
  ) => void;
  stop: () => void;
}

/**
 * The motion of one card, written straight to its element's style frame by frame (no React
 * render per frame). `onFrame` hears every pose drawn, for the stamps and the deck's lean.
 */
export function useCardMotion(
  ref: RefObject<HTMLElement | null>,
  onFrame?: (pose: Pose) => void,
): CardMotion {
  const pose = useRef<Pose>({ ...REST });
  const frame = useRef<number | null>(null);
  const listener = useRef(onFrame);
  useEffect(() => {
    listener.current = onFrame;
  });

  const draw = useCallback(() => {
    const element = ref.current;
    if (element) {
      const { x, y, rotate, scale } = pose.current;
      const still = x === 0 && y === 0 && rotate === 0 && scale === 1;
      element.style.transform = still ? "" : transformOf(pose.current);
      element.style.opacity =
        pose.current.opacity === 1 ? "" : String(pose.current.opacity);
    }
    listener.current?.(pose.current);
  }, [ref]);

  const stop = useCallback(() => {
    if (frame.current !== null) cancelAnimationFrame(frame.current);
    frame.current = null;
  }, []);

  useEffect(() => stop, [stop]);

  return useMemo<CardMotion>(() => {
    const run = (
      step: (seconds: number, now: number) => boolean,
      done?: () => void,
    ) => {
      stop();
      let last: number | null = null;
      const tick = (now: number) => {
        const seconds = last === null ? 1 / 60 : (now - last) / 1000;
        last = now;
        const finished = step(seconds, now);
        draw();
        if (finished) {
          frame.current = null;
          done?.();
        } else frame.current = requestAnimationFrame(tick);
      };
      frame.current = requestAnimationFrame(tick);
    };
    return {
      pose: () => ({ ...pose.current }),
      set: (next) => {
        stop();
        pose.current = { ...pose.current, ...next };
        draw();
      },
      spring: (target, spring, velocity = {}, done) => {
        const axes = AXES.filter((axis) => target[axis] !== undefined);
        const moving = new Map<Axis, Moving>(
          axes.map((axis) => [
            axis,
            { value: pose.current[axis], velocity: velocity[axis] ?? 0 },
          ]),
        );
        run((seconds) => {
          let rest = true;
          for (const axis of axes) {
            const goal = target[axis] ?? 0;
            const now = moving.get(axis);
            if (!now) continue;
            const next = springStep(now, goal, spring, seconds);
            const scale = axis === "scale" || axis === "opacity" ? 0.001 : 0.1;
            if (settled(next, goal, scale)) {
              moving.set(axis, { value: goal, velocity: 0 });
              pose.current = { ...pose.current, [axis]: goal };
            } else {
              rest = false;
              moving.set(axis, next);
              pose.current = { ...pose.current, [axis]: next.value };
            }
          }
          return rest;
        }, done);
      },
      tween: (target, ms, ease, done) => {
        const from = { ...pose.current };
        let start: number | null = null;
        run((_seconds, now) => {
          start ??= now;
          const t = ms <= 0 ? 1 : Math.min(1, (now - start) / ms);
          const k = ease(t);
          const next = { ...pose.current };
          for (const axis of AXES) {
            const goal = target[axis];
            if (goal !== undefined)
              next[axis] = from[axis] + (goal - from[axis]) * k;
          }
          pose.current = next;
          return t >= 1;
        }, done);
      },
      stop,
    };
  }, [draw, stop]);
}
