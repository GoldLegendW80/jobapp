import {
  screenshot,
  type ProfileRun,
  type ProfileSection,
} from "../../api/site-profile-api";

/** The section before saving (Save highlighted) and after, each opening full size in a new tab. */
export function SectionScreenshots({
  run,
  section,
}: {
  run: ProfileRun;
  section: ProfileSection;
}) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {section.screenshots
        .filter((shot) => shot.kind === "before" || shot.kind === "after")
        .map((shot) => (
          <figure key={shot.id} className="min-w-0">
            <a
              href={screenshot(run.id, section.section, shot.id)}
              target="_blank"
              rel="noreferrer"
            >
              <img
                className="w-full rounded-md border"
                src={screenshot(run.id, section.section, shot.id)}
                alt={`${section.label}, ${shot.kind === "before" ? "before saving, with Save highlighted" : "after saving"}`}
              />
            </a>
            <figcaption className="text-xs text-muted-foreground">
              {shot.kind === "before" ? "Before saving" : "After saving"}
            </figcaption>
          </figure>
        ))}
    </div>
  );
}
