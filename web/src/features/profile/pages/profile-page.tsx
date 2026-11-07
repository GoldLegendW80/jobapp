/**
 * /profile, the one place the profile is edited (product audit, chapter 2, analysis B6 and B7;
 * the tabbed classic editor is gone, owner 2026-11-06). The main column, top to bottom: Your CVs
 * and Upload your CV; Experience and education (its form); About you (the skills, the answers
 * and the facts, as text; without a model key the skills and the answers are forms); Advanced
 * matching (the skills written as regular expressions, which About you leaves out, and Try a
 * posting); Contact and links (the identity form, with the languages and the photo). The side rail is the person at a glance and what the profile is
 * Ready for. History, Export and Import are under More actions. The search is in Settings,
 * Search. Each form saves on its own; leaving the page with one unsaved asks first.
 */
import { FileUp } from "lucide-react";
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { Link, Navigate, useSearchParams } from "react-router";

import { LoadError } from "@/components/blocks/load-error";
import { Page, PageBody } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { Section } from "@/components/blocks/section";
import { UnsavedGuard } from "@/components/shared/profile/unsaved-guard";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useTitle } from "@/hooks/use-title";

import type { SectionName } from "../api/profile-api";
import { useProfile } from "../api/use-profile";
import { AboutDocument } from "../components/about-document";
import { AdvancedMatching } from "../components/advanced-matching";
import { ExperienceForm } from "../components/experience-form";
import { IdentityForm } from "../components/identity-form";
import { ProfileMenu } from "../components/profile-menu";
import { ProfileRail } from "../components/profile-rail";
import { YourCvs } from "../components/your-cvs";
import { ABOUT_WORDS } from "../helpers/about-words";
import {
  anchorOfTab,
  fallbackAnchor,
  PROFILE_ANCHORS,
} from "../helpers/anchors";
import { useScrollToAnchor } from "../hooks/use-scroll-to-anchor";

const TITLE = "Profile";
const DESCRIPTION =
  "What the app reads to score postings, write your CVs and fill forms.";

/** The forms of the page, the section each saves, and their names in the unsaved changes guard. */
const FORMS = {
  experience: { section: "experience", label: "Experience and education" },
  skills: { section: "skills", label: "Skills" },
  answers: { section: "answers", label: "Answers" },
  matching: { section: "skills", label: "Advanced matching" },
  identity: { section: "identity", label: "Contact and links" },
} as const satisfies Record<string, { section: SectionName; label: string }>;
type FormName = keyof typeof FORMS;
const FORM_NAMES = Object.keys(FORMS) as FormName[];
const counts = (value: number) =>
  Object.fromEntries(FORM_NAMES.map((name) => [name, value])) as Record<
    FormName,
    number
  >;

/**
 * The addresses of the former tabs keep working: `?tab=search` goes to Settings, Search, and the
 * other tabs land on the section of this page that holds what they showed.
 */
export function ProfilePage() {
  const [params] = useSearchParams();
  const tab = params.get("tab");
  if (tab === "search") return <Navigate to="/settings/search" replace />;
  if (tab !== null)
    return (
      <Navigate to={{ pathname: "/profile", hash: anchorOfTab(tab) }} replace />
    );
  return <OneProfilePage />;
}

