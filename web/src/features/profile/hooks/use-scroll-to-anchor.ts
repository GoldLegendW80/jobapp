import { useEffect } from "react";
import { useLocation } from "react-router";

/** How long the page may take to draw the place the address names (its data loads first). */
export const ANCHOR_WAIT_MS = 5000;

/**
 * Scrolls to the element the address's hash names once the page has drawn it: the router only
 * looks for it at the navigation, before the profile and About you have loaded. When it never
 * shows, `fallback` may name another place to go to.
 */
export function useScrollToAnchor(
  fallback: (id: string) => string | undefined,
) {
  const { hash } = useLocation();
  useEffect(() => {
    const id = decodeURIComponent(hash.slice(1));
    if (id === "") return;
    const reach = (target: string) => {
      const element = document.getElementById(target);
      element?.scrollIntoView({ block: "start" });
      return element !== null;
    };
    if (reach(id)) return;
    const observer = new MutationObserver(() => {
      if (reach(id)) stop();
    });
    const timer = window.setTimeout(() => {
      stop();
      const other = fallback(id);
      if (other !== undefined) reach(other);
    }, ANCHOR_WAIT_MS);
    function stop() {
      observer.disconnect();
      window.clearTimeout(timer);
    }
    observer.observe(document.body, { childList: true, subtree: true });
    return stop;
  }, [hash, fallback]);
}
