import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, LoaderCircle } from "lucide-react";
import { useId, useState, type FormEvent } from "react";
import { Link } from "react-router";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { message } from "@/features/admin/errors";
import { useTitle } from "@/lib/use-title";

import { AnswerBank } from "./answer-bank";
import { CapsEditor } from "./caps-editor";
import {
  applySettingsKey,
  getApplySettings,
  holdText,
  readFit,
  saveApplySettings,
  type ApplySettings,
} from "./settings-api";

/**
 * /settings/applications, "Applications" (SPEC-apply.md, "Frontend", Settings): the mode after a
 * Like (Review first; Full auto shown, not offered yet), the pause switch, the three fit
 * thresholds labelled apart (only "Apply automatically from" is changed here), the Advanced
 * values read only, the company caps and the saved answers.
 */
export function ApplicationsSettingsPage() {
  useTitle("Applications");
  const query = useQuery({
    queryKey: applySettingsKey,
    queryFn: ({ signal }) => getApplySettings(signal),
  });

  return (
    <div className="mx-auto flex max-w-xl flex-col gap-8">
      <header className="flex flex-col gap-1">
        <p className="text-sm font-medium text-muted-foreground">Settings</p>
        <h1 className="text-2xl font-semibold tracking-tight">Applications</h1>
        <p className="text-muted-foreground">
          How the app applies for you: the mode, the pause switch, your fit
          thresholds, your company caps and your saved answers.
        </p>
      </header>
      {query.isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading your settings
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
      ) : (
        <>
          <ModeCard settings={query.data} />
          <PauseCard settings={query.data} />
          <ThresholdCard
            id="show-postings"
            title="Show postings from"
            value={query.data.show_postings_from}
            sentence="Postings are listed from this fit score. You set it on your profile, Search, as Minimum fit."
            link={{ to: "/profile?tab=search", text: "Open Profile, Search" }}
          />
          <ThresholdCard
            id="write-documents"
            title="Write documents from"
            value={query.data.write_documents_from}
            sentence="The app writes a CV and a letter for postings from this fit score. It is a documents setting, read only here."
          />
          <MinFitCard settings={query.data} />
          <AdvancedCard settings={query.data} />
        </>
      )}
      <CapsEditor />
      <AnswerBank />
    </div>
  );
}

function useSaveSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: saveApplySettings,
    onSuccess: (saved) => {
      client.setQueryData<ApplySettings>(applySettingsKey, saved);
    },
  });
}

function ModeCard({ settings }: { settings: ApplySettings }) {
  const id = useId();
  return (
    <Card aria-labelledby={`${id}-title`}>
      <CardHeader>
        <CardTitle>
          <h2 id={`${id}-title`} className="text-lg">
            After a Like
          </h2>
        </CardTitle>
        <CardDescription>
          What the app does with a posting you like.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <fieldset className="flex flex-col gap-3">
          <legend className="sr-only">Mode after a Like</legend>
          <div className="flex items-start gap-2">
            <input
              id={`${id}-review`}
              type="radio"
              name={`${id}-mode`}
              className="mt-0.5 size-4 shrink-0 accent-primary"
              checked={settings.mode_after_like === "review"}
              readOnly
              aria-describedby={`${id}-review-text`}
            />
            <div className="flex flex-col gap-0.5">
              <Label htmlFor={`${id}-review`}>Review first</Label>
              <p
                id={`${id}-review-text`}
                className="text-sm text-muted-foreground"
              >
                The app fills the form and waits for your approval before it
                submits.
              </p>
            </div>
          </div>
          <div className="flex items-start gap-2">
            <input
              id={`${id}-full`}
              type="radio"
              name={`${id}-mode`}
              className="mt-0.5 size-4 shrink-0 accent-primary"
              checked={settings.mode_after_like === "full_auto"}
              disabled
              readOnly
              aria-describedby={`${id}-full-text`}
            />
            <div className="flex flex-col gap-0.5">
              <Label htmlFor={`${id}-full`} className="opacity-60">
                Full auto
              </Label>
              <p
                id={`${id}-full-text`}
                className="text-sm text-muted-foreground"
              >
                Not available yet.
              </p>
            </div>
          </div>
        </fieldset>
      </CardContent>
    </Card>
  );
}

