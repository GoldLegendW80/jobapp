/**
 * /profile as one page, no tabs (product audit, chapter 2, analysis B6 and B7; owner: "Always
 * think about navigation and number of click"). Top to bottom: the photo, name and headline with
 * "Ready for" (in place of the completeness bar: three lines, each with the one button that fixes
 * it); Your CV; What you look for (the search lists with the job title ideas); About you (the
 * Markdown document, about-document.tsx); Contact and links (the identity form). History, Export,
 * Import and the classic editor (the old tabs, kept for one release) are under More.
 */
import { useMutation, useQueryClient, useQuery } from "@tanstack/react-query";
import {
  CircleCheck,
  CircleDashed,
  Download,
  History,
  MoreHorizontal,
  RotateCw,
  Upload,
} from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, useBlocker } from "react-router";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Form } from "@/components/ui/form";
import { Spinner } from "@/components/ui/spinner";
import { CvTab } from "@/features/documents/cv-tab";
import { describeError } from "@/lib/api";
import { useTitle } from "@/lib/use-title";

import { AboutDocument } from "./about-document";
import { ABOUT_WORDS } from "./about-words";
import {
  exportProfile,
  getProfile,
  photoUrl,
  profileKey,
  SECTIONS,
  type Profile,
  type SectionName,
} from "./api";
import { downloadJson } from "./download";
import { searchSchema, fromSearchForm, toSearchForm } from "./editor-forms";
import { HistoryDrawer } from "./history-drawer";
import { IdentityTab } from "./identity-tab";
import { exportFileName } from "./import-file";
import { SECTION_LABELS } from "./labels";
import { ImportDialog } from "./profile-file";
import { SearchLists } from "./search-lists";
import { readiness } from "./readiness";
import { ConflictDialog, FormErrorAlert, SaveBar } from "./section-ui";
import { useSectionForm } from "./use-section-form";

const H2 = "text-lg font-semibold tracking-tight";
const MUTED = "text-sm text-muted-foreground";

