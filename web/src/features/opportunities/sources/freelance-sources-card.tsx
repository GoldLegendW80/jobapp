/**
 * "Freelance and project sources" of the profile's Search tab (opportunities O8): the public
 * feeds the sweep reads after the company boards (api/app/opportunities/sources, O7), each with
 * its switch and the attribution it asks, and inside Advanced the optional words a title must
 * also match. Below them the platforms read only with the person's opt in (task O7b), each with
 * its one line reason and the date it was checked, from GET /api/opportunities/feeds, with no
 * switch here. "Add a mission by hand" opens the jobs module's Add a posting dialog with the
 * contract Freelance preselected. The switches and words are fields of the Search form: they are
 * saved by its own Save, with its base revision and its conflict dialog.
 */
import { useQuery } from "@tanstack/react-query";
import { CircleAlert, ExternalLink, LoaderCircle, Plus } from "lucide-react";
import { useState } from "react";
import { get, useWatch, type UseFormReturn } from "react-hook-form";
import { Link } from "react-router";

import { AdvancedSection } from "@/components/advanced-section";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { FormField } from "@/components/ui/form";
import { formatDate } from "@/features/jobs/format";
import { AddPostingDialog } from "@/features/jobs/jobs-actions";
import type { SearchForm } from "@/features/profile/editor-forms";
import { Group } from "@/features/profile/section-ui";
import { TermChips } from "@/features/profile/term-chips";
import { WORDING } from "@/lib/wording";

import {
  feedCatalogueKey,
  getFeedCatalogue,
  type RestrictedPlatform,
} from "./feeds-api";
import { FEED_NAMES, isFeedKind, type FeedKind } from "./source-words";

/** The id the Advanced section focuses when a feed's words have an error. */
const TERMS_ID = "freelance-sources-terms";

interface Row {
  kind: FeedKind;
  name: string;
  description?: string;
  attribution?: string;
}

export function FreelanceSourcesCard({
  form,
}: {
  form: UseFormReturn<SearchForm>;
}) {
  const catalogue = useQuery({
    queryKey: feedCatalogueKey,
    queryFn: ({ signal }) => getFeedCatalogue(signal),
    staleTime: 5 * 60_000,
    retry: false,
  });
  const feeds = useWatch({ control: form.control, name: "feeds" });
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState(false);

  // With the catalogue, its sources in its order; without it, the ones already saved, by name.
  const rows: Row[] = catalogue.data
    ? catalogue.data.supported.flatMap((source) =>
        isFeedKind(source.kind) ? [{ ...source, kind: source.kind }] : [],
      )
    : catalogue.isError
      ? feeds
          .filter((feed) => feed.stored)
          .map((feed) => ({ kind: feed.kind, name: FEED_NAMES[feed.kind] }))
      : [];
  const indexOf = (kind: FeedKind) =>
    feeds.findIndex((feed) => feed.kind === kind);
  const anyOn = feeds.some((feed) => feed.enabled);
  const termsError = get(form.formState.errors, "feeds") !== undefined;

  return (
    <Group
      title={WORDING.freelanceSources}
      description="Public feeds of remote jobs and freelance missions, read after the company boards at every search. Their postings go through your titles, locations and rules like any other."
      action={
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => {
            setAdded(false);
            setAdding(true);
          }}
        >
          <Plus aria-hidden="true" />
          {WORDING.addMissionByHand}
        </Button>
      }
    >
      {added ? (
        <p role="status" className="text-sm">
          Mission added.{" "}
          <Link
            to="/discover"
            className="text-primary underline-offset-4 hover:underline"
          >
            {WORDING.findInDiscover}
          </Link>
        </p>
      ) : null}
      {anyOn ? null : (
        <p className="text-sm text-muted-foreground">
          {WORDING.noFreelanceSource}
        </p>
      )}
      {catalogue.isPending ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Loading the sources
        </p>
      ) : null}
      {catalogue.isError ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription>
            <p>
              The list of sources could not be loaded.{" "}
              {rows.length > 0
                ? "The sources you saved still show."
                : "Try again in a moment."}
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="mt-2 w-fit"
              onClick={() => void catalogue.refetch()}
            >
              {WORDING.tryAgain}
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}
      {rows.length > 0 ? (
        <ul
          aria-label={WORDING.freelanceSourceList}
          className="flex flex-col gap-3"
        >
          {rows.map((row) => (
            <FeedRow
              key={row.kind}
              form={form}
              row={row}
              index={indexOf(row.kind)}
            />
          ))}
        </ul>
      ) : null}
      {rows.length > 0 ? (
        <AdvancedSection
          pageKey="profile-search-feeds"
          errorFieldId={termsError ? TERMS_ID : undefined}
        >
          <div id={TERMS_ID} tabIndex={-1} className="flex flex-col gap-4">
            <p className="text-sm text-muted-foreground">
              Words a title must also match, per source, on top of your job
              titles. Leave empty to keep every posting your titles match.
            </p>
            {rows.map((row) => (
              <TermChips
                key={row.kind}
                form={form}
                name={`feeds.${indexOf(row.kind)}.terms`}
                label={`${WORDING.wordsFor} ${row.name}`}
                compact
              />
            ))}
          </div>
        </AdvancedSection>
      ) : null}
      {catalogue.data && catalogue.data.not_supported.length > 0 ? (
        <OptInPlatforms platforms={catalogue.data.not_supported} />
      ) : null}
      {adding ? (
        // A React event bubbles out of a portal to its parent in the tree: the dialog's own
        // submit must never reach the Search form around this card.
        <div
          onSubmit={(event) => {
            event.stopPropagation();
          }}
        >
          <AddPostingDialog
            initial={{ contract_type: "Freelance" }}
            onClose={() => {
              setAdding(false);
            }}
            onAdded={() => {
              setAdded(true);
            }}
          />
        </div>
      ) : null}
    </Group>
  );
}

