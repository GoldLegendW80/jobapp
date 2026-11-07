import { useEffect, useRef, type RefObject } from "react";

/**
 * A control shown while `shown` (a run's Cancel): when it goes away with the focus, the focus
 * moves to `target` instead of falling back to the page. Spread the handlers on the control.
 */
export function useReturnFocus(
  shown: boolean,
  target: RefObject<HTMLElement | null>,
) {
  const focused = useRef(false);
  useEffect(() => {
    if (shown || !focused.current) return;
    focused.current = false;
    const lost =
      document.activeElement === null ||
      document.activeElement === document.body;
    if (lost) target.current?.focus();
  }, [shown, target]);
  return {
    onFocus: () => {
      focused.current = true;
    },
    onBlur: () => {
      focused.current = false;
    },
  };
}
