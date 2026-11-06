import { Pencil } from "lucide-react";
import { Link } from "react-router";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

import {
  keyOf,
  rewriteAbout,
  type About,
  type AboutPart,
  type Part,
  type PartKind,
} from "../../api/about-api";
import type { SaveState } from "@/features/profile/api/use-about-autosave";
import { partAnchor } from "@/features/profile/helpers/about-parts";
import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";
import { Markdown } from "../markdown";
import { AppliedChanges } from "./applied-changes";
import { NotApplied } from "./not-applied";
import { PartEditor } from "./part-editor";
import { PartReading } from "./part-reading";
import { ProfileChangedAlert } from "./profile-changed-alert";

/** One part of the document: rendered, or edited in place, with what Done found under it. */
export function AboutPartCard({
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
  const headingId = `${partAnchor(index)}-heading`;
  return (
    <Card
      id={partAnchor(index)}
      role="region"
      aria-labelledby={headingId}
      className="relative scroll-mt-4 gap-3 p-4 sm:p-5"
    >
      {kindAnchor ? (
        <span id={kindAnchor} className="absolute -top-4" aria-hidden="true" />
      ) : null}
      <CardHeader className="flex flex-wrap items-start justify-between gap-2 px-0">
        <div className="flex min-w-0 flex-col gap-1">
          <CardTitle className="text-base break-words">
            <h3 id={headingId}>{part.heading}</h3>
          </CardTitle>
          <CardDescription>{W.kinds[kind]}</CardDescription>
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
      </CardHeader>
      <CardContent className="flex flex-col gap-3 px-0">
        {shown?.state === "elsewhere" ? (
          <ProfileChangedAlert
            busy={busy}
            onRewrite={() => {
              onAct(() => rewriteAbout(part.heading, about.version));
            }}
          />
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
          <p className="text-sm text-muted-foreground">{W.empty}</p>
        )}
        {kind === "skills" && about.advanced > 0 ? (
          <p className="text-sm text-muted-foreground">
            {W.advanced(about.advanced)}{" "}
            <Link
              to="/profile?tab=skills"
              className="font-medium text-foreground underline underline-offset-4"
            >
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
          <PartReading
            reading={reading}
            busy={busy}
            onDone={() => onDone(part.heading)}
            onAct={onAct}
          />
        ) : null}
        {undo && !reading ? (
          <AppliedChanges lines={undo.lines} busy={busy} onAct={onAct} />
        ) : null}
      </CardContent>
    </Card>
  );
}
