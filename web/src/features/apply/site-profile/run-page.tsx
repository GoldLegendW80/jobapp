import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { Button } from "@/components/ui/button";
import { HandoffBanner } from "@/features/browser/handoff-banner";
import { useHandoff } from "@/features/browser/use-handoff";
import { LiveBrowser } from "@/features/browser/live-browser";
import { LaterHandoffBanner } from "@/features/browser/later-handoff-banner";
import { browserQueryKey, getBrowser } from "@/features/browser/browser-api";
import { describeError, errorDetail, isApiError } from "@/lib/api";
import {
  action,
  autosaveSentence,
  getRun,
  runKey,
  type ProfileRun,
} from "./api";
import { SectionReview } from "./section-review";

export function SiteProfileRunPage() {
  const { id = "" } = useParams();
  return <SiteProfileRunContent key={id} id={id} />;
}

function SiteProfileRunContent({ id }: { id: string }) {
  const client = useQueryClient();
  const { handoff: currentHandoff } = useHandoff();
  const query = useQuery({
    queryKey: runKey(id),
    queryFn: ({ signal }) => getRun(id, signal),
    refetchInterval: (current) =>
      ["queued", "filling", "saving", "ready", "needs_person"].includes(
        current.state.data?.state ?? "",
      )
        ? 2000
        : false,
  });
  const browser = useQuery({
    queryKey: browserQueryKey,
    queryFn: ({ signal }) => getBrowser(signal),
    enabled: ["queued", "filling", "saving", "needs_person"].includes(
      query.data?.state ?? "",
    ),
    refetchInterval: 2000,
  });
  const browserReady = browser.isFetchedAfterMount && !browser.isError;
  const handoff = browserReady ? currentHandoff : null;
  const update = async (run: ProfileRun) => {
    await client.cancelQueries({ queryKey: runKey(id) });
    client.setQueryData(runKey(id), run);
  };
  const refresh = () => {
    void client.invalidateQueries({ queryKey: runKey(id) });
  };
  const operation = useMutation({
    mutationFn: (name: string) => action(id, name),
    onSuccess: update,
  });
  if (query.isPending) return <p role="status">Loading profile run.</p>;
  if (query.error)
    return (
      <div role="alert">
        {isApiError(query.error) && query.error.status === 404
          ? "No such profile run."
          : describeError(query.error)}{" "}
        <Link to="/account">Back to Account</Link>
      </div>
    );
  const run = query.data;
  const lease =
    browserReady && browser.data?.holder === "you" ? browser.data.lease : null;
  const liveLease =
    lease?.automation &&
    lease.purpose === "profile_edit" &&
    ["active", "waiting_person"].includes(lease.state)
      ? lease
      : null;
  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 p-4 sm:p-6">
      <header className="space-y-2">
        <Link className="text-sm underline" to="/account">
          Back to Account
        </Link>
        <h1 className="text-2xl font-semibold">
          Complete your profile on {run.host}
        </h1>
        <p className="break-all text-sm text-muted-foreground">{run.url}</p>
        <p role="status" className="capitalize">
          {run.state.replaceAll("_", " ")}
        </p>
      </header>
      {run.error_code && (
        <p role="alert">
          This profile run needs your review:{" "}
          {run.error_code.replaceAll("_", " ")}.
        </p>
      )}
      {handoff ? (
        <HandoffBanner liveOpen />
      ) : (
        run.needs === "signin" &&
        !browser.isFetching &&
        !browser.data?.lease?.automation && (
          <LaterHandoffBanner
            module="site_profile"
            refId={run.id}
            address={run.url}
          />
        )
      )}
      {run.needs === "signin" &&
        !handoff &&
        browser.data?.lease?.automation && (
          <p role="status">Opening your sign in view.</p>
        )}
      {run.needs === "autosave_confirmation" && (
        <div className="space-y-3 rounded-lg border p-4">
          <p>{autosaveSentence}</p>
          <Button
            disabled={operation.isPending}
            onClick={() => {
              operation.mutate("confirm-autosave");
            }}
          >
            I understand, fill empty fields
          </Button>
        </div>
      )}
      {run.autosave && (
        <p className="rounded-lg bg-muted p-4 text-sm">{autosaveSentence}</p>
      )}
      {!handoff && liveLease && ["filling", "saving"].includes(run.state) && (
        <LiveBrowser key={liveLease.id} person={false} />
      )}
      {run.sections.map((section) => (
        <SectionReview
          key={section.section}
          run={run}
          section={section}
          update={update}
          refresh={refresh}
        />
      ))}
      {operation.error && (
        <p role="alert" className="text-sm text-destructive">
          {errorDetail(operation.error) ?? describeError(operation.error)}
        </p>
      )}
      {!["cancelled", "done", "failed"].includes(run.state) && (
        <Button
          variant="outline"
          disabled={operation.isPending}
          onClick={() => {
            operation.mutate("cancel");
          }}
        >
          Cancel profile run
        </Button>
      )}
    </div>
  );
}
