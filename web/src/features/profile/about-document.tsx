/**
 * "About you": the person's Markdown document (owner, 2026-11-05: "just put informations on a .md
 * should be well rendered and editable", "a markdown that a model interpret"). Each part (a `##`
 * heading) is shown rendered by the safe renderer and edited in place, one at a time, with a live
 * preview; the text saves as the person types (AUTOSAVE_MS, as the CV editor). Done reads the
 * part with the model; under it, "What the app understood" lists the changes with one Apply, then
 * Undo. Without a model key the text still saves and a line under Done says what is missing. The
 * contents list is sticky on desktop and a row of chips on phones.
 */
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { FilePlus2, Pencil, RotateCw, Undo2 } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { AUTOSAVE_MS } from "@/features/documents/edit-autosave";
import { money } from "@/features/documents/labels";
import { describeError } from "@/lib/api";

import {
  aboutKey,
  actOnReading,
  demote,
  doneAbout,
  joinParts,
  keyOf,
  rewriteAbout,
  saveAbout,
  splitParts,
  staleAbout,
  useAbout,
  type About,
  type AboutPart,
  type Part,
  type PartKind,
  type Reading,
} from "./about-api";
import { ABOUT_WORDS as W } from "./about-words";
import { profileKey } from "./api";
import { Markdown } from "./markdown";

const MUTED = "text-sm text-muted-foreground";
const LINK = "font-medium text-foreground underline underline-offset-4";

type SaveState =
  | { state: "saved" }
  | { state: "waiting" }
  | { state: "saving" }
  | { state: "failed"; message: string }
  | { state: "conflict"; theirs: About };

function createSaver(
  initial: About,
  queryClient: QueryClient,
  set: { text: (text: string) => void; status: (status: SaveState) => void },
) {
  let version = initial.version;
  let sent = initial.text;
  let latest = initial.text;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let inFlight: Promise<About | undefined> | undefined;
  let theirs: About | undefined;

  const schedule = () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      void save();
    }, AUTOSAVE_MS);
  };

  async function save(): Promise<About | undefined> {
    clearTimeout(timer);
    if (theirs) return undefined;
    if (inFlight) {
      await inFlight;
      return save();
    }
    if (latest === sent) {
      set.status({ state: "saved" });
      return queryClient.getQueryData<About>(aboutKey);
    }
    const sending = latest;
    set.status({ state: "saving" });
    inFlight = saveAbout(version, sending).then(
      (answer) => {
        version = answer.version;
        sent = sending;
        inFlight = undefined;
        queryClient.setQueryData(aboutKey, answer);
        if (latest === sent) set.status({ state: "saved" });
        else {
          set.status({ state: "waiting" });
          schedule();
        }
        return answer;
      },
      (error: unknown) => {
        inFlight = undefined;
        theirs = staleAbout(error);
        set.status(
          theirs
            ? { state: "conflict", theirs }
            : { state: "failed", message: describeError(error) },
        );
        return undefined;
      },
    );
    return inFlight;
  }

  return {
    flush: save,
    dirty: () => latest !== sent,
    change: (next: string) => {
      latest = next;
      set.text(next);
      if (theirs) return;
      set.status({ state: "waiting" });
      schedule();
    },
    /** A text saved elsewhere (a rewrite, Done), taken while nothing typed here waits. */
    adopt: (about: About) => {
      if (latest !== sent || inFlight || theirs) return;
      latest = sent = about.text;
      version = about.version;
      set.text(about.text);
    },
    settle: (keep: "theirs" | "mine") => {
      if (!theirs) return;
      const current = theirs;
      theirs = undefined;
      version = current.version;
      queryClient.setQueryData(aboutKey, current);
      if (keep === "theirs") {
        latest = sent = current.text;
        set.text(current.text);
        set.status({ state: "saved" });
      } else {
        sent = current.text;
        void save();
      }
    },
  };
}

/**
 * The document's saves, in order: never two at once, each on the version the one before
 * answered; typing during a save sends the newer text after it. A 409 stops the saves until the
 * person loads the latest or keeps theirs.
 */
