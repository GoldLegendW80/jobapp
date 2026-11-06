/**
 * "Where to search": one box for any address, a few examples, and the list (sources.ts). Shared
 * by Find jobs (search-setup/search-setup.tsx) and the wizard's "Websites to search" step.
 * Controlled by `useSources` (use-sources.ts), which saves; `useSourcesEditor` makes the
 * changes. Laid out on the grid of setup-grid.tsx: icons in the gutter, every text at one edge.
 */
import { useId } from "react";

import type { Search } from "@/features/profile/api/profile-api";
import { cn } from "@/lib/utils";

import type { Sources } from "@/features/jobs/api/use-sources";
import { useSourcesEditor } from "@/features/jobs/hooks/use-sources-editor";
import { examplesToOffer } from "@/features/jobs/helpers/source-ideas";
import { missingRecommended } from "@/features/jobs/helpers/sources";
import { BLOCKS, COLUMN, Glyph, HANG, StepNumber } from "../setup-grid";
import { AddressField } from "./address-field";
import { AddressKinds } from "./address-kinds";
import { BoardIdeas } from "./board-ideas";
import { ExampleSources } from "./example-sources";
import { SourceList } from "./source-list";

export function SourcesEditor({
  sources,
  reviewed,
  disabled = false,
  heading = "h3",
  step,
  showTitle = true,
}: {
  sources: Sources;
  /** The search as last saved: what saving changes is said against it. */
  reviewed: Search;
  disabled?: boolean;
  heading?: "h2" | "h3";
  /** The number shown before the title, when the list is one part of a longer form. */
  step?: number;
  /** false when the page around already names the list (the wizard's step). */
  showTitle?: boolean;
}) {
  const id = useId();
  const editor = useSourcesEditor(sources);
  const locked = disabled || editor.adding || sources.saving;
  const examples = examplesToOffer(sources.draft);
  const Heading = heading;

  return (
    <section
      className={cn(COLUMN, BLOCKS)}
      aria-labelledby={showTitle ? `${id}-title` : undefined}
      aria-label={showTitle ? undefined : "Where to search"}
    >
      {showTitle ? (
        <Heading
          id={`${id}-title`}
          className={cn(
            HANG,
            "items-center text-base font-semibold tracking-tight",
          )}
        >
          {step ? <StepNumber step={step} /> : <Glyph />}
          Where to search
        </Heading>
      ) : null}

      <AddressKinds />

      <AddressField
        id={id}
        value={sources.pending}
        problem={editor.problem}
        adding={editor.adding}
        disabled={locked}
        onChange={editor.editAddress}
        onAdd={() => void editor.addTyped()}
      />

      {examples.length ? (
        <ExampleSources
          examples={examples}
          offerRecommended={missingRecommended(sources.draft).length > 0}
          disabled={locked}
          onAdd={(url, label, platform) =>
            void editor.add(url, label, platform)
          }
          onAddRecommended={editor.addRecommended}
        />
      ) : null}

      <SourceList
        id={id}
        sources={sources}
        reviewed={reviewed}
        disabled={locked}
        said={editor.said}
        onRemove={editor.remove}
        onResume={editor.resume}
      />

      <BoardIdeas
        boards={sources.draft.boards}
        disabled={locked}
        onAdd={editor.addBoards}
      />
    </section>
  );
}
