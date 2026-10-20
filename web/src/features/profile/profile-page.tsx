import { useQuery } from "@tanstack/react-query";
import { LoaderCircle, RotateCw, Sparkles } from "lucide-react";
import {
  Fragment,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  Link,
  useBlocker,
  useLocation,
  useNavigate,
  useSearchParams,
} from "react-router";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { describeError } from "@/lib/api";
import { useTitle } from "@/lib/use-title";
import { cn } from "@/lib/utils";

import {
  getProfile,
  profileKey,
  TAB_SECTIONS,
  type Profile,
  type TabSection,
} from "./api";
import { CvTab } from "@/features/documents/cv-tab";

import { AnswersTab } from "./answers-tab";
import { ExperienceTab } from "./experience-tab";
import { FactsTab } from "./facts-tab";
import { HistoryDrawer } from "./history-drawer";
import { IdentityTab } from "./identity-tab";
import { missingLabel, SECTION_LABELS } from "./labels";
import { ProfileFileMenu } from "./profile-file";
import { SearchTab } from "./search-tab";
import { SkillsTab } from "./skills-tab";

const isSection = (value: string | null): value is TabSection =>
  value !== null && (TAB_SECTIONS as readonly string[]).includes(value);

/** The CV tab is not a profile section: it lives at /profile/cv and /profile/cv/:id. */
const CV_TAB = "cv";
type TabName = TabSection | typeof CV_TAB;

/**
 * /profile: the completeness of the whole profile, then one tab per section, each an editor that
 * saves on its own.
 * The open tab lives in `?tab=`, except the CV tab (documents D11), which has paths of its own
 * so a base CV can be linked. With unsaved changes, switching tab, following a link away or
 * closing the page asks first. Each tab has a History drawer (view and restore past saves), and
 * the header an Export or import menu. A restore or an import remounts the open tab, so its form
 * reloads from the new current version.
 */
