import { Search as SearchIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Field, FieldLabel } from "@/components/ui/field";
import { Form } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

import type { SearchSetupState } from "@/features/jobs/hooks/use-search-setup";
import { NAME_MAX } from "@/features/jobs/helpers/saved-searches";
import { SourcesEditor } from "@/components/shared/jobs/sources/sources-editor";
import { BLOCK, COLUMN } from "@/components/shared/jobs/setup-grid";
import { BODY, EDGES } from "./setup-surface";
import { WhatYouWant } from "./what-you-want";

/** The two steps of one search (where to search, what you want), its name when it is a saved
 * one, and the footer that stays in view: what went wrong, Back (or Cancel), Save, and "Save and
 * start search". */
export function SetupForm({
  setup,
  onClose,
  onReload,
  onBack,
}: {
  setup: SearchSetupState;
  onClose: () => void;
  onReload: () => Promise<void>;
  /** Back to Your searches, keeping the draft. */
  onBack?: () => void;
}) {
  const { locked, conflict, error, emptyReason } = setup;
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
            {setup.target === "main" ? null : (
              <Field className={cn(COLUMN, BLOCK)}>
                <FieldLabel htmlFor="search-name">Name</FieldLabel>
                <Input
                  id="search-name"
                  value={setup.name}
                  maxLength={NAME_MAX}
                  autoComplete="off"
                  placeholder="For example: Remote roles in Lyon"
                  onChange={(event) => {
                    setup.setName(event.target.value);
                  }}
                />
              </Field>
            )}
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
          {emptyReason ? (
            <p id="setup-empty" className="text-sm text-muted-foreground">
              {emptyReason}
            </p>
          ) : null}
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={onBack ?? onClose}
              disabled={locked}
            >
              {onBack ? "Back" : "Cancel"}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                setup.onSave();
              }}
              disabled={conflict || locked}
            >
              Save
            </Button>
            <Button
              type="submit"
              className="flex-1 sm:flex-none"
              disabled={conflict || locked}
              aria-describedby={emptyReason ? "setup-empty" : undefined}
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
