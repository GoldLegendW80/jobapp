import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * Answers offered for an empty field, each filled in by one click: only words the person picks,
 * never a value the app chose for them.
 */
export function Picks({
  label,
  options,
  onPick,
}: {
  label: string;
  options: readonly { value: string; words?: string }[];
  onPick: (value: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map(({ value, words }) => (
        <Button
          key={value}
          type="button"
          variant="outline"
          size="sm"
          className="h-7 max-w-full rounded-full px-2.5 font-normal"
          aria-label={`${label}: ${value}${words ? `, ${words}` : ""}`}
          onClick={() => {
            onPick(value);
          }}
        >
          <Plus aria-hidden="true" />
          <span className="truncate">
            {value}
            {words ? (
              <span className="text-muted-foreground">, {words}</span>
            ) : null}
          </span>
        </Button>
      ))}
    </div>
  );
}
