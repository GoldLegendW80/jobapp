import { useId } from "react";
import { Link } from "react-router";

import { Checkbox } from "@/components/ui/checkbox";
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import {
  holdText,
  type ApplySettings,
} from "@/features/apply/api/settings-api";
import {
  DOCUMENTS_LANGUAGES,
  DOCUMENTS_TONES,
} from "@/features/profile/api/profile-api";
import { cn } from "@/lib/utils";
import { WORDING } from "@/lib/wording";

import type { PanelProps } from "@/features/onboarding/helpers/steps";
import {
  LANGUAGE_WORDS,
  TONE_WORDS,
} from "@/features/onboarding/helpers/words";
import { CHOICE_CARD, Radios } from "./choice-cards";

/** A mode of the Apply step: a card with a title and a line under it. */
const MODE =
  "min-h-11 w-full items-start rounded-md border px-3 py-2 font-normal";

/**
 * Step 9, How the app applies. Two choices, each kept in apply's own settings (A11, issue #738):
 * the mode after a Like, and "Let me review each filled form before it is sent", which a click
 * on Apply follows. Full auto is only shown here: it is turned on in Application settings, with
 * its own checks. Then the documents' language and tone.
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