function OneProfilePage() {
  useTitle(TITLE);
  useScrollToAnchor(fallbackAnchor);
  const query = useProfile();
  // The forms with unsaved changes, in the order they were changed.
  const [unsaved, setUnsaved] = useState<FormName[]>([]);
  // Bumped per form after a restore of its section, for every form after an import: a new key
  // remounts the form, which reads the new current version.
  const [keys, setKeys] = useState(() => counts(0));
  const onDirty = useMemo(
    () =>
      Object.fromEntries(
        FORM_NAMES.map((name) => [
          name,
          (value: boolean) => {
            setUnsaved((was) =>
              was.filter((one) => one !== name).concat(value ? [name] : []),
            );
          },
        ]),
      ) as Record<FormName, (dirty: boolean) => void>,
    [],
  );
  const reload = useCallback((section?: SectionName) => {
    const hit = (name: FormName) =>
      section === undefined || FORMS[name].section === section;
    setUnsaved((was) => was.filter((name) => !hit(name)));
    setKeys(
      (was) =>
        Object.fromEntries(
          FORM_NAMES.map((name) => [name, was[name] + (hit(name) ? 1 : 0)]),
        ) as Record<FormName, number>,
    );
  }, []);
  const plainForms = useMemo(
    () => ({
      onDirty: { skills: onDirty.skills, answers: onDirty.answers },
      keys: { skills: keys.skills, answers: keys.answers },
    }),
    [onDirty, keys.skills, keys.answers],
  );

  if (query.isPending) return <ProfileSkeleton />;
  if (query.isError)
    return (
      <Page>
        <PageHeader title={TITLE} description={DESCRIPTION} />
        <LoadError query={query}>Your profile could not be loaded.</LoadError>
      </Page>
    );

  const profile = query.data;
  return (
    <Page>
      <PageHeader
        title={TITLE}
        description={DESCRIPTION}
        actions={
          <>
            <Button asChild variant="outline">
              <Link to="/profile/fill">
                <FileUp aria-hidden="true" />
                Fill my profile with an agent
              </Link>
            </Button>
            <ProfileMenu
              profile={profile}
              dirty={unsaved.map((name) => FORMS[name].section)}
              onChanged={reload}
            />
          </>
        }
      />
      <PageBody aside={<ProfileRail profile={profile} />}>
        <Anchor id={PROFILE_ANCHORS.cvs}>
          <YourCvs />
        </Anchor>
        <Anchor id={PROFILE_ANCHORS.experience}>
          <Section
            title="Experience and education"
            description="Your jobs and schools, with their dates, as every CV prints them."
          >
            <ExperienceForm
              key={keys.experience}
              profile={profile}
              onDirtyChange={onDirty.experience}
            />
          </Section>
        </Anchor>
        <Anchor id={PROFILE_ANCHORS.about}>
          <Section title={ABOUT_WORDS.title} description={ABOUT_WORDS.intro}>
            <AboutDocument profile={profile} forms={plainForms} />
          </Section>
        </Anchor>
        <Anchor id={PROFILE_ANCHORS.matching}>
          <Section
            title="Advanced matching"
            description="Skill categories and requirements written as regular expressions, which About you leaves out, and a posting tried against all your skills."
          >
            <AdvancedMatching
              key={keys.matching}
              profile={profile}
              onDirtyChange={onDirty.matching}
            />
          </Section>
        </Anchor>
        <Anchor id={PROFILE_ANCHORS.contact}>
          <Section
            title="Contact and links"
            description="Your name, contact details, links, languages and photo, as every document shows them."
          >
            <IdentityForm
              key={keys.identity}
              profile={profile}
              onDirtyChange={onDirty.identity}
            />
          </Section>
        </Anchor>
      </PageBody>
      <UnsavedGuard
        dirty={unsaved.length > 0}
        what={unsaved.map((name) => FORMS[name].label).join(", ")}
        onDiscard={() => {
          for (const name of unsaved) reload(FORMS[name].section);
        }}
      />
    </Page>
  );
}

/** Where Ready for and the links of the page land, clear of the shell's sticky header. */
function Anchor({ id, children }: { id: string; children: ReactNode }) {
  return (
    <div id={id} className="scroll-mt-24">
      {children}
    </div>
  );
}

/**
 * The page's place while the profile loads: the header, the rail and the first sections, in
 * grey. No h1 yet: the title shows with the profile, so whatever waits for it finds the page.
 */
function ProfileSkeleton() {
  return (
    <Page>
      <div role="status" aria-live="polite" className="flex flex-col gap-2">
        <span className="sr-only">Loading your profile</span>
        <Skeleton aria-hidden="true" className="h-9 w-40" />
        <Skeleton aria-hidden="true" className="h-4 w-full max-w-md" />
      </div>
      <PageBody aside={<Skeleton aria-hidden="true" className="h-64" />}>
        <div aria-hidden="true" className="flex flex-col gap-4">
          <Skeleton className="h-6 w-40" />
          <Skeleton className="h-40" />
        </div>
        <div aria-hidden="true" className="flex flex-col gap-4">
          <Skeleton className="h-6 w-56" />
          <Skeleton className="h-72" />
        </div>
      </PageBody>
    </Page>
  );
}
