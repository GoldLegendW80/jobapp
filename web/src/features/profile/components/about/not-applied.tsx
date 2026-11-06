import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

import type { About, AboutPart } from "../../api/about-api";
import { doneCost } from "@/features/profile/helpers/about-parts";
import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";
import { NoModelKey } from "./no-model-key";

/** A part changed or new since the profile last read it, with the button that reads it. */
export function NotApplied({
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
  const facts = shown.kind === "facts";
  if (about.needs === "model_key" && !facts)
    return (
      <div className="flex flex-col gap-1">
        <p className="text-sm">{W.notApplied}</p>
        <NoModelKey />
      </div>
    );
  const cost = doneCost(shown);
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
        {facts ? W.saveFacts : W.update}
      </Button>
      {cost ? (
        <span className="text-xs text-muted-foreground">{cost}</span>
      ) : null}
    </div>
  );
}
