import { Link } from "react-router";

import type { Profile } from "@/features/profile/api/profile-api";

import { span } from "@/features/documents/helpers/edit-content";
import { FormSection } from "./form-section";

/** The fixed facts, read only: the template prints them from the profile, never from the form. */
export function ProfileFacts({
  profile,
  letter,
}: {
  profile: Profile | undefined;
  letter: boolean;
}) {
  if (profile === undefined) return null;
  const identity = profile.sections.identity;
  const { employers, education } = profile.sections.experience;
  const contact = [
    identity.email,
    identity.phone,
    identity.city,
    identity.website,
  ].filter((one): one is string => typeof one === "string" && one !== "");
  return (
    <FormSection title="From your profile, printed as they are">
      <dl className="grid gap-2 text-sm sm:grid-cols-[10rem_1fr]">
        <dt className="text-muted-foreground">Name</dt>
        <dd>{identity.full_name ?? "Not in your profile yet"}</dd>
        <dt className="text-muted-foreground">Contact lines</dt>
        <dd>{contact.length === 0 ? "None yet" : contact.join(", ")}</dd>
        {letter ? null : (
          <>
            <dt className="text-muted-foreground">Employers</dt>
            <dd>
              {employers.length === 0
                ? "None yet"
                : employers
                    .map((one) =>
                      [one.organisation, one.title, span(one.start, one.end)]
                        .filter((part) => part !== "")
                        .join(", "),
                    )
                    .join("; ")}
            </dd>
            <dt className="text-muted-foreground">Schools</dt>
            <dd>
              {education.length === 0
                ? "None yet"
                : education.map((one) => one.school).join("; ")}
            </dd>
            <dt className="text-muted-foreground">Languages</dt>
            <dd>
              {identity.languages.length === 0
                ? "None yet"
                : identity.languages
                    .map((one) =>
                      one.level ? `${one.name} (${one.level})` : one.name,
                    )
                    .join(", ")}
            </dd>
          </>
        )}
      </dl>
      <p className="text-sm">
        <Link className="underline" to="/profile?tab=identity">
          Change your name and contact in your profile
        </Link>
        {letter ? null : (
          <>
            {", or "}
            <Link className="underline" to="/profile?tab=experience">
              your employers and schools
            </Link>
          </>
        )}
        .
      </p>
    </FormSection>
  );
}
