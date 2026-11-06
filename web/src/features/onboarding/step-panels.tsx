import { TriangleAlert } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router";

import { AdvancedSection } from "@/components/advanced-section";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { holdText, type ApplySettings } from "@/features/apply/settings-api";
import { UploadPanel } from "@/features/documents/upload-panel";
import { RunProgress } from "@/features/ingestion/run-progress";
import { RunStart } from "@/features/ingestion/run-start";
import { StepDocuments } from "@/features/ingestion/step-documents";
import { SourcesEditor } from "@/features/jobs/sources-editor";
import type { Sources } from "@/features/jobs/use-sources";
import {
  COMPANY_TYPES,
  CONTRACT_TYPES,
  CURRENCIES,
  DOCUMENTS_LANGUAGES,
  DOCUMENTS_TONES,
  SENIORITIES,
  type Search,
} from "@/features/profile/api";
import {
  PlaceSuggestion,
  TitleSuggestions,
} from "@/features/profile/title-suggestions";
import { cn } from "@/lib/utils";
import { contractWord, WORDING } from "@/lib/wording";

import { ModelKeyForm } from "./model-key-form";
import {
  readPay,
  splitTerms,
  STEP_IDS,
  STEP_NAMES,
  type Draft,
  type PayDraft,
  type StepId,
} from "./steps";
import {
  COMPANY_WORDS,
  LANGUAGE_WORDS,
  REMOTE_WORDS,
  SENIORITY_WORDS,
  summaryRows,
  sweepCoverage,
  TONE_WORDS,
} from "./words";

export interface PanelProps {
  draft: Draft;
  change: (patch: Partial<Draft>) => void;
  patterns?: { roles: boolean; places: boolean };
  problem?: string | null;
}

function toggle<T>(list: readonly T[], item: T, on: boolean): T[] {
  return on
    ? [...list.filter((x) => x !== item), item]
    : list.filter((x) => x !== item);
}

/** A field's label as tight as the plain Label it replaces, so the field keeps its height. */
const TIGHT = "leading-none";

