import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { describeError } from "@/lib/api";
import { LaterHandoffBanner } from "@/components/shared/browser/later-handoff-banner";

import type { Application, Question } from "@/features/apply/api/apply-api";
import { useAnswerQuestion } from "@/features/apply/api/use-application-actions";

/**
 * One click Apply's question (SPEC-apply, "One click Apply"; apply A19): the assistant stopped on
 * a field it could not fill while nobody watched. The server's fixed sentence names the field's
 * label and the site, never the assistant's words. A fact gets a text box, whose answer is given
 * to the assistant when it goes on, and saved to Saved answers when "Remember this answer" is
 * ticked; an action (a password, a code, a captcha) is the person's on the site: Take over opens
 * it, then "I did it, go on". Asked after a control that may have sent the form (`after_send`,
 * the application `submit_unknown`), the page says so first, and the answer is the person's Try
 * again: the assistant looks on the site for the earlier one before it sends again. No dashes as
 * separators.
 */
export const AFTER_SEND =
  "The assistant may have sent this application already, before it stopped here. Look for a confirmation mail or on the site first.";

export function AgentQuestion({
  application,
  question,
}: {
  application: Application;
  question: Question;
}) {
  const id = useId();
  const [answer, setAnswer] = useState("");
  const [remember, setRemember] = useState(false);
  const fact = question.kind === "fact";
  const afterSend = question.after_send === true;
  const send = useAnswerQuestion(application.id);
  return (
    <form
      className="flex flex-col gap-2"
      data-testid="agent-question"
      onSubmit={(event) => {
        event.preventDefault();
        if (!fact || answer.trim())
          send.mutate({
            answer: fact ? answer : "",
            remember: fact && remember,
          });
      }}
    >
      <p className="text-sm font-medium">{question.sentence}</p>
      {afterSend ? <p className="text-sm">{AFTER_SEND}</p> : null}
      {fact ? (
        <>
          <div className="flex flex-col gap-1">
            <Label htmlFor={`${id}-answer`}>Your answer</Label>
            <Textarea
              id={`${id}-answer`}
              className="min-h-20"
              maxLength={5000}
              value={answer}
              onChange={(event) => {
                setAnswer(event.target.value);
              }}
            />
          </div>
          <div className="flex items-center gap-2">
            <Checkbox
              id={`${id}-remember`}
              checked={remember}
              onCheckedChange={(checked) => {
                setRemember(checked === true);
              }}
            />
            <Label htmlFor={`${id}-remember`}>Remember this answer</Label>
          </div>
        </>
      ) : (
        <LaterHandoffBanner
          module="application"
          refId={application.id}
          address={application.form_url}
          heading={false}
        />
      )}
      <Button
        className="w-full sm:w-fit"
        type="submit"
        disabled={send.isPending || (fact && !answer.trim())}
      >
        {afterSend
          ? fact
            ? "Try again with this answer"
            : "I did it, try again"
          : fact
            ? "Send the answer"
            : "I did it, go on"}
      </Button>
      {send.isError ? (
        <p className="text-sm text-destructive" role="alert">
          {describeError(send.error)}
        </p>
      ) : null}
    </form>
  );
}
