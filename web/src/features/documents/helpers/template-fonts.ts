/**
 * The faces the PDF embeds (api/app/documents/fonts/<family>/family.json: Fontsource 5.3.0 files),
 * loaded from the same packages for the page drawn here, Latin only (the browser draws the rest).
 */
const FACES: Record<string, () => Promise<unknown>> = {
  arimo: () =>
    Promise.all([
      import("@fontsource/arimo/latin-400-italic.css"),
      import("@fontsource/arimo/latin-400.css"),
      import("@fontsource/arimo/latin-600.css"),
      import("@fontsource/arimo/latin-700-italic.css"),
      import("@fontsource/arimo/latin-700.css"),
    ]),
  caladea: () =>
    Promise.all([
      import("@fontsource/caladea/latin-400-italic.css"),
      import("@fontsource/caladea/latin-400.css"),
      import("@fontsource/caladea/latin-700-italic.css"),
      import("@fontsource/caladea/latin-700.css"),
    ]),
  carlito: () =>
    Promise.all([
      import("@fontsource/carlito/latin-400-italic.css"),
      import("@fontsource/carlito/latin-400.css"),
      import("@fontsource/carlito/latin-700-italic.css"),
      import("@fontsource/carlito/latin-700.css"),
    ]),
  "dm-sans": () =>
    Promise.all([
      import("@fontsource/dm-sans/latin-400-italic.css"),
      import("@fontsource/dm-sans/latin-400.css"),
      import("@fontsource/dm-sans/latin-600.css"),
      import("@fontsource/dm-sans/latin-700-italic.css"),
      import("@fontsource/dm-sans/latin-700.css"),
    ]),
  "eb-garamond": () =>
    Promise.all([
      import("@fontsource/eb-garamond/latin-400-italic.css"),
      import("@fontsource/eb-garamond/latin-400.css"),
      import("@fontsource/eb-garamond/latin-600.css"),
      import("@fontsource/eb-garamond/latin-700-italic.css"),
      import("@fontsource/eb-garamond/latin-700.css"),
    ]),
  "ibm-plex-mono": () =>
    Promise.all([
      import("@fontsource/ibm-plex-mono/latin-400-italic.css"),
      import("@fontsource/ibm-plex-mono/latin-400.css"),
      import("@fontsource/ibm-plex-mono/latin-600.css"),
      import("@fontsource/ibm-plex-mono/latin-700-italic.css"),
      import("@fontsource/ibm-plex-mono/latin-700.css"),
    ]),
  "ibm-plex-sans": () =>
    Promise.all([
      import("@fontsource/ibm-plex-sans/latin-400-italic.css"),
      import("@fontsource/ibm-plex-sans/latin-400.css"),
      import("@fontsource/ibm-plex-sans/latin-600.css"),
      import("@fontsource/ibm-plex-sans/latin-700-italic.css"),
      import("@fontsource/ibm-plex-sans/latin-700.css"),
    ]),
  inter: () =>
    Promise.all([
      import("@fontsource/inter/latin-400-italic.css"),
      import("@fontsource/inter/latin-400.css"),
      import("@fontsource/inter/latin-600.css"),
      import("@fontsource/inter/latin-700-italic.css"),
      import("@fontsource/inter/latin-700.css"),
    ]),
  lora: () =>
    Promise.all([
      import("@fontsource/lora/latin-400-italic.css"),
      import("@fontsource/lora/latin-400.css"),
      import("@fontsource/lora/latin-600.css"),
      import("@fontsource/lora/latin-700-italic.css"),
      import("@fontsource/lora/latin-700.css"),
    ]),
  "nunito-sans": () =>
    Promise.all([
      import("@fontsource/nunito-sans/latin-400-italic.css"),
      import("@fontsource/nunito-sans/latin-400.css"),
      import("@fontsource/nunito-sans/latin-600.css"),
      import("@fontsource/nunito-sans/latin-700-italic.css"),
      import("@fontsource/nunito-sans/latin-700.css"),
    ]),
  "playfair-display": () =>
    Promise.all([
      import("@fontsource/playfair-display/latin-400-italic.css"),
      import("@fontsource/playfair-display/latin-400.css"),
      import("@fontsource/playfair-display/latin-600.css"),
      import("@fontsource/playfair-display/latin-700-italic.css"),
      import("@fontsource/playfair-display/latin-700.css"),
    ]),
  "source-serif-4": () =>
    Promise.all([
      import("@fontsource/source-serif-4/latin-400-italic.css"),
      import("@fontsource/source-serif-4/latin-400.css"),
      import("@fontsource/source-serif-4/latin-600.css"),
      import("@fontsource/source-serif-4/latin-700-italic.css"),
      import("@fontsource/source-serif-4/latin-700.css"),
    ]),
  tinos: () =>
    Promise.all([
      import("@fontsource/tinos/latin-400-italic.css"),
      import("@fontsource/tinos/latin-400.css"),
      import("@fontsource/tinos/latin-700-italic.css"),
      import("@fontsource/tinos/latin-700.css"),
    ]),
  "work-sans": () =>
    Promise.all([
      import("@fontsource/work-sans/latin-400-italic.css"),
      import("@fontsource/work-sans/latin-400.css"),
      import("@fontsource/work-sans/latin-600.css"),
      import("@fontsource/work-sans/latin-700-italic.css"),
      import("@fontsource/work-sans/latin-700.css"),
    ]),
};

const loading = new Map<string, Promise<unknown>>();

/** Loads the faces of these families once each; an unknown family is left to the browser. */
export function loadTemplateFonts(
  families: readonly string[],
): Promise<unknown> {
  const started = families.flatMap((family) => {
    const load = FACES[family];
    if (load === undefined) return [];
    const once = loading.get(family) ?? load();
    loading.set(family, once);
    return [once];
  });
  return Promise.all(started);
}
