import { CircleAlert, Plus } from "lucide-react";
import {
  Fragment,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemSeparator,
  ItemTitle,
} from "@/components/ui/item";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { message } from "@/features/admin/helpers/errors";
import { errorDetail, isApiError } from "@/lib/api";

import { LoadError } from "@/components/blocks/load-error";
import { ConfirmDialog } from "@/components/shared/apply/confirm-dialog";
import { SettingsSection } from "@/components/shared/profile/settings-frame";
import {
  ANSWER_MAX,
  QUESTION_MAX,
  answerText,
  type BankEntry,
} from "../api/settings-api";
import {
  useAddBankAnswer,
  useAnswerBank,
  useSaveBankAnswer,
} from "../api/use-answer-bank";

type Change = { key: string; answer: string | null; done: string };

/**
 * "Saved answers": the person's answer bank, the answers reused when the same question comes
 * back on another form. Each can be changed or forgotten (PUT /api/apply/answer-bank, one entry
 * at a time). An answer already forgotten elsewhere answers 404: the list reloads and says so.
 */
export function AnswerBank() {
  const query = useAnswerBank();
  const [editing, setEditing] = useState<string | null>(null);
  const [forgetting, setForgetting] = useState<BankEntry | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const addButton = useRef<HTMLButtonElement>(null);
  const wasAdding = useRef(false);
  const create = useAddBankAnswer({
    onMutate: () => {
      setNotice(null);
    },
    onSuccess: () => {
      setAdding(false);
      toast.success("Reusable answer saved.");
    },
    onError: (error) => {
      setNotice(message(error));
    },
  });
  useEffect(() => {
    if (wasAdding.current && !adding && !create.isPending)
      addButton.current?.focus();
    if (!create.isPending) wasAdding.current = adding;
  }, [adding, create.isPending]);

  const save = useSaveBankAnswer<Change>({
    onMutate: () => {
      setNotice(null);
    },
    onSuccess: (_bank, change) => {
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
        return;
      }
      setNotice(message(error));
    },
  });

  return (
    <SettingsSection
      title="Saved answers"
      description="Answers the app reuses when the same question comes back on another form. Edits to an application stay on that application. Current profile answers take priority."
      actions={
        <Button
          ref={addButton}
          variant="outline"
          disabled={
            query.isPending ||
            query.isError ||
            save.isPending ||
            create.isPending ||
            adding
          }
          onClick={() => {
            setNotice(null);
            setEditing(null);
            setAdding(true);
          }}
        >
          <Plus aria-hidden="true" />
          Add answer
        </Button>
      }
    >
      {adding ? (
        <AddAnswerForm
          pending={create.isPending}
          onCancel={() => {
            setAdding(false);
          }}
          onSave={(question, answer) => {
            create.mutate({ question, answer });
          }}
        />
      ) : null}
      {notice ? (
        <Alert variant="destructive" role="alert">
          <CircleAlert aria-hidden="true" />
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      ) : null}
      {query.isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading your saved answers
        </p>
      ) : query.isError ? (
        <LoadError query={query}>
          Your saved answers could not be loaded.
        </LoadError>
      ) : query.data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No saved answer yet.</p>
      ) : (
        <ItemGroup aria-label="Your saved answers">
          {query.data.items.map((entry, index) => (
            <Fragment key={entry.question_key}>
              {index > 0 ? <ItemSeparator /> : null}
              <Item
                role="listitem"
                size="sm"
                className="flex-col items-stretch gap-2 rounded-none border-0 px-0"
              >
                <ItemTitle className="w-auto break-words">
                  {entry.question}
                </ItemTitle>
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
                    <ItemContent className="min-w-0 gap-0.5">
                      <span className="text-sm break-words whitespace-pre-wrap">
                        {answerText(entry.answer)}
                      </span>
                      <ItemDescription className="text-xs">
                        {entry.used_count === 1
                          ? "Used once"
                          : `Used ${entry.used_count} times`}
                      </ItemDescription>
                    </ItemContent>
                    <ItemActions className="shrink-0">
                      <Button
                        variant="outline"
                        size="sm"
                        aria-label={`Edit the answer to ${entry.question}`}
                        disabled={save.isPending || create.isPending || adding}
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
                        disabled={save.isPending || create.isPending || adding}
                        onClick={() => {
                          setForgetting(entry);
                        }}
                      >
                        Forget
                      </Button>
                    </ItemActions>
                  </div>
                )}
              </Item>
            </Fragment>
          ))}
        </ItemGroup>
      )}
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
    </SettingsSection>
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
      className="flex max-w-2xl flex-col gap-2"
      onSubmit={onSubmit}
    >
      <Field className="gap-2">
        <FieldLabel htmlFor={id}>Your answer</FieldLabel>
        <Textarea
          ref={box}
          id={id}
          value={text}
          maxLength={ANSWER_MAX}
          readOnly={pending}
          aria-invalid={problem ? true : undefined}
          aria-describedby={problem ? `${id}-problem` : undefined}
          onChange={(event) => {
            setText(event.target.value);
          }}
        />
        {problem ? (
          <FieldError id={`${id}-problem`}>{problem}</FieldError>
        ) : null}
      </Field>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={pending} aria-busy={pending}>
          {pending ? <Spinner aria-hidden="true" role={undefined} /> : null}
          Save answer
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={pending}
          onClick={onCancel}
        >
          Cancel
        </Button>
      </div>
    </form>
  );
}

