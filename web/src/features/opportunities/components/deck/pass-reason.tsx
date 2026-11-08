import { useId, useState } from "react";

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { describeError } from "@/lib/api";

import { useSetPassReason } from "../../api/pass-reason-api";
import {
  PASS_REASONS,
  type PassReason as Reason,
} from "../../helpers/deck/pass-reasons";

const isReason = (value: string): value is Reason => value in PASS_REASONS;

/**
 * Under the deck after a Pass: its reason in one tap, optional. The next card is already there
 * and the swipe waits for nothing; the next swipe or the undo takes this away. A second tap
 * replaces the reason. No roving focus, so the arrow keys keep swiping. One row on a phone,
 * scrolled sideways, so it stays above the tab bar.
 */
export function PassReason({ postingId }: { postingId: string }) {
  const labelId = useId();
  const save = useSetPassReason(postingId);
  const [chosen, setChosen] = useState("");
  return (
    <div
      data-testid="pass-reason"
      className="flex w-full min-w-0 flex-col items-center gap-1"
    >
      <div className="flex w-full min-w-0 items-center gap-2 overflow-x-auto py-1 sm:flex-col sm:overflow-visible">
        <p
          id={labelId}
          className="shrink-0 text-xs whitespace-nowrap text-muted-foreground"
        >
          Why the Pass? Optional.
        </p>
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          spacing={1}
          rovingFocus={false}
          aria-labelledby={labelId}
          value={chosen}
          onValueChange={(value) => {
            if (!isReason(value)) return;
            const before = chosen;
            setChosen(value);
            save.mutate(value, {
              onError: () => {
                setChosen(before);
              },
            });
          }}
          className="flex-nowrap sm:flex-wrap sm:justify-center"
        >
          {Object.entries(PASS_REASONS).map(([value, label]) => (
            <ToggleGroupItem key={value} value={value} className="text-xs">
              {label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>
      {save.isError ? (
        <p role="alert" className="text-xs text-destructive">
          The reason was not saved. {describeError(save.error)}
        </p>
      ) : null}
    </div>
  );
}
