import { Link } from "react-router";

import { Button } from "@/components/ui/button";

import type { ApplySettings } from "@/features/apply/api/settings-api";
import { applyDoes } from "@/features/apply/helpers/apply-offer";

/** What Apply does under the person's setting, with the way to change it, and what Apply myself does. */
export function ApplyModeLine({
  settings,
  failed,
  onRetry,
}: {
  settings: ApplySettings | undefined;
  failed: boolean;
  onRetry: () => void;
}) {
  return (
    <div className="flex flex-col gap-1 text-sm text-muted-foreground">
      {settings ? (
        <p data-testid="apply-mode-line">
          {applyDoes(settings)}{" "}
          <Link
            to="/settings/applications"
            className="font-medium text-foreground underline underline-offset-4"
          >
            Change it in Settings
          </Link>
        </p>
      ) : failed ? (
        <p data-testid="apply-mode-pending">
          Your apply settings could not be read, so Apply waits.{" "}
          <Button
            type="button"
            variant="link"
            className="h-auto p-0 font-medium text-foreground underline"
            onClick={onRetry}
          >
            Try again
          </Button>
        </p>
      ) : (
        <p data-testid="apply-mode-pending">Reading your apply settings</p>
      )}
      <p>
        With Apply myself, you fill and send the form yourself in your browser
        here.
      </p>
    </div>
  );
}
