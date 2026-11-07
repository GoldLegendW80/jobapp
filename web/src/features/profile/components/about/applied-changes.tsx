import { Undo2 } from "lucide-react";

import { Button } from "@/components/ui/button";

import type { AboutAction } from "@/features/profile/api/use-about-actions";
import { ABOUT_WORDS as W } from "@/features/profile/helpers/about-words";

/** What the last Apply changed in the profile, with Undo. */
export function AppliedChanges({
  lines,
  busy,
  onAct,
}: {
  lines: readonly string[];
  busy: boolean;
  onAct: (action: AboutAction) => void;
}) {
  return (
    <div className="flex flex-col gap-2 rounded-lg bg-muted p-3">
      <p className="text-sm font-medium">{W.applied}</p>
      <ul className="list-disc pl-5 text-sm">
        {lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      <Button
        size="sm"
        variant="outline"
        className="w-fit"
        disabled={busy}
        onClick={() => {
          onAct({ name: "undo" });
        }}
      >
        <Undo2 aria-hidden="true" />
        Undo
      </Button>
    </div>
  );
}
