import { useId, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Textarea } from "@/components/ui/textarea";

import type { Message } from "@/features/documents/api/chat-api";
import { failureSentence } from "@/features/documents/api/documents-api";
import { useKeepRule } from "@/features/documents/api/use-keep-rule";
import { RULE_MAX } from "@/features/documents/api/writing-rules-api";

type Step = "offer" | "editing" | "kept" | "already" | "dismissed";

/**
 * Under a document's chat: the newest message of the person, once answered, when it corrects the
 * wording, offered as a writing rule (SPEC-documents.md, "The chat, and keeping a rule"). Nothing
 * is written before Keep: the person reads the rule, may change it, and keeps it in the block
 * "Writing rules" of their profile's facts, which every writer reads. A fact is never a rule: the
 * api refuses one and says where facts go.
 */
export function KeepRule({
  documentId,
  messages,
  answering,
}: {
  documentId: string;
  messages: Message[];
  answering: boolean;
}) {
  const id = useId();
  const newest = [...messages].reverse().find((one) => one.role === "user");
  const [state, setState] = useState<{
    id: number;
    step: Step;
    text: string;
  }>();
  const keep = useKeepRule(documentId, {
    onKept: (kept) => {
      if (newest)
        setState({
          id: newest.id,
          step: kept.kept ? "kept" : "already",
          text: kept.rule,
        });
    },
  });
  if (!newest?.rule || answering) return null;
  const rule = newest.rule;
  const current =
    state !== undefined && state.id === newest.id
      ? state
      : { id: newest.id, step: "offer" as Step, text: rule };
  const go = (step: Step, text = current.text) => {
    keep.reset();
    setState({ id: newest.id, step, text });
  };

  if (current.step === "dismissed") return null;
  if (current.step === "kept" || current.step === "already")
    return (
      <p
        role="status"
        className="text-sm text-muted-foreground"
        data-testid="rule-kept"
      >
        {current.step === "kept"
          ? `Kept in your writing rules: “${current.text}”. Every CV and letter written from now on follows it.`
          : "This rule is in your writing rules already."}
      </p>
    );
  if (current.step === "offer")
    return (
      <div
        className="flex flex-wrap items-center gap-2"
        data-testid="rule-offer"
      >
        <p className="text-sm text-muted-foreground">
          Follow this in every CV and letter?
        </p>
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            go("editing");
          }}
        >
          Keep as a rule
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            go("dismissed");
          }}
        >
          Not now
        </Button>
      </div>
    );
  const text = current.text.trim();
  return (
    <div
      className="flex flex-col gap-3 rounded-md border p-3"
      data-testid="rule-editor"
    >
      <Field>
        <FieldLabel htmlFor={`${id}-rule`}>Your writing rule</FieldLabel>
        <Textarea
          id={`${id}-rule`}
          aria-describedby={`${id}-from`}
          maxLength={RULE_MAX}
          rows={2}
          value={current.text}
          onChange={(event) => {
            go("editing", event.target.value);
          }}
        />
        <FieldDescription id={`${id}-from`} className="text-xs">
          {`From your message: “${newest.text}”. It joins the writing rules of your profile's facts. Say how to write, never what is true of you.`}
        </FieldDescription>
      </Field>
      {keep.isError ? (
        <Alert variant="destructive">
          <AlertDescription>{failureSentence(keep.error)}</AlertDescription>
        </Alert>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={text === "" || keep.isPending}
          onClick={() => {
            keep.mutate({ messageId: newest.id, text });
          }}
        >
          Keep
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            go("offer", rule);
          }}
        >
          Cancel
        </Button>
      </div>
    </div>
  );
}
