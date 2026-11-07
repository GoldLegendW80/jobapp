import { CircleAlert } from "lucide-react";
import { useId, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { toast } from "sonner";

import { LoadError } from "@/components/blocks/load-error";
import { LoadingCard } from "@/components/blocks/loading-card";
import { PageBody } from "@/components/blocks/page";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldError,
  FieldLabel,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { message } from "@/features/admin/helpers/errors";
import {
  SettingsFrame,
  SettingsSection,
} from "@/components/shared/profile/settings-frame";
import { useTitle } from "@/hooks/use-title";

import { AnswerBank } from "../components/answer-bank";
import { CapsEditor } from "../components/caps-editor";
import { FullAutoSection } from "../components/full-auto-settings";
import { readFit, type ApplySettings } from "../api/settings-api";
import {
  useApplySettings,
  useSaveApplySettings,
} from "../api/use-apply-settings";

/**
 * /settings/applications, Settings, Applications (SPEC-apply.md, "Frontend", Settings): what
 * Apply does (the review box, issue #738), the mode after a Like (Review first or Full auto, with
 * its Advanced hold length, full-auto-settings.tsx), the pause switch, the three fit thresholds
 * side by side (only "Apply automatically from" is changed here), the company caps and the saved
 * answers.
 */
export function ApplicationsSettingsPage() {
  useTitle("Application settings");
  const query = useApplySettings();

  return (
    <SettingsFrame>
      <PageBody>
        {query.isPending ? (
          <LoadingCard>Loading your settings</LoadingCard>
        ) : query.isError ? (
          <LoadError query={query}>
            Your settings could not be loaded.
          </LoadError>
        ) : (
          <>
            <ReviewBeforeSendSection settings={query.data} />
            <FullAutoSection settings={query.data} />
            <PauseSection settings={query.data} />
            <ThresholdsSection settings={query.data} />
          </>
        )}
        <CapsEditor />
        <AnswerBank />
      </PageBody>
    </SettingsFrame>
  );
}

function Problem({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <Alert variant="destructive">
      <CircleAlert aria-hidden="true" />
      <AlertDescription>{text}</AlertDescription>
    </Alert>
  );
}

/**
 * "Let me review each filled form before it is sent" (issue #738), unticked by default: the job
 * page's Apply then fills the form and sends it; ticked, the fill stops for Approve and submit.
 * Likes follow "After a Like" below, not this box.
 */
function ReviewBeforeSendSection({ settings }: { settings: ApplySettings }) {
  const id = useId();
  const save = useSaveApplySettings();
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <SettingsSection
      title="When you click Apply"
      description="Whether you check each filled form before it is sent."
    >
      <Problem text={problem} />
      <Field orientation="horizontal" className="gap-2">
        <Checkbox
          id={`${id}-box`}
          checked={settings.review_before_send}
          disabled={save.isPending}
          aria-describedby={`${id}-text`}
          onCheckedChange={(checked) => {
            const review = checked === true;
            setProblem(null);
            save.mutate(
              { review_before_send: review },
              {
                onSuccess: () => {
                  toast.success(
                    review
                      ? "You check each filled form before it is sent."
                      : "Apply now fills the form and sends it for you.",
                  );
                },
                onError: (error) => {
                  setProblem(message(error));
                },
              },
            );
          }}
        />
        <FieldContent className="gap-0.5">
          <FieldLabel htmlFor={`${id}-box`}>
            Let me review each filled form before it is sent
          </FieldLabel>
          <FieldDescription id={`${id}-text`}>
            When it is off, Apply fills the form and sends it for you. When it
            is on, you check each filled form and send it yourself.
          </FieldDescription>
        </FieldContent>
      </Field>
    </SettingsSection>
  );
}

