import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, LoaderCircle } from "lucide-react";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { message } from "@/features/admin/errors";
import { errorDetail, isApiError } from "@/lib/api";

import { ConfirmDialog } from "./confirm-dialog";
import {
  ANSWER_MAX,
  answerBankKey,
  answerText,
  getAnswerBank,
  saveBankAnswer,
  type Bank,
  type BankEntry,
} from "./settings-api";

type Change = { key: string; answer: string | null; done: string };

/**
 * "Saved answers": the person's answer bank, the answers reused when the same question comes
 * back on another form. Each can be changed or forgotten (PUT /api/apply/answer-bank, one entry
 * at a time). An answer already forgotten elsewhere answers 404: the list reloads and says so.
 */
export function AnswerBank() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: answerBankKey,
    queryFn: ({ signal }) => getAnswerBank(signal),
  });
  const [editing, setEditing] = useState<string | null>(null);
  const [forgetting, setForgetting] = useState<BankEntry | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: ({ key, answer }: Change) => saveBankAnswer(key, answer),
    onMutate: () => {
      setNotice(null);
    },
    onSuccess: (bank, change) => {
      client.setQueryData<Bank>(answerBankKey, bank);
      setEditing(null);
      setForgetting(null);
      toast.success(change.done);
    },
    onError: (error) => {
      setForgetting(null);
      if (isApiError(error) && error.status === 404) {
        setNotice(
          `${errorDetail(error) ?? "No such saved answer."} The list now shows your current answers.`,
        );
        void client.invalidateQueries({ queryKey: answerBankKey });
        return;
      }
      setNotice(message(error));
    },
  });

  return (
    <Card aria-labelledby="bank-card-title">
      <CardHeader>
        <CardTitle>
          <h2 id="bank-card-title" className="text-lg">
            Saved answers
          </h2>
        </CardTitle>
        <CardDescription>
          Answers the app reuses when the same question comes back on another
          form. Change one, or forget it.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {notice ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>{notice}</AlertDescription>
          </Alert>
        ) : null}
        {query.isPending ? (
          <p role="status" className="text-sm text-muted-foreground">
            Loading your saved answers
          </p>
        ) : query.isError ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription className="flex flex-col items-start gap-2">
              {message(query.error)}
              <Button
                variant="outline"
                size="sm"
                onClick={() => void query.refetch()}
              >
                Retry
              </Button>
            </AlertDescription>
          </Alert>
        ) : query.data.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No saved answer yet.</p>
        ) : (
          <ul
            aria-label="Your saved answers"
            className="flex flex-col divide-y"
          >
            {query.data.items.map((entry) => (
              <li key={entry.question_key} className="flex flex-col gap-2 py-3">
                <span className="text-sm font-medium break-words">
                  {entry.question}
                </span>
                {editing === entry.question_key ? (
                  <AnswerForm
                    entry={entry}
                    pending={save.isPending}
                    onCancel={() => {
                      setEditing(null);
                    }}
                    onSave={(answer) => {
                      save.mutate({
                        key: entry.question_key,
                        answer,
                        done: "Answer saved.",
                      });
                    }}
                  />
                ) : (
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                    <div className="flex min-w-0 flex-col gap-0.5">
                      <span className="text-sm break-words whitespace-pre-wrap">
                        {answerText(entry.answer)}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {entry.used_count === 1
                          ? "Used once"
                          : `Used ${entry.used_count} times`}
                      </span>
                    </div>
                    <div className="flex shrink-0 gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        aria-label={`Edit the answer to ${entry.question}`}
                        disabled={save.isPending}
                        onClick={() => {
                          setEditing(entry.question_key);
                        }}
                      >
                        Edit
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        aria-label={`Forget the answer to ${entry.question}`}
                        disabled={save.isPending}
                        onClick={() => {
                          setForgetting(entry);
                        }}
                      >
                        Forget
                      </Button>
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
      <ConfirmDialog
        ask={
          forgetting
            ? {
                title: "Forget this answer?",
                sentence: `${forgetting.question}: the app no longer reuses this answer on other forms.`,
                confirm: "Forget answer",
              }
            : null
        }
        pending={save.isPending}
        onCancel={() => {
          setForgetting(null);
        }}
        onConfirm={() => {
          if (!forgetting) return;
          save.mutate({
            key: forgetting.question_key,
            answer: null,
            done: "Answer forgotten.",
          });
        }}
      />
    </Card>
  );
}

function AnswerForm({
  entry,
  pending,
  onCancel,
  onSave,
}: {
  entry: BankEntry;
  pending: boolean;
  onCancel: () => void;
  onSave: (answer: string) => void;
}) {
  const id = useId();
  const box = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState(answerText(entry.answer));
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    box.current?.focus();
  }, []);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (text.trim() === "") {
      setProblem("Type an answer, or use Forget to remove it.");
      box.current?.focus();
      return;
    }
    setProblem(null);
    onSave(text.trim());
  };

  return (
    <form
      noValidate
      aria-label={`Edit the answer to ${entry.question}`}
      className="flex flex-col gap-2"
      onSubmit={onSubmit}
    >
      <Label htmlFor={id}>Your answer</Label>
      <Textarea
        ref={box}
        id={id}
        value={text}
        maxLength={ANSWER_MAX}
        aria-invalid={problem ? true : undefined}
        aria-describedby={problem ? `${id}-problem` : undefined}
        onChange={(event) => {
          setText(event.target.value);
        }}
      />
      {problem ? (
        <p id={`${id}-problem`} className="text-sm text-destructive">
          {problem}
        </p>
      ) : null}
      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button
          type="button"
          variant="outline"
          disabled={pending}
          onClick={onCancel}
        >
          Cancel
        </Button>
        <Button type="submit" disabled={pending} aria-busy={pending}>
          {pending ? (
            <LoaderCircle aria-hidden="true" className="animate-spin" />
          ) : null}
          Save answer
        </Button>
      </div>
    </form>
  );
}