function useDocument(about: About) {
  const queryClient = useQueryClient();
  const [text, setText] = useState(about.text);
  const [status, setStatus] = useState<SaveState>({ state: "saved" });
  const [saver] = useState(() =>
    createSaver(about, queryClient, { text: setText, status: setStatus }),
  );
  useEffect(() => {
    saver.adopt(about);
  }, [saver, about]);
  // Leaving the page sends what waits; closing the tab with it asks first.
  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (saver.dirty()) event.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      if (saver.dirty()) void saver.flush();
    };
  }, [saver]);
  return {
    text,
    status,
    change: saver.change,
    flush: saver.flush,
    settle: saver.settle,
  };
}

const anchor = (index: number) => `about-part-${index}`;

export function AboutDocument() {
  const query = useAbout();
  if (query.isPending)
    return (
      <p role="status" className={`flex items-center gap-2 ${MUTED}`}>
        <Spinner aria-hidden="true" />
        {W.loading}
      </p>
    );
  if (query.isError)
    return (
      <Alert variant="destructive">
        <AlertTitle>{W.notLoaded}</AlertTitle>
        <AlertDescription>{describeError(query.error)}</AlertDescription>
        <Button
          variant="outline"
          size="sm"
          className="mt-2 w-fit"
          onClick={() => void query.refetch()}
        >
          <RotateCw aria-hidden="true" />
          Retry
        </Button>
      </Alert>
    );
  return <Document about={query.data} />;
}

