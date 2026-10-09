import { useCallback, useEffect, useRef, useState } from "react";

import { isApiError } from "@/lib/api";

/** Seconds to wait after a 429, from its Retry-After header; undefined for anything else. */
export function throttledFor(error: unknown): number | undefined {
  if (!isApiError(error) || error.status !== 429) return undefined;
  const seconds = error.retryAfter;
  return seconds !== undefined && seconds > 0 ? seconds : undefined;
}

/** "4:05" for 245 seconds, "0:09" for 9: minutes, then two digit seconds. */
export function formatWait(seconds: number): string {
  const whole = Math.max(0, Math.ceil(seconds));
  const minutes = Math.floor(whole / 60);
  const rest = whole % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

/**
 * A countdown for the wait a 429 asks for. `start(seconds)` begins it; `remaining` is the whole
 * seconds left (rounded up, so it never shows 0 while still waiting) and 0 once it is over, when
 * `onExpire` runs once. Starting again replaces the running countdown.
 */
export function useRetryCountdown(onExpire?: () => void): {
  remaining: number;
  start: (seconds: number) => void;
} {
  const [deadline, setDeadline] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const expire = useRef(onExpire);
  useEffect(() => {
    expire.current = onExpire;
  }, [onExpire]);

  const start = useCallback((seconds: number) => {
    const at = Date.now();
    setNow(at);
    setDeadline(at + seconds * 1000);
  }, []);

  useEffect(() => {
    if (deadline === null) return;
    const timer = setInterval(() => {
      const at = Date.now();
      if (at >= deadline) {
        clearInterval(timer);
        setDeadline(null);
        expire.current?.();
      } else {
        setNow(at);
      }
    }, 250);
    return () => {
      clearInterval(timer);
    };
  }, [deadline]);

  const remaining =
    deadline === null ? 0 : Math.max(1, Math.ceil((deadline - now) / 1000));
  return { remaining, start };
}
