import { ArrowLeft, CircleAlert, Info } from "lucide-react";
import { Link, useParams } from "react-router";
import { PageHeader } from "@/components/blocks/page-header";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { HandoffBanner } from "@/components/shared/browser/handoff-banner";
import { useHandoff } from "@/features/browser/api/use-handoff";
import { LiveBrowser } from "@/components/shared/browser/live-browser";
import { LaterHandoffBanner } from "@/components/shared/browser/later-handoff-banner";
import { useBrowser } from "@/features/browser/api/use-browser";
import { describeError, errorDetail, isApiError } from "@/lib/api";
import { siteProfileProblem, siteProfileStateWord } from "@/lib/wording";
import { autosaveSentence } from "../api/site-profile-api";
import {
  useSiteProfileAction,
  useSiteProfileRun,
  useSiteProfileRunCache,
} from "../api/use-site-profile-run";
import { SectionReview } from "../components/site-profile/section-review";

export function SiteProfileRunPage() {
  const { id = "" } = useParams();
  return <SiteProfileRunContent key={id} id={id} />;
}

function SiteProfileRunContent({ id }: { id: string }) {
  const { handoff: currentHandoff } = useHandoff();
  const query = useSiteProfileRun(id);
  const browser = useBrowser({
    enabled: ["queued", "filling", "saving", "needs_person"].includes(
      query.data?.state ?? "",
    ),
    refetchInterval: 2000,
  });
  const browserReady = browser.isFetchedAfterMount && !browser.isError;
  const handoff = browserReady ? currentHandoff : null;
  const { update, refresh } = useSiteProfileRunCache(id);
  const operation = useSiteProfileAction(id);
  if (query.isPending)
    return (
      <p
        role="status"
        className="mx-auto flex w-full max-w-6xl items-center gap-2 text-sm text-muted-foreground"
      >
        <Spinner aria-hidden="true" role={undefined} />
        Loading the completion of your profile.
      </p>
    );
  if (query.error)
    return (
      <div className="mx-auto w-full max-w-6xl">
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription className="flex flex-col items-start gap-2">
            {isApiError(query.error) && query.error.status === 404
              ? "This request to complete your profile on a site no longer exists."
              : describeError(query.error)}
            <Button asChild variant="outline" size="sm">
              <Link to="/account">Back to Account</Link>
            </Button>
          </AlertDescription>
        </Alert>
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
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
      <Button asChild variant="ghost" size="sm" className="w-fit">
        <Link to="/account">
          <ArrowLeft aria-hidden="true" />
          Back to Account
        </Link>
      </Button>
      <div className="flex flex-col gap-2">
        <PageHeader
          title={`Complete your profile on ${run.host}`}
          description={<span className="break-all">{run.url}</span>}
        />
        <p role="status" className="text-sm font-medium">
          {siteProfileStateWord(run.state)}
        </p>
      </div>
      {run.error_code && (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription>
            {siteProfileProblem(run.error_code)}
          </AlertDescription>
        </Alert>
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
        <Card>
          <CardContent className="flex flex-col items-start gap-3">
            <p>{autosaveSentence}</p>
            <Button
              disabled={operation.isPending}
              onClick={() => {
                operation.mutate("confirm-autosave");
              }}
            >
              I understand, fill empty fields
            </Button>
          </CardContent>
        </Card>
      )}
      {run.autosave && (
        <Alert role="note">
          <Info aria-hidden="true" />
          <AlertDescription>{autosaveSentence}</AlertDescription>
        </Alert>
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
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription>
            {errorDetail(operation.error) ?? describeError(operation.error)}
          </AlertDescription>
        </Alert>
      )}
      {!["cancelled", "done", "failed"].includes(run.state) && (
        <Button
          variant="outline"
          className="w-fit"
          disabled={operation.isPending}
          onClick={() => {
            operation.mutate("cancel");
          }}
        >
          Stop completing my profile on this site
        </Button>
      )}
    </div>
  );
}