function TextAnswer({
  label,
  hint,
  value,
  onChange,
  readOnly = false,
  fieldId,
}: {
  fieldId?: string;
  readOnly?: boolean;
  label: string;
  hint: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const ownId = useId();
  const id = fieldId ?? ownId;
  return (
    <Field className="gap-1.5">
      <FieldLabel htmlFor={id} className={TIGHT}>
        {label}
      </FieldLabel>
      <Input
        id={id}
        value={value}
        readOnly={readOnly}
        maxLength={2000}
        aria-describedby={`${id}-hint`}
        onChange={(event) => {
          onChange(event.target.value);
        }}
      />
      <FieldDescription id={`${id}-hint`} className="text-xs">
        {hint}
      </FieldDescription>
    </Field>
  );
}

/** One choice of a card: the whole box is the label, and it is marked while checked. */
const CHOICE =
  "min-h-11 w-full items-center rounded-md border px-3 font-normal leading-snug";

/** The card around a group of choices; its legend sits on the border. */
const CHOICE_CARD = "gap-2 rounded-md border p-4";

/** A mode of the Apply step: a card with a title and a line under it. */
const MODE =
  "min-h-11 w-full items-start rounded-md border px-3 py-2 font-normal";

/** A box alone on its row: the whole row is the label, a tap target of 44 px. */
const ONE_BOX = "min-h-11 font-normal leading-snug";

function Choices<T extends string>({
  legend,
  options,
  words,
  chosen,
  onChange,
}: {
  legend: string;
  options: readonly T[];
  words: (value: T) => string;
  chosen: readonly T[];
  onChange: (next: T[]) => void;
}) {
  const id = useId();
  return (
    <FieldSet className={CHOICE_CARD} data-simple-card>
      <FieldLegend variant="label" className="mb-1">
        {legend}
      </FieldLegend>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {options.map((option) => (
          <FieldLabel
            key={option}
            htmlFor={`${id}-${option}`}
            className={CHOICE}
          >
            <Checkbox
              id={`${id}-${option}`}
              checked={chosen.includes(option)}
              onCheckedChange={(checked) => {
                onChange(toggle(chosen, option, checked === true));
              }}
            />
            {words(option)}
          </FieldLabel>
        ))}
      </div>
    </FieldSet>
  );
}

function Radios<T extends string>({
  legend,
  options,
  words,
  value,
  onChange,
}: {
  legend: string;
  options: readonly T[];
  words: (value: T) => string;
  value: T | null;
  onChange: (next: T) => void;
}) {
  const id = useId();
  return (
    <FieldSet className={CHOICE_CARD} data-simple-card>
      <FieldLegend variant="label" className="mb-1">
        {legend}
      </FieldLegend>
      <RadioGroup
        name={id}
        value={value ?? ""}
        className="grid-cols-1 gap-2 sm:grid-cols-2"
        onValueChange={(next) => {
          const option = options.find((o) => o === next);
          if (option !== undefined) onChange(option);
        }}
      >
        {options.map((option) => (
          <FieldLabel
            key={option}
            htmlFor={`${id}-${option}`}
            className={CHOICE}
          >
            <RadioGroupItem id={`${id}-${option}`} value={option} />
            {words(option)}
          </FieldLabel>
        ))}
      </RadioGroup>
    </FieldSet>
  );
}

// --- 1. Your profile ----------------------------------------------------------------------------

export function StepProfile() {
  // The run part asks the api only once the person opens the disclosure.
  return (
    <div className="flex flex-col gap-6">
      <p className="text-sm text-muted-foreground">
        Upload your CV: the app keeps it as your base CV, and can fill your
        profile from it. You review every proposal before it is written. The
        reading goes on while you answer the next steps.
      </p>
      <UploadPanel openHref={(id) => `/profile/cv/${id}`} />
      <p className="text-sm text-muted-foreground">
        {WORDING.importOtherProfile}
      </p>
      <AdvancedSection pageKey="welcome-profile">
        <div className="flex flex-col gap-6 pt-4">
          <StepDocuments />
          <section
            aria-labelledby="welcome-read"
            className="flex flex-col gap-4"
          >
            <div className="flex flex-col gap-1">
              <h3
                id="welcome-read"
                className="text-base font-semibold tracking-tight"
              >
                {WORDING.readProfile}
              </h3>
              <p className="text-sm text-muted-foreground">
                Choose the documents, see the estimate, and start. The reading
                goes on while you answer the next steps.
              </p>
            </div>
            <RunStart />
            <RunProgress />
          </section>
        </div>
      </AdvancedSection>
      <p className="text-sm">
        <Link
          className="underline underline-offset-4"
          to="/profile/fill?step=review"
        >
          {WORDING.reviewProposals}
        </Link>{" "}
        <span className="text-muted-foreground">
          (opens Fill my profile once the reading is done; come back to this
          page after)
        </span>
      </p>
    </div>
  );
}

// --- 2. What you are looking for ----------------------------------------------------------------

export function StepLooking({ draft, change, patterns, problem }: PanelProps) {
  const rolesId = useId();
  const roles = (
    <TextAnswer
      fieldId={rolesId}
      label={WORDING.roles}
      hint="Separate them with commas, for example Data engineer, Analytics engineer."
      value={draft.roles}
      onChange={(roles) => {
        change({ roles });
      }}
      readOnly={patterns?.roles}
    />
  );
  return (
    <div className="flex flex-col gap-6">
      {patterns?.roles ? (
        <p className="text-sm text-muted-foreground">
          {WORDING.savedPatternHint}
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          {roles}
          {/* The job titles agent's ideas, once there is a key and something to read. */}
          <TitleSuggestions
            quiet
            label={WORDING.roles}
            current={splitTerms(draft.roles)}
            onAdd={(title) => {
              change({ roles: [...splitTerms(draft.roles), title].join(", ") });
            }}
          />
        </div>
      )}
      <AdvancedSection
        pageKey="welcome-looking"
        errorFieldId={
          patterns?.roles && problem && !draft.roles.trim()
            ? rolesId
            : undefined
        }
      >
        {patterns?.roles ? (
          <>
            {roles}
            <Link className="text-sm underline" to="/settings/search">
              {WORDING.searchOptions}
            </Link>
          </>
        ) : null}
        <Choices
          legend={WORDING.seniority}
          options={SENIORITIES}
          words={(value) => SENIORITY_WORDS[value]}
          chosen={draft.seniority}
          onChange={(seniority) => {
            change({ seniority });
          }}
        />
        <TextAnswer
          label={WORDING.industriesOptional}
          hint="For example Healthcare, Energy. They raise the fit of postings that name them, and hide none."
          value={draft.industries}
          onChange={(industries) => {
            change({ industries });
          }}
        />
        <Choices
          legend={WORDING.companyTypesOptional}
          options={COMPANY_TYPES}
          words={(value) => COMPANY_WORDS[value]}
          chosen={draft.companyTypes}
          onChange={(companyTypes) => {
            change({ companyTypes });
          }}
        />
      </AdvancedSection>
    </div>
  );
}

// --- 3. Where -----------------------------------------------------------------------------------

export function StepWhere({ draft, change, patterns, problem }: PanelProps) {
  const id = useId();
  const places = (
    <TextAnswer
      fieldId={`${id}-places`}
      label={WORDING.places}
      hint="Cities, regions or countries, separated by commas, for example Lyon, Remote Europe."
      value={draft.locations}
      readOnly={patterns?.places}
      onChange={(locations) => {
        change({ locations });
      }}
    />
  );
  return (
    <div className="flex flex-col gap-6">
      <Label htmlFor={id} className={ONE_BOX}>
        <Checkbox
          id={id}
          checked={draft.anywhere}
          onCheckedChange={(checked) => {
            change({ anywhere: checked === true });
          }}
        />
        {WORDING.anywhere}
      </Label>
      {draft.anywhere ? null : patterns?.places ? (
        <AdvancedSection
          pageKey="welcome-where"
          errorFieldId={
            problem && !draft.locations.trim() ? `${id}-places` : undefined
          }
        >
          {places}
          <Link className="text-sm underline" to="/settings/search">
            {WORDING.searchOptions}
          </Link>
        </AdvancedSection>
      ) : (
        <div className="flex flex-col gap-3">
          {places}
          <PlaceSuggestion
            label={WORDING.places}
            current={splitTerms(draft.locations)}
            onAdd={(place) => {
              change({
                locations: [...splitTerms(draft.locations), place].join(", "),
              });
            }}
          />
        </div>
      )}
      <Radios
        legend={WORDING.remoteHybridOnSite}
        options={["remote", "hybrid", "office", "any"] as const}
        words={(value) => REMOTE_WORDS[value]}
        value={draft.remote}
        onChange={(remote) => {
          change({ remote });
        }}
      />
    </div>
  );
}

// --- 5. How you want to work --------------------------------------------------------------------

/** The contracts, one group whose last box is Freelance mission; a code this build does not
 * offer stays saved as it was. */
const CONTRACT_ORDER: readonly string[] = [
  ...CONTRACT_TYPES.filter((type) => type !== "Freelance"),
  "Freelance",
];

export function StepWork({ draft, change }: PanelProps) {
  return (
    <div className="flex flex-col gap-4">
      <Choices
        legend={WORDING.contractsChooseAny}
        options={CONTRACT_ORDER}
        words={contractWord}
        chosen={draft.contracts.filter((type) => CONTRACT_ORDER.includes(type))}
        onChange={(chosen) => {
          change({
            contracts: [
              ...draft.contracts.filter(
                (type) => !CONTRACT_ORDER.includes(type),
              ),
              ...chosen,
            ],
          });
        }}
      />
    </div>
  );
}

// --- 6. Pay -------------------------------------------------------------------------------------

function PayFields({
  id,
  invalid,
  legend,
  pay,
  onChange,
}: {
  id: string;
  invalid: boolean;
  legend: string;
  pay: PayDraft;
  onChange: (pay: PayDraft) => void;
}) {
  return (
    <FieldSet className="gap-2">
      <FieldLegend variant="label" className="mb-1">
        {legend}
      </FieldLegend>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <Field className="gap-1.5">
          <FieldLabel htmlFor={`${id}-min`} className={TIGHT}>
            {WORDING.payFrom}
          </FieldLabel>
          <Input
            id={`${id}-min`}
            aria-invalid={invalid}
            aria-describedby={invalid ? "welcome-problem" : undefined}
            inputMode="numeric"
            value={pay.min}
            onChange={(event) => {
              onChange({ ...pay, min: event.target.value });
            }}
          />
        </Field>
        <Field className="gap-1.5">
          <FieldLabel htmlFor={`${id}-max`} className={TIGHT}>
            {WORDING.payTo}
          </FieldLabel>
          <Input
            id={`${id}-max`}
            inputMode="numeric"
            value={pay.max}
            onChange={(event) => {
              onChange({ ...pay, max: event.target.value });
            }}
          />
        </Field>
        <Field className="col-span-2 gap-1.5 sm:col-span-1">
          <FieldLabel htmlFor={`${id}-currency`} className={TIGHT}>
            {WORDING.payCurrency}
          </FieldLabel>
          <NativeSelect
            id={`${id}-currency`}
            value={pay.currency}
            onChange={(event) => {
              onChange({ ...pay, currency: event.target.value });
            }}
          >
            {CURRENCIES.map((code) => (
              <option key={code} value={code}>
                {code}
              </option>
            ))}
          </NativeSelect>
        </Field>
      </div>
    </FieldSet>
  );
}

export function StepPay({ draft, change, problem }: PanelProps) {
  const id = useId();
  const [open, setOpen] = useState<boolean>();
  const error =
    problem && !draft.ratherNotSay
      ? "error" in readPay(draft.salary)
        ? "salary"
        : "error" in readPay(draft.daily)
          ? "daily"
          : null
      : null;
  if (error && open !== true) setOpen(true);
  return (
    <div className="flex flex-col gap-6">
      <p className="text-sm text-muted-foreground">
        Shown on the cards next to a posting&apos;s pay. It is never typed into
        an application form.
      </p>
      <Label htmlFor={id} className={ONE_BOX}>
        <Checkbox
          id={id}
          checked={draft.ratherNotSay}
          onCheckedChange={(checked) => {
            change({ ratherNotSay: checked === true });
          }}
        />
        {WORDING.ratherNotSay}
      </Label>
      {draft.ratherNotSay ? null : (
        <AdvancedSection
          pageKey="welcome-pay"
          open={open}
          onOpenChange={setOpen}
          errorFieldId={error ? `${id}-${error}-min` : undefined}
        >
          <PayFields
            id={`${id}-salary`}
            invalid={error === "salary"}
            legend={WORDING.yearlySalary}
            pay={draft.salary}
            onChange={(salary) => {
              change({ salary });
            }}
          />
          <PayFields
            id={`${id}-daily`}
            invalid={error === "daily"}
            legend={WORDING.dailyRateFreelance}
            pay={draft.daily}
            onChange={(daily) => {
              change({ daily });
            }}
          />
        </AdvancedSection>
      )}
    </div>
  );
}

// --- 7. How the app applies ---------------------------------------------------------------------

/**
 * Two choices, each kept in apply's own settings (A11, issue #738): the mode after a Like, and
 * "Let me review each filled form before it is sent", which a click on Apply follows. Full auto
 * is only shown here: it is turned on in Application settings, with its own checks.
 */
export function StepApply({
  draft,
  change,
  settings,
}: PanelProps & { settings: ApplySettings | undefined }) {
  const id = useId();
  const fullAutoOn =
    settings?.mode_after_like === "full_auto" && draft.mode === null;
  const hold = holdText(settings?.hold_s ?? 900);
  const review = draft.review ?? settings?.review_before_send ?? false;
  return (
    <div className="flex flex-col gap-6">
      <FieldSet
        className={cn(CHOICE_CARD, "has-[>[data-slot=radio-group]]:gap-2")}
        data-simple-card
        aria-describedby={`${id}-like`}
      >
        <FieldLegend variant="label" className="mb-1">
          {WORDING.afterLike}
        </FieldLegend>
        <FieldDescription id={`${id}-like`} className="nth-last-2:mt-0">
          A Like on a posting in Discover asks the app to write your CV and
          letter for it and to prepare the application.
        </FieldDescription>
        <RadioGroup
          name={`${id}-mode`}
          value={fullAutoOn ? "full_auto" : "review"}
          className="gap-2"
          onValueChange={(mode) => {
            if (mode === "review") change({ mode: "review" });
          }}
        >
          <FieldLabel htmlFor={`${id}-review`} className={MODE}>
            <RadioGroupItem
              id={`${id}-review`}
              value="review"
              className="mt-0.5"
            />
            <span className="flex flex-col">
              <span className="font-medium">{WORDING.reviewFirst}</span>
              <span className="text-muted-foreground">
                You approve each application a Like prepares before it is sent.
              </span>
            </span>
          </FieldLabel>
          <FieldLabel
            htmlFor={`${id}-full`}
            className={cn(MODE, "border-dashed")}
          >
            <RadioGroupItem
              id={`${id}-full`}
              value="full_auto"
              className="mt-0.5"
              disabled
              aria-describedby={`${id}-full-text`}
            />
            <span className="flex flex-col">
              <span className="font-medium">{WORDING.fullAuto}</span>
              <span id={`${id}-full-text`} className="text-muted-foreground">
                A Like is enough. The app applies after a hold of at least{" "}
                {hold}, only above your threshold. It is turned on in{" "}
                <Link
                  className="font-medium text-foreground underline underline-offset-4"
                  to="/settings/applications"
                >
                  {WORDING.applicationSettings}
                </Link>
                , with its own checks.
              </span>
            </span>
          </FieldLabel>
        </RadioGroup>
      </FieldSet>
      <FieldSet className={CHOICE_CARD} data-simple-card>
        <FieldLegend variant="label" className="mb-1">
          {WORDING.whenYouClickApply}
        </FieldLegend>
        <Field
          orientation="horizontal"
          className="min-h-11 gap-2 rounded-md border px-3 py-2"
        >
          <Checkbox
            id={`${id}-send`}
            className="mt-0.5"
            checked={review}
            aria-describedby={`${id}-send-text`}
            onCheckedChange={(checked) => {
              change({ review: checked === true });
            }}
          />
          <FieldContent className="gap-0">
            <FieldLabel htmlFor={`${id}-send`}>
              {WORDING.reviewEachForm}
            </FieldLabel>
            <FieldDescription id={`${id}-send-text`}>
              When it is off, Apply fills the form and sends it for you. When it
              is on, you check each filled form and send it yourself.
            </FieldDescription>
          </FieldContent>
        </Field>
      </FieldSet>
      <Radios
        legend={WORDING.documentsLanguage}
        options={DOCUMENTS_LANGUAGES}
        words={(value) => LANGUAGE_WORDS[value]}
        value={draft.language}
        onChange={(language) => {
          change({ language });
        }}
      />
      <Radios
        legend={WORDING.tone}
        options={DOCUMENTS_TONES}
        words={(value) => TONE_WORDS[value]}
        value={draft.tone}
        onChange={(tone) => {
          change({ tone });
        }}
      />
    </div>
  );
}

// --- 4. Websites to search ---------------------------------------------------------------------

export function StepSites({
  sources,
  reviewed,
}: {
  sources: Sources;
  reviewed: Search;
}) {
  return (
    <div className="flex flex-col gap-4">
      <p className="text-sm text-muted-foreground">
        Tell the app where to look for postings: add a few websites now. You can
        change the list any time from Jobs, Find jobs.
      </p>
      <SourcesEditor sources={sources} reviewed={reviewed} showTitle={false} />
    </div>
  );
}

// --- 8. Summary ---------------------------------------------------------------------------------

export function StepSummary({
  draft,
  settings,
  search,
  onChange,
}: {
  draft: Draft;
  settings: ApplySettings | undefined;
  search: Search;
  onChange: (step: StepId) => void;
}) {
  const rows = summaryRows(draft, settings, search);
  const places = sweepCoverage(search);
  return (
    <div className="flex flex-col gap-4">
      <dl className="flex flex-col gap-3">
        {STEP_IDS.filter((step) => step !== "summary").map((step) => (
          <div
            key={step}
            className="flex items-start justify-between gap-3 rounded-md border px-3 py-2"
            data-simple-card
          >
            <div className="flex min-w-0 flex-col">
              <dt className="text-sm font-medium">{STEP_NAMES[step]}</dt>
              <dd className="text-sm break-words text-muted-foreground">
                {rows[step]}
              </dd>
            </div>
            <Button
              variant="outline"
              size="sm"
              aria-label={`${WORDING.change} ${STEP_NAMES[step]}`}
              onClick={() => {
                onChange(step);
              }}
            >
              {WORDING.change}
            </Button>
          </div>
        ))}
      </dl>
      {places ? (
        <p className="text-sm text-muted-foreground">
          Start runs your first search on{" "}
          {places === 1 ? "1 place" : `${String(places)} places`} from your
          list.
        </p>
      ) : (
        <Alert>
          <TriangleAlert aria-hidden="true" />
          <AlertDescription>
            <p>
              Your list of websites is empty, so the first search has nowhere to
              look and finds no postings.
            </p>
            <Button
              size="sm"
              variant="outline"
              className="mt-2"
              onClick={() => {
                onChange("sites");
              }}
            >
              {WORDING.addWebsites}
            </Button>
          </AlertDescription>
        </Alert>
      )}
      <ModelKeyForm />
    </div>
  );
}
