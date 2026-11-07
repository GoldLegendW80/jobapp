import { CircleAlert } from "lucide-react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

import type { AgentPage, EditableField } from "../../api/agents-api";
import {
  codeValueInWords,
  FIELD_LABEL,
} from "@/features/admin/helpers/agent-labels";
import { PromptDiff } from "./prompt-diff";

/** The notice of the fields the code changed since the version that runs was saved. */
export function CodeChangedNotice({
  page,
  onRevert,
  busy,
}: {
  page: AgentPage;
  onRevert: () => void;
  busy: boolean;
}) {
  const fields = page.code_changed.filter(
    (field): field is EditableField => field in FIELD_LABEL,
  );
  return (
    <Alert variant="warning">
      <CircleAlert aria-hidden="true" />
      <AlertDescription className="flex min-w-0 flex-col gap-3">
        {fields.map((field) =>
          field === "system_prompt" ? (
            <div key={field} className="flex min-w-0 flex-col gap-2">
              <p>
                The code&apos;s prompt changed since you saved this version.
              </p>
              {page.code_prompt_then !== null ? (
                <PromptDiff
                  from={page.code_prompt_then}
                  to={page.code.system_prompt}
                  title="What the code changed in its prompt"
                />
              ) : null}
            </div>
          ) : (
            <p key={field} className="[overflow-wrap:anywhere]">
              The code&apos;s {FIELD_LABEL[field]} changed since you saved this
              version. The code&apos;s value now:{" "}
              {codeValueInWords(page.code[field])}.
            </p>
          ),
        )}
        <p>
          Runs still use version {String(page.version)}. Edit it below, or go
          back to the code&apos;s version.
        </p>
        <Button
          variant="outline"
          size="sm"
          className="self-start"
          disabled={busy}
          onClick={onRevert}
        >
          Revert to the code&apos;s version
        </Button>
      </AlertDescription>
    </Alert>
  );
}
