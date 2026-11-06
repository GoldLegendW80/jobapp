import { Search as SearchIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Form } from "@/components/ui/form";
import { Spinner } from "@/components/ui/spinner";
import type { Profile } from "@/features/profile/api/profile-api";
import { cn } from "@/lib/utils";

import { useSearchSetup } from "../../hooks/use-search-setup";
import type { SweepRunner } from "@/features/jobs/api/use-sweep-runner";
import { SourcesEditor } from "@/components/shared/jobs/sources/sources-editor";
import { BODY, EDGES } from "./setup-surface";
import { WhatYouWant } from "./what-you-want";

/** The two steps of Find jobs (where to search, what you want) and the footer that stays in
 * view: what went wrong, Cancel, and "Save and start search". */
export function SetupForm({
  profile,
  runner,
  onClose,
  onReload,
  onBusyChange,
}: {
  profile: Profile;
  runner: SweepRunner;
  onClose: () => void;
  onReload: () => Promise<void>;
  onBusyChange: (busy: boolean) => void;
}) {
  const setup = useSearchSetup({ profile, runner, onClose, onBusyChange });
  const { locked, conflict, error } = setup;
  return (
    <Form {...setup.form}>
      <form
        noValidate
        onSubmit={setup.onSubmit}
        className="flex min-h-0 flex-1 flex-col"
      >
        <div className={BODY}>
          <fieldset
            disabled={locked}
            aria-busy={locked}
            className="flex min-w-0 flex-col gap-6"
          >
            <SourcesEditor
              sources={setup.sources}
              reviewed={setup.reviewed}
              disabled={locked}
              step={1}
            />
            <WhatYouWant
              form={setup.form}
              more={setup.more}
              onMoreChange={setup.setMore}
              instructions={setup.instructions}
              onInstructionsChange={setup.setInstructions}
            />
          </fieldset>
        </div>
        {/* Always in view, under the scrolling middle: so is what went wrong after a click. */}
        <div
          className={cn(
            EDGES,
            "flex flex-col gap-3 border-t pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:py-4",
          )}
        >
          {error ? (
            <div
              role="alert"
              className="flex max-h-40 flex-col items-start gap-2 overflow-y-auto rounded-md border border-destructive p-3 text-sm"
            >
              <p>{error}</p>
              {conflict ? (
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void onReload()}
                >
                  Load current settings
                </Button>
              ) : null}
            </div>
          ) : null}
          <div className="flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={locked}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              className="flex-1 sm:flex-none"
              disabled={conflict || locked}
            >
              {locked ? (
                <Spinner aria-hidden="true" role={undefined} />
              ) : (
                <SearchIcon aria-hidden="true" />
              )}
              {locked ? "Saving and starting…" : "Save and start search"}
            </Button>
          </div>
        </div>
      </form>
    </Form>
  );
}
