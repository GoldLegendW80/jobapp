import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";

import {
  demote,
  type About,
  type AboutPart,
  type Part,
  type PartKind,
} from "../../api/about-api";
import type { SaveState } from "@/features/profile/api/use-about-autosave";
import { doneCost } from "@/features/profile/helpers/about-parts";
import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";
import { Markdown } from "../markdown";
import { NoModelKey } from "./no-model-key";
import { SaveLine } from "./save-line";

/** A part edited in place: its heading, its text beside a live preview, and Done. */
export function PartEditor({
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
  const cost = doneCost(shown);
  const tooLong = `## ${heading}\n\n${body}`.length > about.part_max;
  return (
    <div className="flex flex-col gap-3">
      <Field className="gap-1.5">
        <FieldLabel htmlFor={`${id}-heading`}>{W.heading}</FieldLabel>
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
        {!named ? <FieldError>{W.needsHeading}</FieldError> : null}
        {named && shown?.duplicate ? (
          <FieldError>{W.duplicate}</FieldError>
        ) : null}
      </Field>
      <div className="grid gap-3 md:grid-cols-2">
        <Field className="gap-1.5">
          <FieldLabel htmlFor={`${id}-body`}>{W.text}</FieldLabel>
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
        </Field>
        <div className="flex min-w-0 flex-col gap-1.5">
          <p id={`${id}-preview`} className="text-sm font-medium">
            {W.preview}
          </p>
          <div
            aria-labelledby={`${id}-preview`}
            role="region"
            className="min-h-24 rounded-lg bg-muted p-3"
          >
            {body.trim() ? (
              <Markdown source={demote(body)} />
            ) : (
              <p className="text-sm text-muted-foreground">{W.empty}</p>
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
      {about.needs === "model_key" && kind !== "facts" ? <NoModelKey /> : null}
    </div>
  );
}
