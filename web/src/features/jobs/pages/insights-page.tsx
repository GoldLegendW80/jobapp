import { ChartNoAxesColumn } from "lucide-react";
import { Link } from "react-router";

import { EmptyState } from "@/components/blocks/empty-state";
import { LoadError } from "@/components/blocks/load-error";
import { LoadingCard } from "@/components/blocks/loading-card";
import { Page, PageBody } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { Section } from "@/components/blocks/section";
import { Button } from "@/components/ui/button";
import { useTitle } from "@/hooks/use-title";

import { useInsights, type Insights } from "../api/insights-api";
import { FunnelSection } from "../components/insights/funnel-section";
import { OutcomeTable } from "../components/insights/outcome-table";
import {
  FirstAnswerSection,
  PassReasonsSection,
} from "../components/insights/side-sections";
import { SilentSection } from "../components/insights/silent-section";
import {
  atsLabel,
  BAND_LABELS,
  channelLabel,
  FLOORS,
} from "../helpers/insights";

const TITLE = "Your search in numbers";

function Numbers({ data }: { data: Insights }) {
  return (
    <PageBody
      aside={
        <>
          <FirstAnswerSection first={data.first_answer} />
          <PassReasonsSection
            reasons={data.pass_reasons}
            without={data.passes_without_reason}
          />
        </>
      }
    >
      <FunnelSection funnel={data.funnel} />
      <Section
        title="Interviews by fit"
        description={`Your applications by the fit their posting has today. A rate shows from ${String(FLOORS.BAND_MIN)} applications in a band.`}
      >
        <OutcomeTable
          caption="Applications and interviews by fit"
          groupName="Fit"
          groups={data.by_fit}
          label={(key) => BAND_LABELS[key] ?? key}
        />
      </Section>
      <Section
        title="By application site"
        description={`The applicant tracking system each application went through, read from its form or its posting's address. A rate shows from ${String(FLOORS.GROUP_MIN)} applications.`}
      >
        <OutcomeTable
          caption="Applications and interviews by application site"
          groupName="Site"
          groups={data.by_ats}
          label={atsLabel}
        />
      </Section>
      <Section
        title="By source"
        description={`Where each posting was first found. A rate shows from ${String(FLOORS.GROUP_MIN)} applications.`}
      >
        <OutcomeTable
          caption="Applications and interviews by source"
          groupName="Source"
          groups={data.by_source}
          label={channelLabel}
        />
      </Section>
      <SilentSection
        rows={data.silent}
        days={data.silent_days}
        undated={data.silent_undated}
      />
    </PageBody>
  );
}

/**
 * /jobs/insights, reached from the Jobs page's menu: the person's own counts, no model and no
 * advice. Where a group is too small the page says "Too few to say" instead of a rate.
 */
export function InsightsPage() {
  useTitle(TITLE);
  const query = useInsights();
  return (
    <Page>
      <PageHeader
        trail={[{ to: "/jobs", label: "Jobs" }]}
        title={TITLE}
        description="Counts from your own postings and applications as they stand today. Facts about your rows, not advice."
      />
      {query.isPending ? (
        <LoadingCard>Counting your postings</LoadingCard>
      ) : query.isError ? (
        <LoadError query={query}>Your numbers could not be loaded.</LoadError>
      ) : query.data.funnel.found === 0 ? (
        <EmptyState
          icon={ChartNoAxesColumn}
          title="Nothing to count yet"
          description="Once postings are found and you apply, their numbers show here."
          actions={
            <Button asChild>
              <Link to="/jobs">Go to Jobs</Link>
            </Button>
          }
        />
      ) : (
        <Numbers data={query.data} />
      )}
    </Page>
  );
}
