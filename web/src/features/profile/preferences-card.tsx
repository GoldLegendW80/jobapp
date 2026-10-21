/**
 * Preferences (opportunities O4): the language and the tone of the CV and letter the app writes,
 * saved as the profile's `preferences` section with its own Save, under the Search tab. The
 * onboarding dates of the section are the first login wizard's: the card sends them back as it
 * loaded them, never edits them (the api refuses a change).
 */
import { z } from "zod";

import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { NativeSelect } from "@/components/ui/native-select";

import {
  DOCUMENTS_LANGUAGES,
  DOCUMENTS_TONES,
  type Preferences,
  type Profile,
} from "./api";
import { ConflictDialog, FormErrorAlert, Group, SaveBar } from "./section-ui";
import { useSectionForm } from "./use-section-form";

const LANGUAGE_LABELS: Record<(typeof DOCUMENTS_LANGUAGES)[number], string> = {
  auto: "The posting's language",
  en: "English",
  fr: "French",
};

const TONE_LABELS: Record<(typeof DOCUMENTS_TONES)[number], string> = {
  plain: "Plain",
  formal: "Formal",
  warm: "Warm",
};

const preferencesSchema = z.object({
  onboarding: z.object({
    completed_at: z.string().nullable(),
    dismissed_at: z.string().nullable(),
  }),
  documents_language: z.enum(DOCUMENTS_LANGUAGES),
  documents_tone: z.enum(DOCUMENTS_TONES),
});
type PreferencesForm = z.infer<typeof preferencesSchema>;

const toForm = (p: Preferences): PreferencesForm => ({
  ...p,
  onboarding: { ...p.onboarding },
});
const fromForm = (v: PreferencesForm): Preferences => ({
  ...v,
  onboarding: { ...v.onboarding },
});

export function PreferencesCard({
  profile,
  onDirtyChange,
}: {
  profile: Profile;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const { form, onSubmit, saving, formError, conflict, keepEditing, reload } =
    useSectionForm({
      section: "preferences",
      profile,
      schema: preferencesSchema,
      toForm,
      fromForm,
      onDirtyChange,
    });
  return (
    <>
      <Form {...form}>
        <form
          noValidate
          aria-label="Preferences"
          className="flex flex-col gap-4"
          onSubmit={(event) => void onSubmit(event)}
        >
          <FormErrorAlert message={formError} />
          <Group
            title="Preferences"
            description="How the CV and cover letter written for each posting read."
          >
            <FormField
              control={form.control}
              name="documents_language"
              render={({ field }) => (
                <FormItem className="max-w-64">
                  <FormLabel>Language of documents</FormLabel>
                  <FormControl>
                    <NativeSelect {...field}>
                      {DOCUMENTS_LANGUAGES.map((code) => (
                        <option key={code} value={code}>
                          {LANGUAGE_LABELS[code]}
                        </option>
                      ))}
                    </NativeSelect>
                  </FormControl>
                  <FormDescription>
                    Or follow the language of each posting.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="documents_tone"
              render={({ field }) => (
                <FormItem className="max-w-64">
                  <FormLabel>Tone of documents</FormLabel>
                  <FormControl>
                    <NativeSelect {...field}>
                      {DOCUMENTS_TONES.map((tone) => (
                        <option key={tone} value={tone}>
                          {TONE_LABELS[tone]}
                        </option>
                      ))}
                    </NativeSelect>
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </Group>
          <SaveBar
            label="Preferences"
            saving={saving}
            dirty={form.formState.isDirty}
          />
        </form>
      </Form>
      <ConflictDialog
        open={conflict}
        label="Preferences"
        onKeepEditing={keepEditing}
        onReload={reload}
      />
    </>
  );
}
