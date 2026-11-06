import { RotateCw } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";

import { type Reading } from "../../api/about-api";
import type { AboutAction } from "@/features/profile/api/use-about-actions";
import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";

/** The model's reading of a part after Done: running, failed, or what it would change. */
export function PartReading({
  reading,
  busy,
  onDone,
  onAct,
}: {
  reading: Reading;
  busy: boolean;
  onDone: () => Promise<void>;
  onAct: (action: AboutAction) => void;
}) {
  if (reading.status === "running")
    return (
      <p
        role="status"
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
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
      className="flex flex-col gap-2 rounded-lg bg-muted p-3"
    >
      <p className="text-sm font-medium">{W.understood}</p>
      <ul className="list-disc pl-5 text-sm">
        {reading.lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {reading.refused.length > 0 ? (
        <>
          <p className="text-sm text-muted-foreground">{W.refused}</p>
          <ul className="list-disc pl-5 text-sm text-muted-foreground">
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
              onAct({ name: "apply" });
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
            onAct({ name: "discard" });
          }}
        >
          {reading.can_apply ? W.notNow : W.close}
        </Button>
      </div>
    </div>
  );
}
