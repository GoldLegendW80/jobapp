import { useId, useState, type ReactNode } from "react";
import { Link } from "react-router";

import { AdvancedSection } from "@/components/advanced-section";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { holdText, type ApplySettings } from "@/features/apply/settings-api";
import { UploadPanel } from "@/features/documents/upload-panel";
import { RunProgress } from "@/features/ingestion/run-progress";
import { RunStart } from "@/features/ingestion/run-start";
import { StepDocuments } from "@/features/ingestion/step-documents";
import {
  COMPANY_TYPES,
  CONTRACT_TYPES,
  CURRENCIES,
  DOCUMENTS_LANGUAGES,
  DOCUMENTS_TONES,
  SENIORITIES,
  type Search,
} from "@/features/profile/api";
import { contractWord, WORDING } from "@/lib/wording";

import {
  readPay,
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
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
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
      <p id={`${id}-hint`} className="text-xs text-muted-foreground">
        {hint}
      </p>
    </div>
  );
}

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
    <fieldset
      className="flex flex-col gap-2 rounded-md border p-4"
      data-simple-card
    >
      <legend className="mb-1 text-sm font-medium">{legend}</legend>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {options.map((option) => (
          <label
            key={option}
            htmlFor={`${id}-${option}`}
            className="flex min-h-11 items-center gap-2 rounded-md border px-3 text-sm"
          >
            <input
              id={`${id}-${option}`}
              type="checkbox"
              className="size-4 accent-primary"
              checked={chosen.includes(option)}
              onChange={(event) => {
                onChange(toggle(chosen, option, event.target.checked));
              }}
            />
            {words(option)}
          </label>
        ))}
      </div>
    </fieldset>
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
    <fieldset
      className="flex flex-col gap-2 rounded-md border p-4"
      data-simple-card
    >
      <legend className="mb-1 text-sm font-medium">{legend}</legend>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {options.map((option) => (
          <label
            key={option}
            htmlFor={`${id}-${option}`}
            className="flex min-h-11 items-center gap-2 rounded-md border px-3 text-sm"
          >
            <input
              id={`${id}-${option}`}
              type="radio"
              name={id}
              className="size-4 accent-primary"
              checked={value === option}
              onChange={() => {
                onChange(option);
              }}
            />
            {words(option)}
          </label>
        ))}
      </div>
    </fieldset>
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
        roles
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
            <Link className="text-sm underline" to="/profile?tab=search">
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
      <label htmlFor={id} className="flex min-h-11 items-center gap-2 text-sm">
        <input
          id={id}
          type="checkbox"
          className="size-4 accent-primary"
          checked={draft.anywhere}
          onChange={(event) => {
            change({ anywhere: event.target.checked });
          }}
        />
        {WORDING.anywhere}
      </label>
      {draft.anywhere ? null : patterns?.places ? (
        <AdvancedSection
          pageKey="welcome-where"
          errorFieldId={
            problem && !draft.locations.trim() ? `${id}-places` : undefined
          }
        >
          {places}
          <Link className="text-sm underline" to="/profile?tab=search">
            {WORDING.searchOptions}
          </Link>
        </AdvancedSection>
      ) : (
        places
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

// --- 4. How you want to work --------------------------------------------------------------------

export function StepWork({ draft, change }: PanelProps) {
  return (
    <div className="flex flex-col gap-4">
      {[
        CONTRACT_TYPES.filter((type) => type !== "Freelance"),
        ["Freelance"],
      ].map((options, index) => (
        <Choices
          key={index}
          legend={
            index === 0
              ? WORDING.employmentContracts
              : WORDING.freelanceContracts
          }
          options={options}
          words={contractWord}
          chosen={draft.contracts.filter((type) => options.includes(type))}
          onChange={(chosen) => {
            change({
              contracts: [
                ...draft.contracts.filter((type) => !options.includes(type)),
                ...chosen,
              ],
            });
          }}
        />
      ))}
    </div>
  );
}

// --- 5. Pay -------------------------------------------------------------------------------------

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
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1 text-sm font-medium">{legend}</legend>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-min`}>{WORDING.payFrom}</Label>
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
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={`${id}-max`}>{WORDING.payTo}</Label>
          <Input
            id={`${id}-max`}
            inputMode="numeric"
            value={pay.max}
            onChange={(event) => {
              onChange({ ...pay, max: event.target.value });
            }}
          />
        </div>
        <div className="col-span-2 flex flex-col gap-1.5 sm:col-span-1">
          <Label htmlFor={`${id}-currency`}>{WORDING.payCurrency}</Label>
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
        </div>
      </div>
    </fieldset>
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
      <label htmlFor={id} className="flex min-h-11 items-center gap-2 text-sm">
        <input
          id={id}
          type="checkbox"
          className="size-4 accent-primary"
          checked={draft.ratherNotSay}
          onChange={(event) => {
            change({ ratherNotSay: event.target.checked });
          }}
        />
        {WORDING.ratherNotSay}
      </label>
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

// --- 6. How the app applies ---------------------------------------------------------------------

export function StepApply({
  draft,
  change,
  settings,
}: PanelProps & { settings: ApplySettings | undefined }) {
  const id = useId();
  const fullAutoOn =
    settings?.mode_after_like === "full_auto" && draft.mode === null;
  const hold = holdText(settings?.hold_s ?? 900);
  return (
    <div className="flex flex-col gap-6">
      <fieldset
        className="flex flex-col gap-2 rounded-md border p-4"
        data-simple-card
      >
        <legend className="mb-1 text-sm font-medium">
          {WORDING.afterLike}
        </legend>
        <label
          htmlFor={`${id}-review`}
          className="flex min-h-11 items-start gap-2 rounded-md border px-3 py-2 text-sm"
        >
          <input
            id={`${id}-review`}
            type="radio"
            name={`${id}-mode`}
            className="mt-0.5 size-4 accent-primary"
            checked={!fullAutoOn}
            onChange={() => {
              change({ mode: "review" });
            }}
          />
          <span className="flex flex-col">
            <span className="font-medium">{WORDING.reviewFirst}</span>
            <span className="text-muted-foreground">
              You approve every application.
            </span>
          </span>
        </label>
        <label
          htmlFor={`${id}-full`}
          className="flex min-h-11 items-start gap-2 rounded-md border border-dashed px-3 py-2 text-sm"
        >
          <input
            id={`${id}-full`}
            type="radio"
            name={`${id}-mode`}
            className="mt-0.5 size-4 accent-primary"
            checked={fullAutoOn}
            disabled
            readOnly
            aria-describedby={`${id}-full-text`}
          />
          <span className="flex flex-col">
            <span className="font-medium opacity-70">
              {WORDING.fullAuto}{" "}
              <span className="ml-1 rounded-full bg-muted px-1.5 py-0.5 text-xs font-normal">
                {WORDING.availableSoon}
              </span>
            </span>
            <span id={`${id}-full-text`} className="text-muted-foreground">
              A Like is enough. The app applies after a hold of at least {hold},
              only above your threshold. Once available, it is turned on in
              Applications settings, with its own checks.
            </span>
          </span>
        </label>
      </fieldset>
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

// --- 7. Summary ---------------------------------------------------------------------------------

export function StepSummary({
  draft,
  settings,
  search,
  onChange,
  footer,
}: {
  draft: Draft;
  settings: ApplySettings | undefined;
  search: Search;
  onChange: (step: StepId) => void;
  footer?: ReactNode;
}) {
  const rows = summaryRows(draft, settings);
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
      <p className="text-sm text-muted-foreground">
        The first search covers {sweepCoverage(search)} from your Search
        options.
      </p>
      {footer}
    </div>
  );
}
