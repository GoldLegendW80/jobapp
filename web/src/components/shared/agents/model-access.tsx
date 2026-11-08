import { useEffect, useRef } from "react";

import { LoadError } from "@/components/blocks/load-error";
import type { ModelCredential } from "@/features/agents/api/agents-api";
import {
  useModelAccess,
  type VerifyOutcome,
} from "@/features/agents/api/use-model-access";
import { KIND_LABEL } from "@/features/agents/helpers/model-credential";

import {
  CredentialForm,
  type CredentialStatus,
} from "./model-access/credential-form";
import { RemoveCredentialDialog } from "./model-access/remove-credential-dialog";

/** What the token field says: loading, the check running, accepted, refused, or plain. */
function statusOf(
  saved: ModelCredential | null,
  outcome: VerifyOutcome | undefined,
  checking: boolean,
): CredentialStatus {
  // A subscription token is checked by one Claude Code run in the worker: `check` follows it.
  const check = saved?.check;
  if (checking || check?.state === "checking")
    return {
      tone: "busy",
      text:
        saved?.kind === "claude_subscription"
          ? "Checking it with Claude Code"
          : "Checking it with Anthropic",
    };
  if (outcome?.tone === "error") return { tone: "error", text: outcome.text };
  if (check && check.state !== "verified")
    return { tone: "error", text: check.message };
  if (!saved)
    return {
      tone: "none",
      text: "No credential saved. Agents need one to run.",
    };
  const name = `${KIND_LABEL[saved.kind]} ending in ${saved.last4}`;
  if (outcome?.tone === "success" || saved.verified_at)
    return { tone: "success", text: `${name}, verified.` };
  return { tone: "none", text: outcome?.text ?? `${name}, not verified yet.` };
}

/**
 * The Claude credential the person's agents run on, in one field: saved, it is checked with
 * Anthropic at once (and once on opening while it never was), the field turning green or red;
 * Remove asks first. Account's Model access and the wizard's first step.
 */
export function ModelAccess() {
  const access = useModelAccess();
  const { saved, verify, remove } = access;
  const verifyNow = verify.mutate;
  const checked = useRef<string>();

  // Each credential saved is checked once, by its date of saving, unless a check already ran.
  useEffect(() => {
    if (
      !saved ||
      saved.verified_at ||
      saved.check ||
      checked.current === saved.updated_at
    )
      return;
    checked.current = saved.updated_at;
    verifyNow();
  }, [saved, verifyNow]);

  if (access.query.isError)
    return (
      <LoadError query={access.query}>
        Your credential could not be loaded.
      </LoadError>
    );

  return (
    <>
      <CredentialForm
        saved={saved}
        loading={access.query.isPending}
        status={statusOf(saved, access.outcome, verify.isPending)}
        onSaving={access.clearOutcome}
        onSaved={access.setSaved}
        onRemove={access.askToRemove}
      />

      <RemoveCredentialDialog
        open={access.confirming}
        removing={remove.isPending}
        error={remove.error}
        onClose={access.closeRemove}
        onRemove={() => {
          remove.mutate();
        }}
      />
    </>
  );
}
