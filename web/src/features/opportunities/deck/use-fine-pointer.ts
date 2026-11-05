import { useEffect, useState } from "react";

const QUERY = "(pointer: fine)";

function fine(): boolean {
  return (
    typeof window.matchMedia === "function" && window.matchMedia(QUERY).matches
  );
}

/**
 * True while the main pointer is a precise one (a mouse or a trackpad), where a keyboard is
 * likely; false on a touch screen. Follows a change live, like a tablet docked to a keyboard.
 */
export function useFinePointer(): boolean {
  const [value, setValue] = useState(fine);
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
