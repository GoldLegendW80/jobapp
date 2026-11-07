import { useEffect } from "react";

import { loadTemplateFonts } from "../helpers/template-fonts";

/** Starts loading a template's faces; the page is drawn in the fallback face until they arrive. */
export function useTemplateFonts(families: readonly string[]): void {
  const key = families.join(" ");
  useEffect(() => {
    void loadTemplateFonts(key.split(" "));
  }, [key]);
}
