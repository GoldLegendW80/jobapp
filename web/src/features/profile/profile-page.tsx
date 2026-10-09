import { useQuery } from "@tanstack/react-query";
import { LoaderCircle, RotateCw } from "lucide-react";
import {
  Fragment,
  useCallback,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useBlocker, useSearchParams } from "react-router";

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
  SECTIONS,
  type Profile,
  type SectionName,
} from "./api";
import { AnswersTab } from "./answers-tab";
import { ExperienceTab } from "./experience-tab";
import { FactsTab } from "./facts-tab";
import { HistoryDrawer } from "./history-drawer";
import { IdentityTab } from "./identity-tab";
import { missingLabel, SECTION_LABELS } from "./labels";
import { ProfileFileMenu } from "./profile-file";
import { SearchTab } from "./search-tab";
import { SkillsTab } from "./skills-tab";

const isSection = (value: string | null): value is SectionName =>
  value !== null && (SECTIONS as readonly string[]).includes(value);

/**
 * /profile: the completeness of the whole profile, then one tab per section, each an editor that
 * saves on its own.
 * The open tab lives in `?tab=`. With unsaved changes, switching tab, following a link away or
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
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab");
  const tab: SectionName = isSection(requested) ? requested : "identity";
  const [dirty, setDirty] = useState(false);
  const [pendingTab, setPendingTab] = useState<SectionName>();
  // Bumped after a restore or an import: the tabs remount and read the new current version.
  const [generation, setGeneration] = useState(0);
  const reloadTabs = useCallback(() => {
    setDirty(false);
    setGeneration((value) => value + 1);
  }, []);
  const onDirtyChange = useCallback((value: boolean) => {
    setDirty(value);
  }, []);

  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty && currentLocation.pathname !== nextLocation.pathname,
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

  const openTab = (next: SectionName) => {
    setParams(
      (old) => {
        const updated = new URLSearchParams(old);
        updated.set("tab", next);
        return updated;
      },
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
        value={tab}
        onValueChange={(value) => {
          if (!isSection(value) || value === tab) return;
          if (dirty) setPendingTab(value);
          else openTab(value);
        }}
      >
        <TabsList
          aria-label="Profile sections"
          className="grid h-auto grid-cols-3 gap-1 sm:grid-cols-6"
        >
          {SECTIONS.map((name) => {
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
        {SECTIONS.map((name) => (
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
                  openTab(pendingTab);
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
