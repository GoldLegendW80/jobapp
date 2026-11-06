import type { AgentPage } from "../../api/agents-api";
import { Moment } from "@/components/shared/admin/moment";
import { AgentSection } from "./agent-section";

/** Every version saved for the agent, newest first, and which one runs use. */
export function VersionHistorySection({ page }: { page: AgentPage }) {
  return (
    <AgentSection id="history" title="History of versions">
      {page.versions.length ? (
        <ol
          aria-label="Versions, newest first"
          className="divide-y rounded-md border"
        >
          {page.versions.map((version) => (
            <li
              key={version.version}
              className="flex flex-col gap-1 p-3 text-sm"
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
                <p className="[overflow-wrap:anywhere]">{version.note}</p>
              ) : null}
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-sm text-muted-foreground">
          No version saved yet: runs use the code&apos;s definition.
        </p>
      )}
    </AgentSection>
  );
}
