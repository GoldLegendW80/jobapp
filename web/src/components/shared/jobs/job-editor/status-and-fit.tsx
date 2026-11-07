import { Section } from "@/components/blocks/section";
import { Card, CardContent } from "@/components/ui/card";

import type { JobDetail } from "@/features/jobs/api/jobs-api";
import type { Save } from "@/features/jobs/api/use-job-editor";
import { FitControl } from "./fit-control";
import { StatusControl } from "./status-control";

/** Where the posting stands for the person, and its fit, both editable. */
export function StatusAndFit({ job, save }: { job: JobDetail; save: Save }) {
  return (
    <Section title="Status and fit">
      <Card>
        <CardContent className="flex flex-col gap-6">
          <StatusControl job={job} save={save} />
          <FitControl key={job.id} job={job} save={save} />
        </CardContent>
      </Card>
    </Section>
  );
}
