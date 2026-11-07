import { screenshot, type ProfileRun } from "../../api/site-profile-api";

/**
 * The sections of the run as the site shows them, before saving (Save highlighted) and after,
 * each opening full size in a new tab; named after their section when there are several.
 */
export function SectionScreenshots({ run }: { run: ProfileRun }) {
  const shown = run.sections.flatMap((section) =>
    section.screenshots
      .filter((shot) => shot.kind === "before" || shot.kind === "after")
      .map((shot) => ({ section, shot })),
  );
  const several = new Set(shown.map(({ section }) => section.section)).size > 1;
  return (
    <div className="flex flex-col gap-4">
      {shown.map(({ section, shot }) => {
        const when = shot.kind === "before" ? "Before saving" : "After saving";
        return (
          <figure key={shot.id} className="flex min-w-0 flex-col gap-1">
            <a
              href={screenshot(run.id, section.section, shot.id)}
              target="_blank"
              rel="noreferrer"
              className="rounded-md outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
            >
              <img
                className="w-full rounded-md border bg-card"
                src={screenshot(run.id, section.section, shot.id)}
                alt={`${section.label}, ${shot.kind === "before" ? "before saving, with Save highlighted" : "after saving"}`}
              />
            </a>
            <figcaption className="text-xs text-muted-foreground">
              {several ? `${section.label}, ${when.toLowerCase()}` : when}
            </figcaption>
          </figure>
        );
      })}
    </div>
  );
}