function PauseSection({ settings }: { settings: ApplySettings }) {
  const id = useId();
  const save = useSaveApplySettings();
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <SettingsSection
      title="Pause"
      description="One switch that stops every automatic application."
    >
      <Problem text={problem} />
      <Field orientation="horizontal" className="gap-2">
        <Switch
          id={`${id}-switch`}
          checked={settings.paused}
          disabled={save.isPending}
          aria-describedby={`${id}-text`}
          onCheckedChange={(paused) => {
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
        <FieldContent className="gap-0.5">
          <FieldLabel htmlFor={`${id}-switch`}>
            Pause automatic applications
          </FieldLabel>
          <FieldDescription id={`${id}-text`}>
            {settings.paused
              ? "Paused. The app submits nothing for you until you turn this off; your filled forms wait for you."
              : "While paused, the app submits nothing for you: an approval is refused and the form waits for you."}
          </FieldDescription>
        </FieldContent>
      </Field>
    </SettingsSection>
  );
}

/**
 * The three fit thresholds side by side: the two set elsewhere, read only with the way to them,
 * and "Apply automatically from", changed here.
 */
function ThresholdsSection({ settings }: { settings: ApplySettings }) {
  return (
    <SettingsSection
      title="Fit thresholds"
      description="Three fit scores, from 0 to 100: what the app shows you, writes documents for, and applies to by itself."
      className="@container max-w-none"
    >
      <div className="grid gap-6 @3xl:grid-cols-3">
        <Threshold title="Show postings from">
          <Score value={settings.show_postings_from} />
          <p className="text-sm text-muted-foreground">
            Postings are listed from this fit score. You set it in Settings,
            Search, as Minimum fit.
          </p>
          <ThresholdLink to="/settings/search">
            Open Settings, Search
          </ThresholdLink>
        </Threshold>
        <Threshold title="Write documents from">
          <Score value={settings.write_documents_from} />
          <p className="text-sm text-muted-foreground">
            The app writes a CV and a letter for postings from this fit score.
            It is a documents setting, read only here.
          </p>
          <ThresholdLink to="/settings/search">
            Open the documents settings
          </ThresholdLink>
        </Threshold>
        <MinFit settings={settings} />
      </div>
    </SettingsSection>
  );
}

function Threshold({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex min-w-0 flex-col gap-2">
      <h3 id={id} className="text-sm font-medium">
        {title}
      </h3>
      {children}
    </section>
  );
}

function Score({ value }: { value: number | null }) {
  return (
    <p className="text-2xl font-medium tracking-tight tabular-nums">
      {value ?? "Not set"}
    </p>
  );
}

function ThresholdLink({ to, children }: { to: string; children: string }) {
  return (
    <Link
      to={to}
      className="w-fit text-sm font-medium underline underline-offset-4"
    >
      {children}
    </Link>
  );
}

function MinFit({ settings }: { settings: ApplySettings }) {
  const id = useId();
  const save = useSaveApplySettings();
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
    <Threshold title="Apply automatically from">
      <form
        noValidate
        aria-label="Apply automatically from"
        className="flex flex-col gap-2"
        onSubmit={onSubmit}
      >
        <Problem text={failed} />
        <Field className="gap-2">
          <FieldLabel htmlFor={`${id}-input`} className="sr-only">
            Fit score, 0 to 100
          </FieldLabel>
          <div className="flex flex-wrap gap-2">
            <Input
              id={`${id}-input`}
              inputMode="numeric"
              maxLength={3}
              className="w-20"
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
                <Spinner aria-hidden="true" role={undefined} />
              ) : null}
              Save threshold
            </Button>
          </div>
          {problem ? (
            <FieldError id={`${id}-problem`}>{problem}</FieldError>
          ) : null}
        </Field>
      </form>
      <p className="text-sm text-muted-foreground">
        Full auto applies only to postings from this fit score.
        {settings.min_fit_chosen
          ? null
          : ` You have not chosen one yet, so ${settings.min_fit} applies.`}
      </p>
    </Threshold>
  );
}
