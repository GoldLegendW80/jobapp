import { FilePlus2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { describeError } from "@/lib/api";

import {
  joinParts,
  splitParts,
  type About,
  type Part,
} from "../../api/about-api";
import type { Profile } from "../../api/profile-api";
import { useAboutActions } from "@/features/profile/api/use-about-actions";
import { useAboutAutosave } from "@/features/profile/api/use-about-autosave";
import { useSaveFactOrder } from "@/features/profile/api/use-fact-order";
import {
  kindAnchors,
  moveId,
  newPartHeading,
  partAnchor,
  shownPart,
} from "@/features/profile/helpers/about-parts";
import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";
import {
  blockTitle,
  factBaseOf,
  factBytes,
  isFactPart,
} from "@/features/profile/helpers/fact-base";
import { AboutContents, type ContentsEntry } from "./about-contents";
import { AboutPartCard } from "./about-part-card";
import { FactSizeMeter } from "./fact-size-meter";
import { PlainAnswers } from "./plain-answers";
import { PlainSkills } from "./plain-skills";
import { SaveConflictAlert } from "./save-conflict-alert";

/** The forms of About you without a model key: their dirty flags and their remount keys. */
export interface PlainForms {
  onDirty: Record<"skills" | "answers", (dirty: boolean) => void>;
  keys: Record<"skills" | "answers", number>;
}

/** What About you lists, top to bottom: a part of the text, or the fields of a kind. */
type Item =
  { kind: "part"; index: number } | { kind: "skills" } | { kind: "answers" };

/**
 * The items of the page: the parts in order; without a model key the Skills and Answers fields
 * stand where the first part of their kind is, or first and last when the text has none.
 */
function itemsOf(about: About, parts: readonly Part[]): Item[] {
  if (about.needs !== "model_key")
    return parts.map((_, index) => ({ kind: "part", index }));
  const items: Item[] = [];
  const placed = new Set<string>();
  parts.forEach((part, index) => {
    const kind = shownPart(about, part.heading)?.kind ?? "facts";
    if (kind === "facts") items.push({ kind: "part", index });
    else if (!placed.has(kind)) {
      placed.add(kind);
      items.push(kind === "skills" ? { kind: "skills" } : { kind: "answers" });
    }
  });
  if (!placed.has("skills")) items.unshift({ kind: "skills" });
  if (!placed.has("answers")) items.push({ kind: "answers" });
  return items;
}

const FIELDS_ENTRY: Record<"skills" | "answers", ContentsEntry> = {
  skills: { label: "Skills", anchor: "about-skills" },
  answers: { label: "Answers", anchor: "about-answers" },
};

/**
 * The loaded document: the contents and the size of the fact base beside its parts, one part
 * edited at a time. Fact parts move up and down, and the fact base keeps their order. Without a
 * model key the skills and the answers are fields (owner, 2026-11-07).
 */
export function AboutEditor({
  about,
  profile,
  forms,
}: {
  about: About;
  profile: Profile;
  forms: PlainForms;
}) {
  const doc = useAboutAutosave(about);
  const actions = useAboutActions(doc.flush);
  const order = useSaveFactOrder({
    onError: (error) => {
      toast.error(`${W.orderNotSaved} ${describeError(error)}`);
    },
  });
  // The fields are memoized against the renders typing causes: their save reads the latest here.
  const latest = useRef({ about, syncPart: actions.syncPart });
  useEffect(() => {
    latest.current = { about, syncPart: actions.syncPart };
  });
  const onSkillsSaved = useCallback(() => {
    void latest.current.syncPart("skills", latest.current.about);
  }, []);
  const onAnswersSaved = useCallback(() => {
    void latest.current.syncPart("answers", latest.current.about);
  }, []);
  const [editing, setEditing] = useState<number | null>(null);
  const [moved, setMoved] = useState("");
  // The fact part just moved: its button keeps the focus at its new place.
  const focusAfterMove = useRef<{ index: number; up: boolean } | null>(null);
  const { preamble, parts } = splitParts(doc.text);
  const anchors = kindAnchors(about, parts);
  const items = itemsOf(about, parts);
  const facts = parts.flatMap((part, index) =>
    isFactPart(about, part) ? [index] : [],
  );
  const bytes = factBytes(
    factBaseOf(parts, about, profile.sections.facts.blocks),
  );

  useEffect(() => {
    const pending = focusAfterMove.current;
    if (!pending) return;
    focusAfterMove.current = null;
    const id = moveId(pending.index);
    const same = document.getElementById(`${id}-${pending.up ? "up" : "down"}`);
    const other = document.getElementById(
      `${id}-${pending.up ? "down" : "up"}`,
    );
    const usable = (button: HTMLElement | null) =>
      button instanceof HTMLButtonElement && !button.disabled ? button : null;
    (usable(same) ?? usable(other))?.focus();
  });

  const setPart = (index: number, part: Part) => {
    const next = parts.map((one, i) => (i === index ? part : one));
    doc.change(joinParts(preamble, next));
  };

  const addPart = () => {
    const heading = newPartHeading(parts);
    doc.change(joinParts(preamble, [...parts, { heading, body: "" }]));
    setEditing(parts.length);
  };

  const movePart = (index: number, up: boolean) => {
    const target = facts[facts.indexOf(index) + (up ? -1 : 1)];
    const part = parts[index];
    const other = target === undefined ? undefined : parts[target];
    if (target === undefined || !part || !other) return;
    const next = parts.map((one, i) =>
      i === index ? other : i === target ? part : one,
    );
    doc.change(joinParts(preamble, next));
    order.mutate(
      next
        .filter((one) => isFactPart(about, one))
        .map((one) => blockTitle(one.heading)),
    );
    focusAfterMove.current = { index: target, up };
    setMoved(W.moved(part.heading, up ? "up" : "down"));
  };

  const entries = items.map((item) =>
    item.kind === "part"
      ? {
          label: parts[item.index]?.heading ?? "",
          anchor: partAnchor(item.index),
        }
      : FIELDS_ENTRY[item.kind],
  );

  return (
    <div className="flex flex-col gap-4">
      {doc.status.state === "conflict" ? (
        <SaveConflictAlert onSettle={doc.settle} />
      ) : null}
      <div className="flex flex-col gap-4 lg:grid lg:grid-cols-[11rem_minmax(0,1fr)] lg:gap-6">
        <AboutContents
          entries={entries}
          footer={
            <div className="lg:px-2">
              <FactSizeMeter bytes={bytes} />
            </div>
          }
        />
        <div className="flex min-w-0 flex-col gap-4">
          {items.map((item) => {
            if (item.kind === "skills")
              return (
                <PlainSkills
                  key={`skills-${forms.keys.skills}`}
                  profile={profile}
                  advanced={about.advanced}
                  onDirtyChange={forms.onDirty.skills}
                  onSaved={onSkillsSaved}
                />
              );
            if (item.kind === "answers")
              return (
                <PlainAnswers
                  key={`answers-${forms.keys.answers}`}
                  profile={profile}
                  onDirtyChange={forms.onDirty.answers}
                  onSaved={onAnswersSaved}
                />
              );
            const { index } = item;
            const part = parts[index];
            if (!part) return null;
            const position = facts.indexOf(index);
            return (
              <AboutPartCard
                kindAnchor={anchors[index]}
                key={index}
                index={index}
                move={
                  position < 0
                    ? undefined
                    : {
                        position,
                        count: facts.length,
                        disabled:
                          editing !== null ||
                          actions.busy ||
                          doc.status.state === "conflict",
                        onMove: (direction) => {
                          movePart(index, direction === "up");
                        },
                      }
                }
                about={about}
                part={part}
                shown={shownPart(about, part.heading)}
                editing={editing === index}
                busy={actions.busy}
                status={doc.status}
                onEdit={() => {
                  setEditing(index);
                }}
                onChange={(next) => {
                  setPart(index, next);
                }}
                onDone={async (heading) => {
                  if (await actions.finishPart(heading)) setEditing(null);
                }}
                onAct={(action) => void actions.runAction(action)}
              />
            );
          })}
          <p className="sr-only" aria-live="polite">
            {moved}
          </p>
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
              <p className="text-sm text-muted-foreground">
                {W.missing(about.missing_blocks.length)}{" "}
                <Button
                  variant="link"
                  className="h-auto p-0"
                  disabled={actions.busy}
                  onClick={() => {
                    void actions.runAction({
                      name: "rewrite",
                      heading: null,
                      version: about.version,
                    });
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