function AddAnswerForm({
  pending,
  onCancel,
  onSave,
}: {
  pending: boolean;
  onCancel: () => void;
  onSave: (question: string, answer: string) => void;
}) {
  const id = useId();
  const questionBox = useRef<HTMLInputElement>(null);
  const answerBox = useRef<HTMLTextAreaElement>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [problem, setProblem] = useState<"question" | "answer" | null>(null);
  useEffect(() => {
    questionBox.current?.focus();
  }, []);
  return (
    <form
      aria-label="Add a saved answer"
      noValidate
      className="flex max-w-2xl flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (pending) return;
        const missing = !question.trim()
          ? "question"
          : !answer.trim()
            ? "answer"
            : null;
        setProblem(missing);
        if (missing) {
          (missing === "question"
            ? questionBox.current
            : answerBox.current
          )?.focus();
          return;
        }
        onSave(question.trim(), answer.trim());
      }}
    >
      <Field className="gap-2">
        <FieldLabel htmlFor={`${id}-question`}>Question</FieldLabel>
        <Input
          ref={questionBox}
          id={`${id}-question`}
          value={question}
          maxLength={QUESTION_MAX}
          readOnly={pending}
          aria-invalid={problem === "question" || undefined}
          aria-describedby={
            problem === "question" ? `${id}-problem` : undefined
          }
          onChange={(event) => {
            setQuestion(event.target.value);
          }}
        />
      </Field>
      <Field className="gap-2">
        <FieldLabel htmlFor={`${id}-answer`}>Reusable answer</FieldLabel>
        <Textarea
          ref={answerBox}
          id={`${id}-answer`}
          value={answer}
          maxLength={ANSWER_MAX}
          readOnly={pending}
          aria-invalid={problem === "answer" || undefined}
          aria-describedby={problem === "answer" ? `${id}-problem` : undefined}
          onChange={(event) => {
            setAnswer(event.target.value);
          }}
        />
      </Field>
      {problem ? (
        <FieldError id={`${id}-problem`}>
          {problem === "question"
            ? "Type the question as it appears on the form."
            : "Type an answer to reuse on other forms."}
        </FieldError>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={pending} aria-busy={pending}>
          {pending ? <Spinner aria-hidden="true" role={undefined} /> : null}
          Save new answer
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={pending}
          onClick={onCancel}
        >
          Cancel
        </Button>
      </div>
    </form>
  );
}