function FeedRow({
  form,
  row,
  index,
}: {
  form: UseFormReturn<SearchForm>;
  row: Row;
  index: number;
}) {
  if (index < 0) return null;
  return (
    <li className="flex flex-col gap-1 rounded-md border p-3">
      <FormField
        control={form.control}
        name={`feeds.${index}.enabled`}
        render={({ field }) => (
          <label className="flex min-h-11 items-center gap-3 text-sm sm:min-h-0">
            <input
              type="checkbox"
              role="switch"
              aria-checked={field.value}
              aria-label={`${WORDING.read} ${row.name}`}
              className="size-4 shrink-0 accent-primary"
              name={field.name}
              ref={field.ref}
              checked={field.value}
              onBlur={field.onBlur}
              onChange={(event) => {
                field.onChange(event.target.checked);
              }}
            />
            <span className="font-medium break-words">{row.name}</span>
            <span aria-hidden="true" className="ml-auto text-muted-foreground">
              {field.value ? WORDING.on : WORDING.off}
            </span>
          </label>
        )}
      />
      {row.description ? (
        <p className="text-sm break-words">{row.description}</p>
      ) : null}
      {row.attribution ? (
        <p className="text-xs text-muted-foreground break-words">
          {row.attribution}
        </p>
      ) : null}
    </li>
  );
}

function OptInPlatforms({ platforms }: { platforms: RestrictedPlatform[] }) {
  return (
    <section
      aria-label={WORDING.readOnlyWithOptIn}
      className="flex flex-col gap-2"
    >
      <h4 className="text-sm font-semibold">{WORDING.readOnlyWithOptIn}</h4>
      <p className="text-sm text-muted-foreground">
        Their terms forbid an app to read them by itself. Reading them will need
        your opt in, a choice that comes in a later version of the app.
      </p>
      <ul className="flex flex-col gap-2">
        {platforms.map((platform) => (
          <li
            key={platform.kind}
            className="flex flex-col gap-0.5 rounded-md border border-dashed p-3 text-sm"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <span className="font-medium">{platform.name}</span>
              <span className="text-xs text-muted-foreground">
                Needs your opt in, coming later
              </span>
            </div>
            <p className="break-words">{platform.reason}</p>
            <p className="text-xs text-muted-foreground">
              {`Checked on ${formatDate(platform.checked_on)}. `}
              <a
                href={platform.terms_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 underline underline-offset-4"
              >
                {`${WORDING.termsOf} ${platform.name}`}
                <ExternalLink aria-hidden="true" className="size-3" />
                <span className="sr-only">{WORDING.opensNewTab}</span>
              </a>
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}