export function OneProfilePage() {
  useTitle("Profile");
  const query = useQuery({
    queryKey: profileKey,
    queryFn: ({ signal }) => getProfile(signal),
  });
  const [dirty, setDirty] = useState({ search: false, identity: false });
  const [generation, setGeneration] = useState(0);
  const onSearchDirty = useCallback((value: boolean) => {
    setDirty((was) => ({ ...was, search: value }));
  }, []);
  const onIdentityDirty = useCallback((value: boolean) => {
    setDirty((was) => ({ ...was, identity: value }));
  }, []);
  const anyDirty = dirty.search || dirty.identity;
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      anyDirty &&
      currentLocation.pathname + currentLocation.search !==
        nextLocation.pathname + nextLocation.search,
  );
  useEffect(() => {
    if (!anyDirty) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
    };
  }, [anyDirty]);

  if (query.isPending)
    return (
      <div
        role="status"
        className="mx-auto flex max-w-5xl items-center gap-2 text-sm text-muted-foreground"
      >
        <Spinner aria-hidden="true" />
        Loading your profile
      </div>
    );
  if (query.isError)
    return (
      <div className="mx-auto flex max-w-5xl flex-col gap-4">
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

  const profile = query.data;
  const reload = () => {
    setDirty({ search: false, identity: false });
    setGeneration((value) => value + 1);
  };
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Profile</h1>
        <More profile={profile} dirty={anyDirty} onChanged={reload} />
      </div>
      <Who profile={profile} />
      {/* The CV card names itself ("Your CVs"): no second heading above it. */}
      <section
        id="your-cv"
        aria-label="Your CV"
        className="flex scroll-mt-4 flex-col gap-3"
      >
        <CvTab />
        <p className={MUTED}>
          Other documents, such as certificates or a portfolio, fill your
          profile too.{" "}
          <Link
            to="/profile/fill"
            className="font-medium text-foreground underline underline-offset-4"
          >
            Add other documents
          </Link>
        </p>
      </section>
      <Part id="looking-for" title="What you look for">
        <LookingFor
          key={generation}
          profile={profile}
          onDirtyChange={onSearchDirty}
        />
      </Part>
      <Part id="about-you" title={ABOUT_WORDS.title} intro={ABOUT_WORDS.intro}>
        <AboutDocument />
      </Part>
      <Part
        id="contact"
        title="Contact and links"
        intro="Your name, contact details, links, languages and photo, as every document shows them."
      >
        <IdentityTab
          key={generation}
          profile={profile}
          onDirtyChange={onIdentityDirty}
        />
      </Part>
      <AlertDialog
        open={blocker.state === "blocked"}
        onOpenChange={(open) => {
          if (!open) blocker.reset?.();
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Discard unsaved changes?</AlertDialogTitle>
            <AlertDialogDescription>
              Your changes to{" "}
              {dirty.search ? "What you look for" : "Contact and links"} are not
              saved. Keep editing to save them first, or discard them.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <Button variant="outline" onClick={() => blocker.reset?.()}>
              Keep editing
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                setDirty({ search: false, identity: false });
                blocker.proceed?.();
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

function Part({
  id,
  title,
  intro,
  children,
}: {
  id: string;
  title: string;
  intro?: string;
  children: ReactNode;
}) {
  return (
    <section
      id={id}
      aria-labelledby={`${id}-title`}
      className="flex scroll-mt-4 flex-col gap-4"
    >
      <div className="flex flex-col gap-1">
        <h2 id={`${id}-title`} className={H2}>
          {title}
        </h2>
        {intro ? <p className={MUTED}>{intro}</p> : null}
      </div>
      {children}
    </section>
  );
}

function Who({ profile }: { profile: Profile }) {
  const identity = profile.sections.identity;
  const name = identity.full_name ?? "Your name";
  const initials =
    name
      .split(/\s+/)
      .map((word) => word[0] ?? "")
      .join("")
      .slice(0, 2)
      .toUpperCase() || "?";
  return (
    <section
      aria-label="You"
      className="flex flex-col gap-4 rounded-lg border p-4 sm:flex-row sm:items-start sm:gap-6"
    >
      <div className="flex items-center gap-4 sm:min-w-56">
        <Avatar className="size-16 border">
          {profile.has_photo && profile.photo_etag ? (
            <AvatarImage src={photoUrl(profile.photo_etag)} alt="" />
          ) : null}
          <AvatarFallback>{initials}</AvatarFallback>
        </Avatar>
        <div className="min-w-0">
          <p className="break-words font-semibold">{name}</p>
          {identity.headline ? (
            <p className={MUTED}>{identity.headline}</p>
          ) : (
            <a href="#contact" className={`${MUTED} underline`}>
              Add a headline
            </a>
          )}
        </div>
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <p id="ready-for" className="text-sm font-medium">
          Ready for
        </p>
        <ul aria-labelledby="ready-for" className="flex flex-col gap-2">
          {readiness(profile).map((line) => (
            <li
              key={line.name}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm"
            >
              {line.ready ? (
                <CircleCheck
                  aria-hidden="true"
                  className="size-4 text-emerald-600 dark:text-emerald-400"
                />
              ) : (
                <CircleDashed
                  aria-hidden="true"
                  className="size-4 text-muted-foreground"
                />
              )}
              <span>
                {line.name}
                <span className="sr-only">
                  {line.ready ? ": ready" : ": not ready yet"}
                </span>
              </span>
              {line.fix ? (
                <Button asChild variant="outline" size="sm">
                  <a href={line.fix.href}>{line.fix.label}</a>
                </Button>
              ) : null}
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function LookingFor({
  profile,
  onDirtyChange,
}: {
  profile: Profile;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const { form, onSubmit, saving, formError, conflict, keepEditing, reload } =
    useSectionForm({
      section: "search",
      profile,
      schema: searchSchema,
      toForm: toSearchForm,
      fromForm: fromSearchForm,
      rules: { wrapped: ["never_apply"] },
      onDirtyChange,
    });
  return (
    <>
      <Form {...form}>
        <form
          noValidate
          aria-label="What you look for"
          className="flex flex-col gap-4"
          onSubmit={(event) => void onSubmit(event)}
        >
          <FormErrorAlert message={formError} />
          <SearchLists form={form} />
          <p className={MUTED}>
            Websites, contracts, remote work and pay are set where you search.{" "}
            <Link
              to="/jobs?find=1"
              className="font-medium text-foreground underline underline-offset-4"
            >
              Websites and more in Find jobs
            </Link>
          </p>
          <SaveBar
            label="Search"
            saving={saving}
            dirty={form.formState.isDirty}
          />
        </form>
      </Form>
      <ConflictDialog
        open={conflict}
        label="Search"
        onKeepEditing={keepEditing}
        onReload={reload}
      />
    </>
  );
}

function More({
  profile,
  dirty,
  onChanged,
}: {
  profile: Profile;
  dirty: boolean;
  onChanged: () => void;
}) {
  const queryClient = useQueryClient();
  const [history, setHistory] = useState<SectionName>();
  const [importing, setImporting] = useState(false);
  const exporting = useMutation({
    mutationFn: exportProfile,
    onSuccess: (file) => {
      const name = exportFileName(file.exported_at);
      downloadJson(file, name);
      toast.success(`Profile exported as ${name}.`);
    },
    onError: (error) => {
      toast.error(`The profile was not exported. ${describeError(error)}`);
    },
  });
  const changed = () => {
    void queryClient.invalidateQueries({ queryKey: ["profile-about"] });
    onChanged();
  };
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" aria-busy={exporting.isPending}>
            {exporting.isPending ? (
              <Spinner aria-hidden="true" />
            ) : (
              <MoreHorizontal aria-hidden="true" />
            )}
            More
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>
              <History aria-hidden="true" />
              History
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
              {SECTIONS.filter((name) => name !== "preferences").map((name) => (
                <DropdownMenuItem
                  key={name}
                  onSelect={() => {
                    setHistory(name);
                  }}
                >
                  {SECTION_LABELS[name]}
                </DropdownMenuItem>
              ))}
            </DropdownMenuSubContent>
          </DropdownMenuSub>
          <DropdownMenuItem
            onSelect={() => {
              exporting.mutate();
            }}
          >
            <Download aria-hidden="true" />
            Export profile
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => {
              setImporting(true);
            }}
          >
            <Upload aria-hidden="true" />
            Import profile
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem asChild>
            <Link to="/profile?tab=identity">Classic editor</Link>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <HistoryDrawer
        section={history ?? "identity"}
        profile={profile}
        dirty={dirty}
        open={history !== undefined}
        onOpenChange={(open) => {
          if (!open) setHistory(undefined);
        }}
        onRestored={changed}
      />
      <ImportDialog
        open={importing}
        onOpenChange={setImporting}
        profile={profile}
        dirtySection={dirty ? "identity" : undefined}
        onImported={changed}
      />
    </>
  );
}