function PauseCard({ settings }: { settings: ApplySettings }) {
  const id = useId();
  const save = useSaveSettings();
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <Card aria-labelledby={`${id}-title`}>
      <CardHeader>
        <CardTitle>
          <h2 id={`${id}-title`} className="text-lg">
            Pause
          </h2>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {problem ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>{problem}</AlertDescription>
          </Alert>
        ) : null}
        <div className="flex items-start gap-2">
          <input
            id={`${id}-switch`}
            type="checkbox"
            role="switch"
            className="mt-0.5 size-4 shrink-0 accent-primary"
            checked={settings.paused}
            disabled={save.isPending}
            aria-describedby={`${id}-text`}
            onChange={(event) => {
              const paused = event.target.checked;
              setProblem(null);
              save.mutate(
                { paused },
                {
                  onSuccess: () => {
                    toast.success(
                      paused
                        ? "Automatic applications paused."
                        : "Automatic applications back on.",
                    );
                  },
                  onError: (error) => {
                    setProblem(message(error));
                  },
                },
              );
            }}
          />
          <div className="flex flex-col gap-0.5">
            <Label htmlFor={`${id}-switch`}>Pause automatic applications</Label>
            <p id={`${id}-text`} className="text-sm text-muted-foreground">
              {settings.paused
                ? "Paused. The app submits nothing for you until you turn this off; your filled forms wait for you."
                : "While paused, the app submits nothing for you: an approval is refused and the form waits for you."}
            </p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function ThresholdCard({
  id,
  title,
  value,
  sentence,
  link,
}: {
  id: string;
  title: string;
  value: number | null;
  sentence: string;
  link?: { to: string; text: string };
}) {
  return (
    <Card aria-labelledby={`${id}-title`}>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id={`${id}-title`} className="text-lg">
            {title}
          </h2>
          <span className="text-lg font-semibold tabular-nums">
            {value ?? "Not set"}
          </span>
        </CardTitle>
        <CardDescription>{sentence}</CardDescription>
      </CardHeader>
      {link ? (
        <CardContent>
          <Link
            to={link.to}
            className="text-sm font-medium underline underline-offset-4"
          >
            {link.text}
          </Link>
        </CardContent>
      ) : null}
    </Card>
  );
}

function MinFitCard({ settings }: { settings: ApplySettings }) {
  const id = useId();
  const save = useSaveSettings();
  const [typed, setTyped] = useState(String(settings.min_fit));
  const [problem, setProblem] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const minFit = readFit(typed);
    if (minFit === null) {
      setProblem("Type a whole number from 0 to 100.");
      document.getElementById(`${id}-input`)?.focus();
      return;
    }
    setProblem(null);
    setFailed(null);
    save.mutate(
      { min_fit: minFit },
      {
        onSuccess: (saved) => {
          setTyped(String(saved.min_fit));
          toast.success("Threshold saved.");
        },
        onError: (error) => {
          setFailed(message(error));
        },
      },
    );
  };
  const unchanged =
    settings.min_fit_chosen && readFit(typed) === settings.min_fit;
  return (
    <Card aria-labelledby={`${id}-title`}>
      <CardHeader>
        <CardTitle>
          <h2 id={`${id}-title`} className="text-lg">
            Apply automatically from
          </h2>
        </CardTitle>
        <CardDescription>
          Full auto applies only to postings from this fit score.
          {settings.min_fit_chosen
            ? null
            : ` You have not chosen one yet, so ${settings.min_fit} applies.`}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          noValidate
          aria-label="Apply automatically from"
          className="flex flex-col gap-3"
          onSubmit={onSubmit}
        >
          {failed ? (
            <Alert variant="destructive">
              <CircleAlert aria-hidden="true" />
              <AlertDescription>{failed}</AlertDescription>
            </Alert>
          ) : null}
          <div className="grid gap-2">
            <Label htmlFor={`${id}-input`}>Fit score, 0 to 100</Label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <Input
                id={`${id}-input`}
                inputMode="numeric"
                maxLength={3}
                className="sm:w-28"
                value={typed}
                aria-invalid={problem ? true : undefined}
                aria-describedby={problem ? `${id}-problem` : undefined}
                onChange={(event) => {
                  setTyped(event.target.value);
                }}
              />
              <Button
                type="submit"
                disabled={save.isPending || unchanged}
                aria-busy={save.isPending}
              >
                {save.isPending ? (
                  <LoaderCircle aria-hidden="true" className="animate-spin" />
                ) : null}
                Save threshold
              </Button>
            </div>
            {problem ? (
              <p id={`${id}-problem`} className="text-sm text-destructive">
                {problem}
              </p>
            ) : null}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function AdvancedCard({ settings }: { settings: ApplySettings }) {
  return (
    <Card>
      <CardContent>
        <details className="group">
          <summary className="cursor-pointer rounded-md text-lg font-semibold">
            Advanced
          </summary>
          <dl className="mt-4 grid gap-3 text-sm">
            <div className="flex flex-col gap-0.5">
              <dt className="font-medium">Safety ceiling</dt>
              <dd className="text-muted-foreground">
                At most {settings.safety_ceiling} submissions a day in Full
                auto.
              </dd>
            </div>
            <div className="flex flex-col gap-0.5">
              <dt className="font-medium">Hold length</dt>
              <dd className="text-muted-foreground">
                {holdText(settings.hold_s)} between a Full auto decision and its
                submission, so you can cancel it.
              </dd>
            </div>
          </dl>
          <p className="mt-3 text-sm text-muted-foreground">
            Both are read only for now.
          </p>
        </details>
      </CardContent>
    </Card>
  );
}
