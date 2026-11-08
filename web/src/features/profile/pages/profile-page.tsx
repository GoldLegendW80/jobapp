/**
 * /profile, the one place the profile is edited (product audit, chapter 2, analysis B6 and B7),
 * in tabs, each its own address (owner, 2026-11-07: "I don't want too much scroll, you can put
 * tab"; helpers/anchors.ts): CVs (Your CVs and Upload your CV), Experience (experience and
 * education), About you (the skills, the answers and the facts, as text; without a model key the
 * skills and the answers are forms), Contact (the identity form, with the languages and the
 * photo). Advanced matching is gone (owner, 2026-11-08): skills stored as regular expressions
 * keep scoring, and About you says they stay. The side rail is the person at a glance and what the
 * profile is Ready for. History, Export and Import are under More actions. The search is in Settings, Search. Each
 * form saves on its own; leaving a tab or the page with one unsaved asks first.
 */
import { FileUp } from "lucide-react";
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { Link, Navigate, useLocation, useSearchParams } from "react-router";

import { LoadError } from "@/components/blocks/load-error";
import { Page, PageBody } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { PageTabs } from "@/components/blocks/page-tabs";
import { Section } from "@/components/blocks/section";
import { UnsavedGuard } from "@/components/shared/profile/unsaved-guard";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useTitle } from "@/hooks/use-title";

import type { SectionName } from "../api/profile-api";
import { useProfile } from "../api/use-profile";
import { AboutDocument } from "../components/about-document";
import { ExperienceForm } from "../components/experience-form";
import { IdentityForm } from "../components/identity-form";
import { ProfileMenu } from "../components/profile-menu";
import { ProfileRail } from "../components/profile-rail";
import { YourCvs } from "../components/your-cvs";
import { ABOUT_WORDS } from "../helpers/about-words";
import {
  fallbackAnchor,
  pathOfAnchor,
  pathOfFormerTab,
  PROFILE_ANCHORS,
  PROFILE_TABS,
  tabOf,
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
 * The older addresses keep working: `?tab=search` goes to Settings, Search, the other former
 * tabs and the places of the former one page (`/profile#contact`) to the tab that holds them.
 */
export function ProfilePage() {
  const [params] = useSearchParams();
  const { pathname, hash } = useLocation();
  const tab = params.get("tab");
  if (tab === "search") return <Navigate to="/settings/search" replace />;
  if (tab !== null) return <Navigate to={pathOfFormerTab(tab)} replace />;
  const moved =
    pathname === "/profile" ? pathOfAnchor(hash.slice(1)) : undefined;
  if (moved) return <Navigate to={moved} replace />;
  return <OneProfilePage />;
}

const TABS = PROFILE_TABS.map((tab) => ({ to: tab.path, label: tab.label }));

function OneProfilePage() {
  useTitle(TITLE);
  const tab = tabOf(useLocation().pathname);
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
        <PageTabs label="Parts of your profile" tabs={TABS}>
          {tab === "cvs" ? (
            <Anchor id={PROFILE_ANCHORS.cvs}>
              <YourCvs />
            </Anchor>
          ) : null}
          {tab === "experience" ? (
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
          ) : null}
          {tab === "about" ? (
            <Anchor id={PROFILE_ANCHORS.about}>
              <Section
                title={ABOUT_WORDS.title}
                description={ABOUT_WORDS.intro}
              >
                <AboutDocument profile={profile} forms={plainForms} />
              </Section>
            </Anchor>
          ) : null}
          {tab === "contact" ? (
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
          ) : null}
        </PageTabs>
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
