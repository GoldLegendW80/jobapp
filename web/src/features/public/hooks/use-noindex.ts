import { useEffect } from "react";

/**
 * Tells search engines not to index the page: a robots meta tag in the head while the page is
 * shown, removed when it leaves so the tag never sticks to another route. Caddy serves the same
 * index.html for every path, so the tag has to be added by the page itself.
 */
export function useNoindex() {
  useEffect(() => {
    const tag = document.createElement("meta");
    tag.name = "robots";
    tag.content = "noindex, nofollow";
    document.head.appendChild(tag);
    return () => {
      tag.remove();
    };
  }, []);
}