export function ProfilePage() {
  useTitle("Profile");
  const query = useQuery({
    queryKey: profileKey,
    queryFn: ({ signal }) => getProfile(signal),
  });
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const onCv = /^\/profile\/cv(\/|$)/.test(useLocation().pathname);
  const requested = params.get("tab");
  const tab: TabSection = isSection(requested) ? requested : "identity";
  const active: TabName = onCv ? CV_TAB : tab;
  const [dirty, setDirty] = useState(false);
  const [pendingTab, setPendingTab] = useState<TabName>();
  // Bumped after a restore or an import: the tabs remount and read the new current version.
  const [generation, setGeneration] = useState(0);
  const reloadTabs = useCallback(() => {
    setDirty(false);
    setGeneration((value) => value + 1);
  }, []);
  const onDirtyChange = useCallback((value: boolean) => {
    setDirty(value);
  }, []);

  // Set while the tab guard's own "Discard changes" navigates: the person has just answered, so
  // the blocker must not ask a second time for a change of pathname (the CV tab has its own).
  const discarding = useRef(false);
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      !discarding.current &&
      dirty &&
      currentLocation.pathname !== nextLocation.pathname,
  );

  useEffect(() => {
    if (!dirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      // Asks the browser to confirm leaving; it shows its own message.
      event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
    };
  }, [dirty]);

  const openTab = (next: TabName) => {
    void navigate(
      next === CV_TAB
        ? { pathname: "/profile/cv" }
        : { pathname: "/profile", search: `?tab=${next}` },
      { replace: true },
    );
  };

  if (query.isPending) {
    return (
      <div
        role="status"
        className="mx-auto flex max-w-3xl items-center gap-2 text-sm text-muted-foreground"
      >
        <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
        Loading your profile
      </div>
    );
  }
  if (query.isError) {
    return (
      <div className="mx-auto flex max-w-3xl flex-col gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">Profile</h1>
        <Alert variant="destructive">
          <AlertTitle>Your profile could not be loaded</AlertTitle>
          <AlertDescription>{describeError(query.error)}</AlertDescription>
        </Alert>
        <div>
          <Button
            variant="outline"
            disabled={query.isFetching}
            onClick={() => void query.refetch()}
          >
            <RotateCw aria-hidden="true" />
            Retry
          </Button>
        </div>
      </div>
    );
  }

  const profile = query.data;
  const leaving = blocker.state === "blocked";
  const guardOpen = leaving || pendingTab !== undefined;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <Header
        profile={profile}
        actions={
          <ProfileFileMenu
            profile={profile}
            dirtySection={dirty ? tab : undefined}
            onImported={reloadTabs}
          />
        }
      />
      <Tabs
        value={active}
        onValueChange={(value) => {
          if (!(isSection(value) || value === CV_TAB) || value === active)
            return;
          if (dirty) setPendingTab(value);
          else openTab(value);
        }}
      >
        <TabsList
          aria-label="Profile sections"
          className="grid h-auto grid-cols-4 gap-1 sm:grid-cols-7"
        >
          <TabsTrigger value={CV_TAB}>CV</TabsTrigger>
          {TAB_SECTIONS.map((name) => {
            const percent = profile.completeness.sections[name]?.percent ?? 0;
            return (
              <TabsTrigger
                key={name}
                value={name}
                aria-label={`${SECTION_LABELS[name]}, ${percent}% complete`}
              >
                <CompletenessDot percent={percent} />
                {SECTION_LABELS[name]}
              </TabsTrigger>
            );
          })}
        </TabsList>
        <TabsContent value={CV_TAB} className="flex flex-col gap-4">
          <CvTab />
        </TabsContent>
        {TAB_SECTIONS.map((name) => (
          <TabsContent key={name} value={name} className="flex flex-col gap-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <Missing
                missing={profile.completeness.sections[name]?.missing ?? []}
              />
              <div className="ml-auto">
                <HistoryDrawer
                  section={name}
                  profile={profile}
                  dirty={dirty}
                  onRestored={reloadTabs}
                />
              </div>
            </div>
            {/* A new key after a restore or an import: the editor reloads its form. */}
            <Fragment key={generation}>
              {name === "identity" ? (
                <IdentityTab profile={profile} onDirtyChange={onDirtyChange} />
              ) : name === "experience" ? (
                <ExperienceTab
                  profile={profile}
                  onDirtyChange={onDirtyChange}
                />
              ) : name === "answers" ? (
                <AnswersTab profile={profile} onDirtyChange={onDirtyChange} />
              ) : name === "facts" ? (
                <FactsTab profile={profile} onDirtyChange={onDirtyChange} />
              ) : name === "skills" ? (
                <SkillsTab profile={profile} onDirtyChange={onDirtyChange} />
              ) : (
                <SearchTab profile={profile} onDirtyChange={onDirtyChange} />
              )}
            </Fragment>
          </TabsContent>
        ))}
      </Tabs>
      <AlertDialog
        open={guardOpen}
        onOpenChange={(open) => {
          if (open) return;
          if (leaving) blocker.reset();
          setPendingTab(undefined);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
            <AlertDialogDescription>
              Your changes to {SECTION_LABELS[tab]} are not saved. Keep editing
              to save them first, or discard them.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                if (leaving) blocker.reset();
                setPendingTab(undefined);
              }}
            >
              Keep editing
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (leaving) {
                  setDirty(false);
                  blocker.proceed();
                } else if (pendingTab) {
                  setDirty(false);
                  discarding.current = true;
                  try {
                    openTab(pendingTab);
                  } finally {
                    discarding.current = false;
                  }
                  setPendingTab(undefined);
                }
              }}
            >
              Discard changes
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function Header({
  profile,
  actions,
}: {
  profile: Profile;
  actions: ReactNode;
}) {
  const overall = profile.completeness.overall;
  return (
    <header className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Profile</h1>
        {actions}
      </div>
      <p className="text-sm text-muted-foreground">
        Everything search and document generation read about you. Each section
        saves on its own.
      </p>
      <div>
        <Button asChild variant="outline">
          <Link to="/profile/fill">
            <Sparkles aria-hidden="true" />
            Fill my profile with an agent
          </Link>
        </Button>
      </div>
      <div className="flex flex-col gap-1.5">
        <div className="flex items-baseline justify-between text-sm">
          <span id="completeness-label" className="font-medium">
            Profile completeness
          </span>
          <span className="tabular-nums text-muted-foreground">{overall}%</span>
        </div>
        <div
          role="progressbar"
          aria-labelledby="completeness-label"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={overall}
          className="h-2 w-full overflow-hidden rounded-full bg-muted"
        >
          <div
            className="h-full rounded-full bg-primary transition-[width]"
            style={{ width: `${overall}%` }}
          />
        </div>
      </div>
    </header>
  );
}

/** Full when the section is complete, a ring when empty, half filled in between. */
function CompletenessDot({ percent }: { percent: number }) {
  return (
    <span
      aria-hidden="true"
      data-percent={percent}
      className={cn(
        "inline-block size-2 shrink-0 rounded-full border border-current",
        percent >= 100 &&
          "border-emerald-600 bg-emerald-600 dark:border-emerald-400 dark:bg-emerald-400",
        percent > 0 &&
          percent < 100 &&
          "border-amber-600 bg-[linear-gradient(90deg,currentColor_50%,transparent_50%)] text-amber-600 dark:border-amber-400 dark:text-amber-400",
      )}
    />
  );
}

function Missing({ missing }: { missing: string[] }) {
  if (missing.length === 0) return null;
  return (
    <p className="min-w-0 flex-1 basis-60 text-sm text-muted-foreground">
      To complete this section, add: {missing.map(missingLabel).join(", ")}.
    </p>
  );
}
