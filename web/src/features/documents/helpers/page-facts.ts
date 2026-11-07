/**
 * The facts the page prints as they are, never from the text the person writes: the name and the
 * contact lines, each employer's title and dates, the schools and the languages. They come from
 * the profile, completed by a base CV's own `fixed` block as profile_view.build does.
 */
import { photoUrl } from "@/features/profile/api/photo-api";
import type { SectionData } from "@/features/profile/api/profile-api";

import { listAt, type Draft, type EmployerFact } from "./edit-content";
import { textOf } from "./edit-form";

export interface PageEmployer extends EmployerFact {
  location: string | null;
}

export interface PageSchool {
  school: string;
  degree: string;
  start: string | null;
  end: string | null;
  location: string | null;
}

/** A contact line as templates.contact_lines prints it; `link` when it prints as a link. */
export interface ContactLine {
  text: string;
  link: boolean;
}

export interface PageFacts {
  name: string;
  contact: ContactLine[];
  employers: Map<string, PageEmployer>;
  schools: PageSchool[];
  languages: { name: string; level: string | null }[];
}

const record = (value: unknown) => (value ?? {}) as Record<string, unknown>;
const orNull = (value: unknown) => textOf(value) || null;
const fold = (text: string) =>
  text.toLocaleLowerCase().split(/\s+/).filter(Boolean).join(" ");

/** The profile's employers, a missing location from the base CV's; then those only the base CV states. */
export function employersOf(
  profile: {
    key: string;
    organisation: string;
    title: string;
    start: string;
    end: string | null;
    location?: string | null;
  }[],
  fixed: unknown[],
): Map<string, PageEmployer> {
  const own = new Map(
    fixed.map(record).map((one) => [textOf(one.key), one] as const),
  );
  const out = new Map<string, PageEmployer>();
  for (const one of profile)
    out.set(one.key, {
      ...one,
      location: one.location || orNull(own.get(one.key)?.location),
    });
  for (const [key, one] of own) {
    if (key === "" || out.has(key)) continue;
    out.set(key, {
      key,
      organisation: textOf(one.organisation) || key,
      title: textOf(one.title),
      start: orNull(one.start),
      end: orNull(one.end),
      location: orNull(one.location),
    });
  }
  return out;
}

function schoolsOf(
  profile: PageSchool[],
  fixed: Record<string, unknown>[],
): PageSchool[] {
  const own = new Map(fixed.map((one) => [fold(textOf(one.school)), one]));
  const out = profile.map((one) => {
    const other = own.get(fold(one.school));
    return {
      ...one,
      start: one.start || (other ? orNull(other.start) : null),
      end: one.end || (other ? orNull(other.end) : null),
      location: one.location || (other ? orNull(other.location) : null),
    };
  });
  const named = new Set(profile.map((one) => fold(one.school)));
  for (const one of fixed)
    if (!named.has(fold(textOf(one.school))))
      out.push({
        school: textOf(one.school),
        degree: textOf(one.degree),
        start: orNull(one.start),
        end: orNull(one.end),
        location: orNull(one.location),
      });
  return out;
}

function languagesOf(
  profile: { name: string; level?: string | null }[],
  fixed: Record<string, unknown>[],
): PageFacts["languages"] {
  const own = new Map(fixed.map((one) => [fold(textOf(one.name)), one]));
  const out = profile.map((one) => ({
    name: one.name,
    level: one.level || orNull(own.get(fold(one.name))?.level),
  }));
  const named = new Set(profile.map((one) => fold(one.name)));
  for (const one of fixed)
    if (!named.has(fold(textOf(one.name))))
      out.push({ name: textOf(one.name), level: orNull(one.level) });
  return out;
}

/** An address as printed: without its scheme, `www.` and a final slash (templates._shown). */
const shown = (url: string) =>
  url
    .replace(/^https?:\/\//i, "")
    .replace(/^www\./, "")
    .replace(/\/+$/, "");

/** E-mail, phone, place, then each address once, as templates.contact_lines orders them. */
export function contactLines(
  identity: Partial<SectionData["identity"]> | undefined,
): ContactLine[] {
  const lines: ContactLine[] = [];
  if (identity?.email) lines.push({ text: identity.email, link: true });
  if (identity?.phone) lines.push({ text: identity.phone, link: false });
  const place = [identity?.city, identity?.country].filter(Boolean).join(", ");
  if (place !== "") lines.push({ text: place, link: false });
  const seen = new Set<string>();
  for (const url of [
    identity?.linkedin_url,
    identity?.website,
    ...(identity?.links ?? []).map((one) => one.url),
  ]) {
    if (!url || seen.has(url)) continue;
    seen.add(url);
    lines.push({ text: shown(url), link: /^https:\/\/[^\s/]/i.test(url) });
  }
  return lines;
}

/** What the page prints from the profile, completed by `fixed` (a base CV's own facts). */
export function pageFacts(
  profile: Pick<SectionData, "identity" | "experience"> | undefined,
  fixedFrom: Draft,
): PageFacts {
  const identity = profile?.identity;
  const experience = profile?.experience;
  return {
    name: identity?.full_name ?? "",
    contact: contactLines(identity),
    employers: employersOf(
      experience?.employers ?? [],
      listAt(fixedFrom, ["fixed", "employers"]),
    ),
    schools: schoolsOf(
      (experience?.education ?? []).map((one) => ({
        school: one.school,
        degree: one.degree,
        start: one.start,
        end: one.end,
        location: one.location,
      })),
      listAt(fixedFrom, ["fixed", "schools"]).map(record),
    ),
    languages: languagesOf(
      identity?.languages ?? [],
      listAt(fixedFrom, ["fixed", "languages"]).map(record),
    ),
  };
}

/** The address of the profile's photo, if it has one: the photo a template prints. */
export function profilePhoto(
  profile: { has_photo: boolean; photo_etag: string | null } | undefined,
): string | null {
  return profile?.has_photo && profile.photo_etag !== null
    ? photoUrl(profile.photo_etag)
    : null;
}
