/**
 * The faces the PDF embeds (api/app/documents/fonts/<family>/family.json: Fontsource 5.3.0 files),
 * loaded from the same packages for the page drawn here, Latin only (the browser draws the rest).
 */
const FACES: Record<string, () => Promise<unknown>> = {
  gantari: () =>
    Promise.all([
      import("@fontsource/gantari/latin-400-italic.css"),
      import("@fontsource/gantari/latin-400.css"),
      import("@fontsource/gantari/latin-500.css"),
      import("@fontsource/gantari/latin-600.css"),
      import("@fontsource/gantari/latin-700-italic.css"),
      import("@fontsource/gantari/latin-700.css"),
    ]),
  "inter-tight": () =>
    Promise.all([
      import("@fontsource/inter-tight/latin-400-italic.css"),
      import("@fontsource/inter-tight/latin-400.css"),
      import("@fontsource/inter-tight/latin-500.css"),
      import("@fontsource/inter-tight/latin-600.css"),
      import("@fontsource/inter-tight/latin-700-italic.css"),
      import("@fontsource/inter-tight/latin-700.css"),
    ]),
  raleway: () =>
    Promise.all([
      import("@fontsource/raleway/latin-400-italic.css"),
      import("@fontsource/raleway/latin-400.css"),
      import("@fontsource/raleway/latin-500.css"),
      import("@fontsource/raleway/latin-600.css"),
      import("@fontsource/raleway/latin-700-italic.css"),
      import("@fontsource/raleway/latin-700.css"),
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
