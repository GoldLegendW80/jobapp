import { MAX } from "@/features/documents/helpers/edit-content";
import type { Form } from "@/features/documents/helpers/edit-form";
import { ListField } from "../edit-fields";
import { FormLines, type FormLine } from "./form-lines";
import { FormSection } from "./form-section";

const LETTER_TOP: FormLine[] = [
  ["target", "What this letter is for (not printed)", MAX.line],
  ["headline", "Headline", MAX.headline],
  ["recipient", "Recipient", MAX.line],
  ["subject", "Subject", MAX.line],
  ["salutation", "Salutation", MAX.line],
];

/** The Edit view of a letter: its heading lines, its paragraphs, its closing. */
export function LetterForm({ form }: { form: Form }) {
  return (
    <FormSection title="The letter">
      <FormLines form={form} lines={LETTER_TOP} />
      <ListField
        form={form}
        path={["paragraphs"]}
        legend="Paragraphs"
        noun="Paragraph"
        max={MAX.paragraphs}
        itemMax={MAX.paragraph}
        multiline
      />
      <FormLines form={form} lines={[["closing", "Closing", MAX.line]]} />
    </FormSection>
  );
}
