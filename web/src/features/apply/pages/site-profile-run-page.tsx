import { CircleAlert, ClipboardList, Info, SearchX } from "lucide-react";
import { Link, useParams } from "react-router";

import { EmptyState } from "@/components/blocks/empty-state";
import { LoadError } from "@/components/blocks/load-error";
import { LoadingCard } from "@/components/blocks/loading-card";
import { Page, PageBody } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { Section } from "@/components/blocks/section";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { HandoffBanner } from "@/components/shared/browser/handoff-banner";
import { useHandoff } from "@/features/browser/api/use-handoff";
import { LiveBrowser } from "@/components/shared/browser/live-browser";
import { LaterHandoffBanner } from "@/components/shared/browser/later-handoff-banner";
import { useBrowser } from "@/features/browser/api/use-browser";
import { describeError, errorDetail, isApiError } from "@/lib/api";
import { siteProfileProblem, siteProfileStateWord } from "@/lib/wording";
import { autosaveSentence, type ProfileRun } from "../api/site-profile-api";
import {
  useSiteProfileAction,
  useSiteProfileRun,
  useSiteProfileRunCache,
} from "../api/use-site-profile-run";
import { SectionReview } from "../components/site-profile/section-review";
import { SectionScreenshots } from "../components/site-profile/section-screenshots";

const TRAIL = [{ to: "/account", label: "Account" }];
const ENDED = ["cancelled", "done", "failed"];

export function SiteProfileRunPage() {
  const { id = "" } = useParams();
  return <SiteProfileRunContent key={id} id={id} />;
}

/**
 * /site-profiles/:id, an item page: the sections to review in the main column, each with its
 * fields table and its Approve and Skip; where the run stands and the screenshots of the site in
 * the side rail; Stop in the header.
 */
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
      <Page>
        <PageHeader trail={TRAIL} title="Complete your profile on a site" />
        <LoadingCard>Loading the completion of your profile.</LoadingCard>
      </Page>
    );
  if (query.error)
    return (
      <Page>
        <PageHeader trail={TRAIL} title="Complete your profile on a site" />
        {isApiError(query.error) && query.error.status === 404 ? (
          <EmptyState
            icon={SearchX}
            title="This request no longer exists"
            description="This request to complete your profile on a site no longer exists. Start a new one from your Account page."
            actions={
              <Button asChild variant="outline">
                <Link to="/account">Back to Account</Link>
              </Button>
            }
          />
        ) : (
          <LoadError query={query}>
            The completion of your profile could not be loaded.
          </LoadError>
        )}
      </Page>
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
  const liveId =
    !handoff && liveLease && ["filling", "saving"].includes(run.state)
      ? liveLease.id
      : null;
  const signinBanner =
    !handoff &&
    run.needs === "signin" &&
    !browser.isFetching &&
    !browser.data?.lease?.automation;
  const openingSignin =
    run.needs === "signin" && !handoff && browser.data?.lease?.automation;
  const waitingOnPerson =
    Boolean(handoff) ||
    signinBanner ||
    openingSignin ||
    run.needs === "autosave_confirmation";
  return (
    <Page>
      <PageHeader
        trail={TRAIL}
        title={`Complete your profile on ${run.host}`}
        description={<span className="break-all">{run.url}</span>}
        actions={
          ENDED.includes(run.state) ? null : (
            <Button
              variant="outline"
              disabled={operation.isPending}
              onClick={() => {
                operation.mutate("cancel");
              }}
            >
              Stop completing my profile on this site
            </Button>
          )
        }
      />
      <PageBody aside={<RunAside run={run} />}>
        {operation.error && (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>
              {errorDetail(operation.error) ?? describeError(operation.error)}
            </AlertDescription>
          </Alert>
        )}
        {handoff ? <HandoffBanner liveOpen /> : null}
        {signinBanner ? (
          <LaterHandoffBanner
            module="site_profile"
            refId={run.id}
            address={run.url}
          />
        ) : null}
        {openingSignin ? (
          <p role="status" className="text-sm">
            Opening your sign in view.
          </p>
        ) : null}
        {run.needs === "autosave_confirmation" && (
          <Section
            title="Before the assistant starts"
            description="Nothing is typed on the site until you agree."
          >
            <Card>
              <CardContent className="flex flex-col items-start gap-3">
                <p className="max-w-prose text-sm">{autosaveSentence}</p>
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
          </Section>
        )}
        {liveId ? <LiveBrowser key={liveId} person={false} /> : null}
        {run.sections.map((section) => (
          <SectionReview
            key={section.section}
            run={run}
            section={section}
            update={update}
            refresh={refresh}
          />
        ))}
        {run.sections.length === 0 && !liveId && !waitingOnPerson ? (
          <EmptyState
            icon={ClipboardList}
            title={
              ENDED.includes(run.state)
                ? "Nothing to review"
                : "No section to review yet"
            }
            description={
              ENDED.includes(run.state)
                ? "No section of your profile was filled on this site."
                : run.autosave
                  ? "Each section of the site shows here once the assistant has filled it."
                  : "Each section of the site shows here once the assistant has filled it. Nothing is saved before you approve it."
            }
          />
        ) : null}
      </PageBody>
    </Page>
  );
}

/** Where the run stands, why it stopped, and the screenshots of the site. */
function RunAside({ run }: { run: ProfileRun }) {
  const shot = run.sections.some((section) =>
    section.screenshots.some(
      (one) => one.kind === "before" || one.kind === "after",
    ),
  );
  return (
    <>
      <Section title="Progress">
        <Card>
          <CardContent className="flex flex-col gap-3">
            <p role="status" className="text-sm font-medium">
              {siteProfileStateWord(run.state)}
            </p>
            {run.error_code && (
              <Alert variant="destructive">
                <CircleAlert aria-hidden="true" />
                <AlertDescription>
                  {siteProfileProblem(run.error_code)}
                </AlertDescription>
              </Alert>
            )}
            {run.autosave && (
              <Alert role="note">
                <Info aria-hidden="true" />
                <AlertDescription>{autosaveSentence}</AlertDescription>
              </Alert>
            )}
          </CardContent>
        </Card>
      </Section>
      {shot ? (
        <Section
          title="On the site"
          description="Each opens full size in a new tab."
        >
          <SectionScreenshots run={run} />
        </Section>
      ) : null}
    </>
  );
}
