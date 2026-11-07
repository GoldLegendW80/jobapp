import { NativeSelect } from "@/components/ui/native-select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

import type { AdminUser } from "../../api/admin-api";
import { displayName } from "@/features/admin/helpers/user-actions";

/** Toggles up to this many choices, a select beyond. */
const TOGGLES_MAX = 3;

/**
 * An account's browsers at once (owner, 2026-11-07): 1 up to the server's BROWSER_SLOTS (`max`),
 * saved as soon as the admin picks it. With one browser on the server there is nothing to pick.
 */
export function BrowsersControl({
  user,
  max,
  saving,
  onChange,
}: {
  user: AdminUser;
  max: number;
  saving: boolean;
  onChange: (browsers: number) => void;
}) {
  const label = `Browsers at once for ${displayName(user)}`;
  const choices = Array.from({ length: max }, (_, index) => String(index + 1));
  if (max <= 1) return <span className="tabular-nums">1</span>;
  if (max > TOGGLES_MAX)
    return (
      <NativeSelect
        aria-label={label}
        className="h-8 w-20"
        disabled={saving}
        value={String(user.browsers)}
        onChange={(event) => {
          onChange(Number(event.target.value));
        }}
      >
        {choices.map((choice) => (
          <option key={choice} value={choice}>
            {choice}
          </option>
        ))}
      </NativeSelect>
    );
  return (
    <ToggleGroup
      type="single"
      variant="outline"
      size="sm"
      aria-label={label}
      disabled={saving}
      value={String(user.browsers)}
      onValueChange={(next) => {
        // A click on the toggle that is on would leave none on: it stays.
        if (next && next !== String(user.browsers)) onChange(Number(next));
      }}
    >
      {choices.map((choice) => (
        <ToggleGroupItem key={choice} value={choice} className="tabular-nums">
          {choice}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}
