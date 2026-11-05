import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { describeError } from "@/lib/api";
import { LaterHandoffBanner } from "@/features/browser/later-handoff-banner";

import {
  answerQuestion,
  applicationKey,
  applicationsKey,
  type Application,
  type Question,
} from "./apply-api";

/**
 * One click Apply's question (SPEC-apply, "One click Apply"; apply A19): the assistant stopped on
 * a field it could not fill while nobody watched. The server's fixed sentence names the field's
 * label and the site, never the assistant's words. A fact gets a text box, whose answer is given
 * to the assistant when it goes on, and saved to Saved answers when "Remember this answer" is
 * ticked; an action (a password, a code, a captcha) is the person's on the site: Take over opens
 * it, then "I did it, go on". No dashes as separators.
 */
export function AgentQuestion({
  application,
  question,
}: {
  application: Application;
  question: Question;
}) {
  const client = useQueryClient();
  const [answer, setAnswer] = useState("");
  const [remember, setRemember] = useState(false);
  const fact = question.kind === "fact";
  const send = useMutation({
    mutationFn: () =>
      answerQuestion(application.id, fact ? answer : "", fact && remember),
    onSuccess: (updated) => {
      void client.invalidateQueries({
        queryKey: applicationsKey(updated.posting_id),
      });
      void client.invalidateQueries({ queryKey: applicationKey(updated.id) });
    },
  });
  return (
    <form
      className="flex flex-col gap-2"
      data-testid="agent-question"
      onSubmit={(event) => {
        event.preventDefault();
        if (!fact || answer.trim()) send.mutate();
      }}
    >
      <p className="text-sm font-medium">{question.sentence}</p>
      {fact ? (
        <>
          <label className="flex flex-col gap-1 text-sm">
            <span>Your answer</span>
            <textarea
              className="min-h-20 rounded-md border bg-background p-2 text-sm"
              maxLength={5000}
              value={answer}
              onChange={(event) => {
                setAnswer(event.target.value);
              }}
            />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={remember}
              onChange={(event) => {
                setRemember(event.target.checked);
              }}
            />
            <span>Remember this answer</span>
          </label>
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
        {fact ? "Send the answer" : "I did it, go on"}
      </Button>
      {send.isError ? (
        <p className="text-sm text-destructive" role="alert">
          {describeError(send.error)}
        </p>
      ) : null}
    </form>
  );
}
