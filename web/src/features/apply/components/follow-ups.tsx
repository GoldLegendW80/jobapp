import { useId } from "react";
import { toast } from "sonner";

import { LoadError } from "@/components/blocks/load-error";
import { Section } from "@/components/blocks/section";
import { Field, FieldLabel } from "@/components/ui/field";
import { Switch } from "@/components/ui/switch";
import { describeError } from "@/lib/api";

import { useMailAccount } from "@/features/mail/api/use-mail-account";
import { useFollowUps, useSaveFollowUpsOn } from "../api/use-followups";
import { FollowUpCard } from "./follow-up-card";

/** What the section is for, under its title. */
export const FOLLOW_UPS_INTRO =
  "When to write to a company again after you applied: a first follow up after 7 days, a second 7 days later, never a third, and a thank you note the day after an interview. You send each one yourself.";

/**
 * The follow ups of the Applications page (SPEC-apply.md, "Follow ups"): the person's switch,
 * then each followed posting whose follow up is due or waits, due first, and a line for those
 * where none is asked any more. Hidden while there is nothing and the switch is on.
 */
export function FollowUps() {
  const id = useId();
  const query = useFollowUps();
  const account = useMailAccount();
  const save = useSaveFollowUpsOn();
  const gmail =
    account.data?.available === true &&
    account.data.status === "ok" &&
    account.data.can_send;
  if (query.isPending) return null;
  if (query.isError && query.data === undefined)
    return (
      <Section title="Follow ups">
        <LoadError query={query}>The follow ups could not be loaded.</LoadError>
      </Section>
    );
  const { enabled, today, items } = query.data;
  const open = items.filter((item) => item.can_draft);
  const ended = items.filter((item) => !item.can_draft);
  if (enabled && items.length === 0) return null;
  const due = open.filter((item) => item.urgency === "due").length;
  return (
    <Section
      title={due ? `Follow ups (${due} due)` : "Follow ups"}
      description={FOLLOW_UPS_INTRO}
      actions={
        <Field orientation="horizontal" className="gap-2">
          <Switch
            id={`${id}-on`}
            checked={enabled}
            disabled={save.isPending}
            onCheckedChange={(on) => {
              save.mutate(on, {
                onSuccess: () =>
                  toast.success(on ? "Follow ups on." : "Follow ups off."),
                onError: (error) => toast.error(describeError(error)),
              });
            }}
          />
          <FieldLabel htmlFor={`${id}-on`}>Remind me</FieldLabel>
        </Field>
      }
    >
      {!enabled ? (
        <p className="text-sm text-muted-foreground">
          Follow ups are off: no reminder and no draft.
        </p>
      ) : (
        <div className="flex flex-col gap-4">
          {open.length > 0 ? (
            <ul className="flex flex-col gap-4">
              {open.map((item) => (
                <li key={item.posting_id}>
                  <FollowUpCard item={item} today={today} gmail={gmail} />
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No follow up waits.</p>
          )}
          {ended.length > 0 ? (
            <p className="text-sm text-muted-foreground">
              {ended.length === 1
                ? "1 more application asks no follow up now: no answer after two, an answer to read, or a thank you note sent."
                : `${ended.length} more applications ask no follow up now: no answer after two, an answer to read, or a thank you note sent.`}
            </p>
          ) : null}
        </div>
      )}
    </Section>
  );
}