function Document({ about }: { about: About }) {
  const queryClient = useQueryClient();
  const doc = useDocument(about);
  const [editing, setEditing] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const { preamble, parts } = splitParts(doc.text);

  const act = async (call: () => Promise<About>) => {
    setBusy(true);
    try {
      const answer = await call();
      queryClient.setQueryData(aboutKey, answer);
      await queryClient.invalidateQueries({ queryKey: profileKey });
    } catch (error) {
      const theirs = staleAbout(error);
      if (theirs) queryClient.setQueryData(aboutKey, theirs);
      toast.error(describeError(error));
    } finally {
      setBusy(false);
    }
  };

  const setPart = (index: number, part: Part) => {
    const next = parts.map((one, i) => (i === index ? part : one));
    doc.change(joinParts(preamble, next));
  };

  const addPart = () => {
    const taken = new Set(parts.map((part) => keyOf(part.heading)));
    let heading: string = W.newPart;
    for (let n = 2; taken.has(keyOf(heading)); n += 1)
      heading = `${W.newPart} ${n}`;
    doc.change(joinParts(preamble, [...parts, { heading, body: "" }]));
    setEditing(parts.length);
  };

  const done = async (heading: string): Promise<boolean> => {
    const saved = await doc.flush();
    if (!saved) return false;
    if (saved.needs === "model_key") return true;
    try {
      queryClient.setQueryData(
        aboutKey,
        await doneAbout(heading, saved.version),
      );
      return true;
    } catch (error) {
      const theirs = staleAbout(error);
      if (theirs) queryClient.setQueryData(aboutKey, theirs);
      toast.error(describeError(error));
      return false;
    }
  };

  return (
    <div className="flex flex-col gap-4">
      {doc.status.state === "conflict" ? (
        <Alert variant="destructive">
          <AlertTitle>{W.conflictTitle}</AlertTitle>
          <AlertDescription>{W.conflict}</AlertDescription>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button
              size="sm"
              onClick={() => {
                doc.settle("theirs");
              }}
            >
              {W.loadLatest}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                doc.settle("mine");
              }}
            >
              {W.keepMine}
            </Button>
          </div>
        </Alert>
      ) : null}
      <div className="flex flex-col gap-4 lg:grid lg:grid-cols-[11rem_minmax(0,1fr)] lg:gap-6">
        <nav
          aria-label={W.contents}
          className="lg:sticky lg:top-4 lg:self-start"
        >
          <ul className="flex flex-wrap gap-2 lg:flex-col lg:gap-1">
            {parts.map((part, index) => (
              <li key={`${index}-${part.heading}`} className="min-w-0">
                <a
                  href={`#${anchor(index)}`}
                  className="inline-flex max-w-full truncate rounded-full border px-3 py-1 text-sm hover:bg-accent lg:rounded-md lg:border-0 lg:px-2"
                >
                  {part.heading}
                </a>
              </li>
            ))}
          </ul>
        </nav>
        <div className="flex min-w-0 flex-col gap-4">
          {parts.map((part, index) => {
            const shown = about.parts.find(
              (p) => p.key === keyOf(part.heading),
            );
            const kind = shown?.kind ?? "facts";
            // The first part of each kind is where "Ready for" sends the person.
            const first =
              kind !== "facts" &&
              parts.findIndex(
                (one) =>
                  about.parts.find((p) => p.key === keyOf(one.heading))
                    ?.kind === kind,
              ) === index;
            return (
              <PartView
                kindAnchor={first ? `about-${kind}` : undefined}
                key={index}
                index={index}
                about={about}
                part={part}
                shown={shown}
                editing={editing === index}
                busy={busy}
                status={doc.status}
                onEdit={() => {
                  setEditing(index);
                }}
                onChange={(next) => {
                  setPart(index, next);
                }}
                onDone={async (heading) => {
                  if (await done(heading)) setEditing(null);
                }}
                onAct={(call) => void act(call)}
              />
            );
          })}
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="outline"
              onClick={addPart}
              disabled={editing !== null}
            >
              <FilePlus2 aria-hidden="true" />
              {W.addPart}
            </Button>
            {about.missing_blocks.length > 0 ? (
              <p className={MUTED}>
                {W.missing(about.missing_blocks.length)}{" "}
                <Button
                  variant="link"
                  className="h-auto p-0"
                  disabled={busy}
                  onClick={() => {
                    void act(() => rewriteAbout(null, about.version));
                  }}
                >
                  {W.addThem}
                </Button>
              </p>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

function PartView({
  kindAnchor,
  index,
  about,
  part,
  shown,
  editing,
  busy,
  status,
  onEdit,
  onChange,
  onDone,
  onAct,
}: {
  kindAnchor: string | undefined;
  index: number;
  about: About;
  part: Part;
  shown: AboutPart | undefined;
  editing: boolean;
  busy: boolean;
  status: SaveState;
  onEdit: () => void;
  onChange: (part: Part) => void;
  onDone: (heading: string) => Promise<void>;
  onAct: (call: () => Promise<About>) => void;
}) {
  const kind: PartKind = shown?.kind ?? "facts";
  const key = keyOf(part.heading);
  const reading = about.reading?.key === key ? about.reading : null;
  const undo =
    about.undo && keyOf(about.undo.heading) === key ? about.undo : null;
  const headingId = `${anchor(index)}-heading`;
  return (
    <section
      id={anchor(index)}
      aria-labelledby={headingId}
      className="relative scroll-mt-4 rounded-lg border p-4"
    >
      {kindAnchor ? (
        <span id={kindAnchor} className="absolute -top-4" aria-hidden="true" />
      ) : null}
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 id={headingId} className="break-words text-base font-semibold">
            {part.heading}
          </h3>
          <p className={MUTED}>{W.kinds[kind]}</p>
        </div>
        {editing ? null : (
          <Button
            variant="outline"
            size="sm"
            aria-label={`Edit ${part.heading}`}
            onClick={onEdit}
          >
            <Pencil aria-hidden="true" />
            Edit
          </Button>
        )}
      </div>
      <div className="mt-3 flex flex-col gap-3">
        {shown?.state === "elsewhere" ? (
          <Alert>
            <AlertTitle>{W.elsewhereTitle}</AlertTitle>
            <AlertDescription>{W.elsewhere}</AlertDescription>
            <Button
              size="sm"
              variant="outline"
              className="mt-2 w-fit"
              disabled={busy}
              onClick={() => {
                onAct(() => rewriteAbout(part.heading, about.version));
              }}
            >
              {W.rewrite}
            </Button>
          </Alert>
        ) : null}
        {editing ? (
          <PartEditor
            part={part}
            kind={kind}
            about={about}
            shown={shown}
            status={status}
            onChange={onChange}
            onDone={onDone}
          />
        ) : part.body.trim() ? (
          <Markdown source={part.body} />
        ) : (
          <p className={MUTED}>{W.empty}</p>
        )}
        {kind === "skills" && about.advanced > 0 ? (
          <p className={MUTED}>
            {W.advanced(about.advanced)}{" "}
            <Link to="/profile?tab=skills" className={LINK}>
              {W.classic}
            </Link>
          </p>
        ) : null}
        {!editing &&
        !reading &&
        (shown?.state === "edited" || shown?.state === "new") ? (
          <NotApplied
            about={about}
            heading={part.heading}
            shown={shown}
            onDone={onDone}
          />
        ) : null}
        {reading ? (
          <ReadingBox
            reading={reading}
            busy={busy}
            onDone={() => onDone(part.heading)}
            onAct={onAct}
          />
        ) : null}
        {undo && !reading ? (
          <div className="flex flex-col gap-2 rounded-md bg-muted/50 p-3">
            <p className="text-sm font-medium">{W.applied}</p>
            <ul className="list-disc pl-5 text-sm">
              {undo.lines.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
            <Button
              size="sm"
              variant="outline"
              className="w-fit"
              disabled={busy}
              onClick={() => {
                onAct(() => actOnReading("undo"));
              }}
            >
              <Undo2 aria-hidden="true" />
              Undo
            </Button>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function costLine(shown: AboutPart | undefined): string | null {
  const cost = shown?.estimate?.cost;
  if (!cost) return null;
  return W.cost(
    money(cost.currency, cost.low),
    money(cost.currency, cost.high),
  );
}

function NoKey() {
  return (
    <p className={MUTED}>
      {W.noKey}{" "}
      <Link to="/account" className={LINK}>
        {W.addKey}
      </Link>
    </p>
  );
}

function NotApplied({
  about,
  heading,
  shown,
  onDone,
}: {
  about: About;
  heading: string;
  shown: AboutPart;
  onDone: (heading: string) => Promise<void>;
}) {
  const [pending, setPending] = useState(false);
  if (about.needs === "model_key")
    return (
      <div className="flex flex-col gap-1">
        <p className="text-sm">{W.notApplied}</p>
        <NoKey />
      </div>
    );
  const cost = costLine(shown);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <p className="text-sm">{W.notApplied}</p>
      <Button
        size="sm"
        disabled={pending || shown.too_long || shown.duplicate}
        aria-busy={pending}
        onClick={() => {
          setPending(true);
          void onDone(heading).finally(() => {
            setPending(false);
          });
        }}
      >
        {pending ? <Spinner aria-hidden="true" /> : null}
        {W.update}
      </Button>
      {cost ? (
        <span className="text-xs text-muted-foreground">{cost}</span>
      ) : null}
    </div>
  );
}

function PartEditor({
  part,
  kind,
  about,
  shown,
  status,
  onChange,
  onDone,
}: {
  part: Part;
  kind: PartKind;
  about: About;
  shown: AboutPart | undefined;
  status: SaveState;
  onChange: (part: Part) => void;
  onDone: (heading: string) => Promise<void>;
}) {
  // The editor holds what is typed; the document gets it with its headings demoted.
  const [heading, setHeading] = useState(part.heading);
  const [body, setBody] = useState(part.body);
  const [pending, setPending] = useState(false);
  const id = useId();
  const named = heading.trim() !== "";
  const send = (nextHeading: string, nextBody: string) => {
    if (nextHeading.trim())
      onChange({ heading: nextHeading.trim(), body: demote(nextBody) });
  };
  const cost = costLine(shown);
  const tooLong = `## ${heading}\n\n${body}`.length > about.part_max;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${id}-heading`}>{W.heading}</Label>
        <Input
          id={`${id}-heading`}
          value={heading}
          maxLength={200}
          aria-invalid={!named || shown?.duplicate}
          onChange={(event) => {
            setHeading(event.target.value);
            send(event.target.value, body);
          }}
        />
        {!named ? (
          <p className="text-sm text-destructive">{W.needsHeading}</p>
        ) : null}
        {named && shown?.duplicate ? (
          <p className="text-sm text-destructive">{W.duplicate}</p>
        ) : null}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-body`}>{W.text}</Label>
          <Textarea
            id={`${id}-body`}
            value={body}
            rows={12}
            placeholder={W.placeholders[kind]}
            className="font-mono text-sm"
            onChange={(event) => {
              setBody(event.target.value);
              send(heading, event.target.value);
            }}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-1.5">
          <p id={`${id}-preview`} className="text-sm font-medium">
            {W.preview}
          </p>
          <div
            aria-labelledby={`${id}-preview`}
            role="region"
            className="min-h-24 rounded-md border bg-muted/30 p-3"
          >
            {body.trim() ? (
              <Markdown source={demote(body)} />
            ) : (
              <p className={MUTED}>{W.empty}</p>
            )}
          </div>
        </div>
      </div>
      {tooLong ? <p className="text-sm text-destructive">{W.tooLong}</p> : null}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          disabled={pending || !named || tooLong}
          aria-busy={pending}
          onClick={() => {
            setPending(true);
            void onDone(heading.trim()).finally(() => {
              setPending(false);
            });
          }}
        >
          {pending ? <Spinner aria-hidden="true" /> : null}
          Done
        </Button>
        <SaveLine status={status} />
        {cost && about.needs === null ? (
          <span className="text-xs text-muted-foreground">{cost}</span>
        ) : null}
      </div>
      {about.needs === "model_key" ? <NoKey /> : null}
    </div>
  );
}

function SaveLine({ status }: { status: SaveState }) {
  const text =
    status.state === "saved"
      ? W.saved
      : status.state === "saving"
        ? W.saving
        : status.state === "waiting"
          ? W.waiting
          : status.state === "failed"
            ? `${W.notSaved} ${status.message}`
            : W.conflictTitle;
  return (
    <span
      aria-live="polite"
      className={
        status.state === "failed"
          ? "text-sm text-destructive"
          : "text-sm text-muted-foreground"
      }
    >
      {text}
    </span>
  );
}

function ReadingBox({
  reading,
  busy,
  onDone,
  onAct,
}: {
  reading: Reading;
  busy: boolean;
  onDone: () => Promise<void>;
  onAct: (call: () => Promise<About>) => void;
}) {
  if (reading.status === "running")
    return (
      <p role="status" className={`flex items-center gap-2 ${MUTED}`}>
        <Spinner aria-hidden="true" />
        {W.reading}
      </p>
    );
  if (reading.status === "failed")
    return (
      <Alert variant="destructive">
        <AlertTitle>{W.notRead}</AlertTitle>
        <AlertDescription>{reading.error ?? W.notRead}</AlertDescription>
        <Button
          size="sm"
          variant="outline"
          className="mt-2 w-fit"
          disabled={busy}
          onClick={() => void onDone()}
        >
          <RotateCw aria-hidden="true" />
          Try again
        </Button>
      </Alert>
    );
  return (
    <div
      role="region"
      aria-label={W.understood}
      className="flex flex-col gap-2 rounded-md border bg-muted/40 p-3"
    >
      <p className="text-sm font-medium">{W.understood}</p>
      <ul className="list-disc pl-5 text-sm">
        {reading.lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {reading.refused.length > 0 ? (
        <>
          <p className={MUTED}>{W.refused}</p>
          <ul className={`list-disc pl-5 ${MUTED}`}>
            {reading.refused.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </>
      ) : null}
      {reading.stale ? <p className="text-sm">{W.stale}</p> : null}
      <div className="flex flex-wrap gap-2">
        {reading.can_apply && !reading.stale ? (
          <Button
            size="sm"
            disabled={busy}
            onClick={() => {
              onAct(() => actOnReading("apply"));
            }}
          >
            Apply
          </Button>
        ) : null}
        {reading.stale ? (
          <Button size="sm" disabled={busy} onClick={() => void onDone()}>
            Done
          </Button>
        ) : null}
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => {
            onAct(() => actOnReading("discard"));
          }}
        >
          {reading.can_apply ? W.notNow : W.close}
        </Button>
      </div>
    </div>
  );
}
