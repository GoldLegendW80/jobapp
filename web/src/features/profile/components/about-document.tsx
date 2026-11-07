/**
 * "About you": the person's Markdown document (owner, 2026-11-05: "just put informations on a .md
 * should be well rendered and editable", "a markdown that a model interpret"). Each part (a `##`
 * heading) is shown rendered by the safe renderer and edited in place, one at a time, with a live
 * preview; the text saves as the person types (AUTOSAVE_MS, as the CV editor). Done reads the
 * part with the model; under it, "What the app understood" lists the changes with one Apply, then
 * Undo. A fact part needs no model: Done saves it as written. Fact parts move up and down and the
 * fact base keeps their order; a meter beside the contents says its size against the cap. Without
 * a model key the skills and the answers are fields, saved as their sections (owner, 2026-11-07).
 * The contents list is sticky on desktop and a row of chips on phones.
 */
import { LoadError } from "@/components/blocks/load-error";
import { Skeleton } from "@/components/ui/skeleton";

import { useAbout } from "../api/about-api";
import type { Profile } from "../api/profile-api";
import { ABOUT_WORDS as W } from "../helpers/about-words";
import { AboutEditor, type PlainForms } from "./about/about-editor";

export function AboutDocument({
  profile,
  forms,
}: {
  profile: Profile;
  forms: PlainForms;
}) {
  const query = useAbout();
  if (query.isPending)
    return (
      <div role="status" aria-live="polite" className="flex flex-col gap-4">
        <span className="sr-only">{W.loading}</span>
        <Skeleton aria-hidden="true" className="h-40" />
        <Skeleton aria-hidden="true" className="h-40" />
      </div>
    );
  if (query.isError) return <LoadError query={query}>{W.notLoaded}.</LoadError>;
  return <AboutEditor about={query.data} profile={profile} forms={forms} />;
}
