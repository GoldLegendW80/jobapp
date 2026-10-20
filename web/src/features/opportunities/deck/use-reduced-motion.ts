import { useEffect, useState } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

function reduced(): boolean {
  return (
    typeof window.matchMedia === "function" && window.matchMedia(QUERY).matches
  );
}

/** True while the person asked the system for reduced motion; follows a change live. */
export function useReducedMotion(): boolean {
  const [value, setValue] = useState(reduced);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const list = window.matchMedia(QUERY);
    const changed = () => {
      setValue(list.matches);
    };
    list.addEventListener("change", changed);
    return () => {
      list.removeEventListener("change", changed);
    };
  }, []);
  return value;
}
