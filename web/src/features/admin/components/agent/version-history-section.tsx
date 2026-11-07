import { Card, CardContent } from "@/components/ui/card";

import type { AgentPage } from "../../api/agents-api";
import { Moment } from "@/components/shared/admin/moment";
import { AgentSection } from "./agent-section";

/** Every version saved for the agent, newest first, and which one runs use: one row each. */
export function VersionHistorySection({ page }: { page: AgentPage }) {
  return (
    <AgentSection
      title="History of versions"
      description="Every version saved for this agent, newest first."
      surface="own"
    >
      {page.versions.length ? (
        <Card className="gap-0 py-0">
          <ol aria-label="Versions, newest first" className="divide-y">
            {page.versions.map((version) => (
              <li
                key={version.version}
                className="flex flex-col gap-1 px-6 py-4 text-sm"
              >
                <p className="font-medium">
                  Version {String(version.version)}
                  {version.version === page.version ? " (runs use it)" : ""}
                </p>
                <p className="text-muted-foreground">
                  {version.fields.length
                    ? `Changes ${version.fields.join(", ")}.`
                    : "No change: the code's definition."}{" "}
                  Saved by {version.saved_by ?? "an account that is gone"}{" "}
                  <Moment at={version.saved_at} />.
                </p>
                {version.note ? (
                  <p className="max-w-prose [overflow-wrap:anywhere]">
                    {version.note}
                  </p>
                ) : null}
              </li>
            ))}
          </ol>
        </Card>
      ) : (
        <Card>
          <CardContent className="text-sm text-muted-foreground">
            No version saved yet: runs use the code&apos;s definition.
          </CardContent>
        </Card>
      )}
    </AgentSection>
  );
}
