import { useState } from "react";

import { Section } from "@/components/blocks/section";
import { ChatTurn } from "@/components/shared/agents/chat/chat-turn";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";

import { failureSentence } from "../../api/documents-api";
import type { PracticeMessage } from "../../api/interview-prep-api";
import { usePractice } from "../../api/use-interview-prep";

/**
 * Practice: the partner asks one question at a time and gives feedback on each answer. Its
 * answers are written by a private run, so the page polls the preparation until one lands.
 */
export function PrepPractice({
  jobId,
  documentId,
  messages,
  answering,
}: {
  jobId: string;
  documentId: string;
  messages: PracticeMessage[];
  answering: boolean;
}) {
  const send = usePractice(jobId, documentId);
  const [text, setText] = useState("");
  const started = messages.length > 0;
  const submit = (value: string) => {
    send.mutate(value, {
      onSuccess: () => {
        setText("");
      },
    });
  };
  return (
    <Section
      title="Practice"
      description="One question at a time, with feedback on each answer."
    >
      <Card>
        <CardContent className="flex flex-col gap-4 text-sm">
          <ol className="flex flex-col gap-3" aria-live="polite">
            {messages.map((message) => (
              <li key={message.id} data-testid="practice-message">
                <ChatTurn
                  from={message.role === "user" ? "user" : "agent"}
                  label={message.role === "user" ? "You:" : "Partner:"}
                >
                  <span className="whitespace-pre-wrap">{message.text}</span>
                </ChatTurn>
              </li>
            ))}
          </ol>
          {answering ? (
            <p
              className="flex items-center gap-2 text-muted-foreground"
              role="status"
            >
              <Spinner aria-hidden="true" role={undefined} />
              The partner is answering
            </p>
          ) : null}
          {send.isError ? (
            <Alert variant="destructive">
              <AlertDescription>{failureSentence(send.error)}</AlertDescription>
            </Alert>
          ) : null}
          {started ? (
            <form
              className="flex flex-col gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                submit(text.trim());
              }}
            >
              <Label htmlFor="practice-answer">Your answer</Label>
              <Textarea
                id="practice-answer"
                value={text}
                maxLength={2000}
                onChange={(event) => {
                  setText(event.target.value);
                }}
              />
              <Button
                type="submit"
                size="sm"
                className="self-start"
                disabled={text.trim() === "" || answering || send.isPending}
              >
                Send
              </Button>
            </form>
          ) : (
            <Button
              size="sm"
              className="self-start"
              disabled={answering || send.isPending}
              onClick={() => {
                submit("Ask me the first question.");
              }}
            >
              Start practice
            </Button>
          )}
        </CardContent>
      </Card>
    </Section>
  );
}
