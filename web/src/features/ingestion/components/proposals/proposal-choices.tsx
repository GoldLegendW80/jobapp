import { useId } from "react";

import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";

import type { Citation, ProposalItem } from "../../api/proposals-api";
import { isRecord } from "@/features/ingestion/helpers/proposal-value";
import {
  scalar,
  TWO_ENTRIES_NOTE,
} from "@/features/ingestion/helpers/proposal-words";
import { CitationList } from "./citation-list";
import { ProfileValue } from "./profile-value";

export interface Choice {
  value: unknown;
  citations: Citation[];
}

/** The values the documents disagree on, to pick the one to keep. Nothing is picked at first. */
export function ProposalChoices({
  item,
  title,
  choices,
  chosen,
  disabled,
  onChoose,
  onOpen,
}: {
  item: ProposalItem;
  title: string;
  choices: Choice[];
  chosen: number | undefined;
  disabled: boolean;
  onChoose: (index: number) => void;
  onOpen: (citation: Citation) => void;
}) {
  const group = useId();
  const pending = item.status === "pending";
  return (
    <div className="flex flex-col gap-2">
      <p className="text-sm text-muted-foreground">
        {pending
          ? "Your documents give different values. Pick the one to keep."
          : "Your documents gave different values."}
        {pending && item.section === "experience"
          ? TWO_ENTRIES_NOTE
          : pending && isRecord(item.value)
            ? " If these are two different entries, keep one here and add the other in your profile."
            : ""}
      </p>
      <RadioGroup
        name={group}
        aria-label={`Values for ${title}`}
        value={chosen === undefined ? "" : String(chosen)}
        onValueChange={(value) => {
          onChoose(Number(value));
        }}
        disabled={disabled}
        className="flex flex-col gap-2"
      >
        {choices.map((choice, index) => (
          // The values of a conflict have no identity but their place in it.
          <div
            key={index}
            className="flex flex-col gap-1 rounded-sm border p-2 has-data-[state=checked]:border-primary"
          >
            <Label className="items-start leading-[inherit] font-normal select-text">
              <RadioGroupItem
                value={String(index)}
                className="mt-0.5"
                aria-label={`Value ${String(index + 1)}: ${scalar(choice.value)}`}
              />
              <ProfileValue
                value={choice.value}
                label={`Value ${String(index + 1)}`}
              />
            </Label>
            <CitationList
              citations={choice.citations}
              label={`Sources of value ${String(index + 1)}`}
              onOpen={onOpen}
            />
          </div>
        ))}
      </RadioGroup>
    </div>
  );
}
