import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { ArrowLeft, LoaderCircle, Plus, RotateCw } from "lucide-react";
import { useId, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { getProfile, profileKey } from "@/features/profile/api";
import { describeError } from "@/lib/api";

import { ConsentDialog } from "./consent-dialog";
import { DocumentView } from "./document-view";
import { UploadPanel } from "./upload-panel";
import {
  acceptConsent,
  baseCvsKey,
  consentKey,
  createBaseCv,
  deleteBaseCv,
  documentKey,
  failureSentence,
  getConsent,
  listBaseCvs,
  patchBaseCv,
  type BaseCv,
  type BaseCvRequest,
  type BaseCvs,
} from "./documents-api";
import {
  failedSentence,
  formatWhen,
  isOpen,
  pollInterval,
  SOURCE_LABELS,
  templateLabel,
  WAIT_SENTENCES,
} from "./labels";

const LIST = "/profile/cv";
const NAME_MAX = 100;

const tooMany = (max: number) =>
  `You have ${String(max)} CVs, the most the app keeps. Delete one to add another.`;

const refresh = (client: QueryClient) =>
  client.invalidateQueries({ queryKey: baseCvsKey });

/**
 * The CV tab of the profile: the person's base CVs, one of them the default, and the document
 * view of the one opened (/profile/cv/:id). Building from the profile sends the profile to a
 * model, so it asks for the consent first; a copy sends nothing.
 */
export function CvTab() {
  const { id } = useParams();
  const client = useQueryClient();
  const list = useQuery({
    queryKey: baseCvsKey,
    queryFn: ({ signal }) => listBaseCvs(signal),
    refetchInterval: (current) => {
      const waits = (current.state.data?.base_cvs ?? [])
        .map((one) => pollInterval(one.render_state, one.generation))
        .filter((wait) => wait !== false);
      return waits.length > 0 ? Math.min(...waits) : false;
    },
  });
  const consent = useQuery({
    queryKey: consentKey,
    queryFn: ({ signal }) => getConsent(signal),
  });
  // The same read as the profile page's own, so it is already in the cache.
  const profile = useQuery({
    queryKey: profileKey,
    queryFn: ({ signal }) => getProfile(signal),
  });
  // The build that waits for the person's agreement.
  const [asking, setAsking] = useState<BaseCvRequest>();
  const [creating, setCreating] = useState(false);

  const create = useMutation({
    mutationFn: createBaseCv,
    onSuccess: () => refresh(client),
    onError: (error) => {
      toast.error(failureSentence(error));
      void refresh(client);
    },
  });
  const agree = useMutation({
    mutationFn: async (version: number) => {
      const accepted = await acceptConsent(version);
      client.setQueryData(consentKey, accepted);
      return accepted;
    },
    // A 422 means the text changed under the open tab: read the current one to agree to.
    onError: () => {
      void client.invalidateQueries({ queryKey: consentKey });
    },
  });

  if (list.isPending) {
    return (
      <div
        role="status"
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
        Loading your CVs
      </div>
    );
  }
  if (list.isError) {
    return (
      <div className="flex flex-col gap-3">
        <Alert variant="destructive">
          <AlertTitle>Your CVs could not be loaded</AlertTitle>
          <AlertDescription>{describeError(list.error)}</AlertDescription>
        </Alert>
        <div>
          <Button
            variant="outline"
            disabled={list.isFetching}
            onClick={() => void list.refetch()}
          >
            <RotateCw aria-hidden="true" />
            Retry
          </Button>
        </div>
      </div>
    );
  }

  const { base_cvs: cvs, max } = list.data;
  const opened =
    id === undefined ? undefined : cvs.find((one) => one.id === id);

  /** A build from the profile needs the consent; anything else goes straight through. */
  const start = (request: BaseCvRequest) => {
    if (request.from === "profile" && consent.data?.accepted !== true) {
      if (consent.data === undefined) {
        toast.error(
          "What is sent could not be loaded yet. Try again in a moment.",
        );
        return;
      }
      setCreating(false);
      setAsking(request);
      return;
    }
    create.mutate(request, {
      onSuccess: () => {
        setCreating(false);
      },
    });
  };

  return (
    <div className="flex flex-col gap-4">
      {id !== undefined ? (
        opened === undefined ? (
          <NotFound />
        ) : (
          <Opened cv={opened} cvs={cvs} />
        )
      ) : (
        <Overview
          cvs={cvs}
          max={max}
          building={create.isPending}
          consentMissing={consent.data?.accepted === false}
          minFit={consent.data?.min_fit}
          consentReady={consent.data !== undefined}
          consentError={consent.isError}
          noExperience={
            profile.data !== undefined &&
            profile.data.sections.experience.employers.length === 0
          }
          onNew={() => {
            setCreating(true);
          }}
          onBuild={() => {
            start({ from: "profile" });
          }}
        />
      )}

      {id === undefined ? (
        <UploadPanel openHref={(to) => `${LIST}/${to}`} />
      ) : null}
      <NewCvDialog
        key={creating ? "open" : "closed"}
        open={creating}
        cvs={cvs}
        busy={create.isPending}
        onClose={() => {
          setCreating(false);
        }}
        onCreate={start}
      />
      {consent.data !== undefined && asking !== undefined ? (
        <ConsentDialog
          consent={consent.data}
          open
          busy={agree.isPending || create.isPending}
          error={agree.isError ? failureSentence(agree.error) : undefined}
          onClose={() => {
            agree.reset();
            setAsking(undefined);
          }}
          onAgree={(version) => {
            agree.mutate(version, {
              onSuccess: () => {
                const request = asking;
                setAsking(undefined);
                setCreating(false);
                create.mutate(request);
              },
            });
          }}
        />
      ) : null}
    </div>
  );
}

// --- the list -----------------------------------------------------------------------------------

function Overview({
  cvs,
  max,
  building,
  consentMissing,
  consentReady,
  consentError,
  noExperience,
  minFit,
  onNew,
  onBuild,
}: {
  cvs: BaseCv[];
  max: number;
  building: boolean;
  consentMissing: boolean;
  consentReady: boolean;
  consentError: boolean;
  noExperience: boolean;
  minFit: number | undefined;
  onNew: () => void;
  onBuild: () => void;
}) {
  const full = cvs.length >= max;
  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h2 className="text-lg font-semibold tracking-tight">Your CVs</h2>
          <p className="text-sm text-muted-foreground">
            The default CV is the one every other document is written from.
          </p>
        </div>
        {cvs.length > 0 ? (
          <div className="flex flex-col items-end gap-1">
            <Button disabled={full || building} onClick={onNew}>
              <Plus aria-hidden="true" />
              New CV
            </Button>
            {full ? (
              <p className="max-w-64 text-right text-xs text-muted-foreground">
                {tooMany(max)}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>

      {cvs.length === 0 ? (
        <section
          aria-labelledby="empty-heading"
          className="flex flex-col items-start gap-3 rounded-lg border border-dashed p-4"
        >
          <h3 id="empty-heading" className="font-semibold">
            You have no CV yet
          </h3>
          <p className="text-sm text-muted-foreground">
            The app can write one from your profile. It becomes the default and
            every document for a posting starts from it.
          </p>
          {noExperience ? (
            <p className="text-sm font-medium" data-testid="no-experience-line">
              Add your CV or fill your experience to get documents for each
              posting.
            </p>
          ) : null}
          {consentMissing ? (
            <p className="text-sm font-medium" data-testid="confirm-line">
              Confirm what is sent to build your CV.
            </p>
          ) : null}
          {consentError ? (
            <p className="text-sm text-destructive">
              What is sent could not be loaded. Reload the page to try again.
            </p>
          ) : null}
          <Button disabled={building || !consentReady} onClick={onBuild}>
            {building ? (
              <LoaderCircle aria-hidden="true" className="animate-spin" />
            ) : null}
            Build it from my profile
          </Button>
        </section>
      ) : (
        <ul className="flex flex-col gap-3" aria-label="Your CVs">
          {cvs.map((one) => (
            <li
              key={one.id}
              className="flex flex-col gap-3 rounded-lg border p-3"
            >
              <Row cv={one} only={cvs.length === 1} />
              <Actions cv={one} cvs={cvs} />
            </li>
          ))}
        </ul>
      )}

      {minFit !== undefined ? (
        <p className="text-sm text-muted-foreground" data-testid="threshold">
          Documents are written for new postings with a fit of {minFit} or more.{" "}
          <Link
            to="/profile?tab=search"
            className="font-medium text-foreground underline underline-offset-4"
          >
            Change it in the Search options
          </Link>
          .
        </p>
      ) : null}
    </>
  );
}

/** What a CV is doing, in a sentence; null when it is simply there. */
function stateLine(cv: BaseCv, only: boolean): string | null {
  const generation = cv.generation;
  if (generation === null) return null;
  if (generation.status === "waiting")
    return generation.wait_reason
      ? WAIT_SENTENCES[generation.wait_reason]
      : "Waiting to start.";
  if (isOpen(generation))
    return cv.last_version_at === null && only
      ? "Your first CV is being built."
      : cv.last_version_at === null
        ? "Being built."
        : "Writing a new version.";
  if (generation.status === "failed") return failedSentence(generation.error);
  return null;
}

function Row({ cv, only }: { cv: BaseCv; only: boolean }) {
  const line = stateLine(cv, only);
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <Link
          to={`${LIST}/${cv.id}`}
          className="min-w-0 break-words text-base font-semibold underline-offset-4 hover:underline"
        >
          {cv.name}
        </Link>
        {cv.is_default ? <Badge>Default</Badge> : null}
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>{SOURCE_LABELS[cv.source]}</span>
        <span>Template: {templateLabel(cv.template_slug)}</span>
        <span>
          {cv.last_version_at === null
            ? "No version yet"
            : `Last version ${formatWhen(cv.last_version_at)}`}
        </span>
      </div>
      {line !== null ? (
        <p
          role="status"
          className="flex items-center gap-2 text-sm text-muted-foreground"
        >
          {isOpen(cv.generation) && cv.generation?.status !== "waiting" ? (
            <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          ) : null}
          {line}
        </p>
      ) : null}
    </div>
  );
}

// --- one CV -------------------------------------------------------------------------------------

function Opened({ cv, cvs }: { cv: BaseCv; cvs: BaseCv[] }) {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <Button asChild variant="ghost" size="sm">
          <Link to={LIST}>
            <ArrowLeft aria-hidden="true" />
            All my CVs
          </Link>
        </Button>
      </div>
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="min-w-0 break-words text-lg font-semibold tracking-tight">
            {cv.name}
          </h2>
          {cv.is_default ? <Badge>Default</Badge> : null}
        </div>
        <p className="text-sm text-muted-foreground">
          {SOURCE_LABELS[cv.source]}
        </p>
        <Actions cv={cv} cvs={cvs} backToList />
      </div>
      <DocumentView documentId={cv.id} generation={cv.generation} />
    </div>
  );
}

function NotFound() {
  return (
    <div className="flex flex-col items-start gap-3">
      <Alert>
        <AlertTitle>This CV does not exist</AlertTitle>
        <AlertDescription>
          It may have been deleted in another tab.
        </AlertDescription>
      </Alert>
      <Button asChild variant="outline">
        <Link to={LIST}>
          <ArrowLeft aria-hidden="true" />
          All my CVs
        </Link>
      </Button>
    </div>
  );
}

// --- make default, rename, delete -----------------------------------------------------------------

function Actions({
  cv,
  cvs,
  backToList = false,
}: {
  cv: BaseCv;
  cvs: BaseCv[];
  backToList?: boolean;
}) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const makeDefault = useMutation({
    mutationFn: () => patchBaseCv(cv.id, { default: true }),
    onSuccess: () => refresh(client),
    onError: (error) => {
      toast.error(failureSentence(error));
    },
  });
  const remove = useMutation({
    mutationFn: () => deleteBaseCv(cv.id),
    onSuccess: async () => {
      setDeleting(false);
      client.removeQueries({ queryKey: documentKey(cv.id) });
      await refresh(client);
      if (backToList) void navigate(LIST);
    },
    onError: (error) => {
      setDeleting(false);
      toast.error(failureSentence(error));
      void refresh(client);
    },
  });

  const others = cvs.length - 1;
  return (
    <div className="flex flex-wrap gap-2">
      {cv.is_default ? null : (
        <Button
          variant="outline"
          size="sm"
          disabled={makeDefault.isPending}
          aria-label={`Make default for ${cv.name}`}
          onClick={() => {
            makeDefault.mutate();
          }}
        >
          Make default
        </Button>
      )}
      <Button
        variant="outline"
        size="sm"
        aria-label={`Rename ${cv.name}`}
        onClick={() => {
          setRenaming(true);
        }}
      >
        Rename
      </Button>
      <Button
        variant="outline"
        size="sm"
        aria-label={`Delete ${cv.name}`}
        onClick={() => {
          setDeleting(true);
        }}
      >
        Delete
      </Button>

      <RenameDialog
        key={renaming ? "open" : "closed"}
        cv={cv}
        open={renaming}
        onClose={() => {
          setRenaming(false);
        }}
      />
      <AlertDialog
        open={deleting}
        onOpenChange={(open) => {
          if (!open && !remove.isPending) setDeleting(false);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {cv.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              {cv.is_default && others > 0
                ? "It is the default: the oldest other CV becomes the default. "
                : cv.is_default
                  ? "It is your only CV: while documents are written automatically, a new one is built from your profile. "
                  : ""}
              The documents already written for your postings from this CV stay
              as they are.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <Button
              variant="outline"
              disabled={remove.isPending}
              onClick={() => {
                setDeleting(false);
              }}
            >
              Keep it
            </Button>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => {
                remove.mutate();
              }}
            >
              Delete this CV
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function RenameDialog({
  cv,
  open,
  onClose,
}: {
  cv: BaseCv;
  open: boolean;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const inputId = useId();
  const [name, setName] = useState(cv.name);
  const trimmed = name.trim();
  const save = useMutation({
    mutationFn: () => patchBaseCv(cv.id, { name: trimmed }),
    onSuccess: async () => {
      await refresh(client);
      onClose();
    },
  });
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
    >
      <DialogContent>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (trimmed !== "" && !save.isPending) save.mutate();
          }}
        >
          <DialogHeader>
            <DialogTitle>Rename this CV</DialogTitle>
            <DialogDescription>
              The name is only for you, in this list and in the selector of a
              posting.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-1.5">
            <label htmlFor={inputId} className="text-sm font-medium">
              Name
            </label>
            <Input
              id={inputId}
              value={name}
              maxLength={NAME_MAX}
              autoComplete="off"
              onChange={(event) => {
                setName(event.target.value);
              }}
            />
            {save.isError ? (
              <p role="alert" className="text-sm text-destructive">
                {failureSentence(save.error)}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={save.isPending}
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={trimmed === "" || save.isPending}>
              Save name
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// --- New CV -------------------------------------------------------------------------------------

function NewCvDialog({
  open,
  cvs,
  busy,
  onClose,
  onCreate,
}: {
  open: boolean;
  cvs: BaseCvs["base_cvs"];
  busy: boolean;
  onClose: () => void;
  onCreate: (request: BaseCvRequest) => void;
}) {
  const nameId = useId();
  const copyId = useId();
  const [source, setSource] = useState<"profile" | "copy">("profile");
  const [copyOf, setCopyOf] = useState("");
  const [name, setName] = useState("");
  const chosen = cvs.find((one) => one.id === copyOf)?.id ?? cvs[0]?.id;
  const trimmed = name.trim();

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && !busy) onClose();
      }}
    >
      <DialogContent>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (busy) return;
            if (source === "copy" && chosen === undefined) return;
            onCreate({
              from: source,
              ...(source === "copy" ? { copy_of: chosen } : {}),
              ...(trimmed === "" ? {} : { name: trimmed }),
            });
          }}
        >
          <DialogHeader>
            <DialogTitle>New CV</DialogTitle>
            <DialogDescription>
              Start from your profile, or from a copy of a CV you already have.
            </DialogDescription>
          </DialogHeader>
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-sm font-medium">Start from</legend>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="new-cv-source"
                className="size-4 accent-primary"
                checked={source === "profile"}
                onChange={() => {
                  setSource("profile");
                }}
              />
              My profile
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="new-cv-source"
                className="size-4 accent-primary"
                checked={source === "copy"}
                onChange={() => {
                  setSource("copy");
                }}
              />
              A copy of another CV
            </label>
          </fieldset>
          {source === "copy" ? (
            <div className="flex flex-col gap-1.5">
              <label htmlFor={copyId} className="text-sm font-medium">
                CV to copy
              </label>
              <NativeSelect
                id={copyId}
                value={chosen ?? ""}
                onChange={(event) => {
                  setCopyOf(event.target.value);
                }}
              >
                {cvs.map((one) => (
                  <option key={one.id} value={one.id}>
                    {one.name}
                  </option>
                ))}
              </NativeSelect>
            </div>
          ) : null}
          <div className="flex flex-col gap-1.5">
            <label htmlFor={nameId} className="text-sm font-medium">
              Name (optional)
            </label>
            <Input
              id={nameId}
              value={name}
              maxLength={NAME_MAX}
              autoComplete="off"
              onChange={(event) => {
                setName(event.target.value);
              }}
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={busy}>
              {busy ? (
                <LoaderCircle aria-hidden="true" className="animate-spin" />
              ) : null}
              {source === "copy" ? "Make a copy" : "Build it"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
