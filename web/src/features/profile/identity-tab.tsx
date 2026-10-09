import { Plus } from "lucide-react";
import { useFieldArray } from "react-hook-form";

import { Button } from "@/components/ui/button";
import { Form } from "@/components/ui/form";

import type { Profile } from "./api";
import {
  fromIdentityForm,
  identitySchema,
  LANGUAGES_MAX,
  LINKS_MAX,
  toIdentityForm,
} from "./forms";
import { PhotoField } from "./photo";
import {
  ConflictDialog,
  FormErrorAlert,
  Group,
  ItemControls,
  SaveBar,
  TextField,
} from "./section-ui";
import { useSectionForm } from "./use-section-form";

export function IdentityTab({
  profile,
  onDirtyChange,
}: {
  profile: Profile;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const { form, onSubmit, saving, formError, conflict, keepEditing, reload } =
    useSectionForm({
      section: "identity",
      profile,
      schema: identitySchema,
      toForm: toIdentityForm,
      fromForm: fromIdentityForm,
      rules: { unique: { languages: "name" } },
      onDirtyChange,
    });
  const languages = useFieldArray({ control: form.control, name: "languages" });
  const links = useFieldArray({ control: form.control, name: "links" });
  const { control } = form;

  return (
    <div className="flex flex-col gap-6">
      <Group
        title="Photo"
        description="Shown on generated CVs that use a photo."
      >
        <PhotoField profile={profile} />
      </Group>
      <Form {...form}>
        <form
          noValidate
          aria-label="Identity"
          className="flex flex-col gap-6"
          onSubmit={(event) => void onSubmit(event)}
        >
          <FormErrorAlert message={formError} />
          <Group
            title="You"
            description="Your name and contact details, as every document shows them."
          >
            <div className="grid gap-4 sm:grid-cols-2">
              <TextField
                control={control}
                name="full_name"
                label="Full name"
                autoComplete="name"
              />
              <TextField control={control} name="headline" label="Headline" />
              <TextField
                control={control}
                name="email"
                label="Email"
                type="email"
                inputMode="email"
                autoComplete="email"
                spellCheck={false}
              />
              <TextField
                control={control}
                name="phone"
                label="Phone"
                type="tel"
                autoComplete="tel"
              />
              <TextField
                control={control}
                name="city"
                label="City"
                autoComplete="address-level2"
              />
              <TextField
                control={control}
                name="country"
                label="Country"
                autoComplete="country-name"
              />
              <TextField
                control={control}
                name="linkedin_url"
                label="LinkedIn address"
                type="url"
                inputMode="url"
                spellCheck={false}
                placeholder="https://www.linkedin.com/in/..."
              />
              <TextField
                control={control}
                name="website"
                label="Website"
                type="url"
                inputMode="url"
                spellCheck={false}
                placeholder="https://"
              />
            </div>
          </Group>

          <Group
            title="Languages"
            description="In the order documents list them."
            action={
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={languages.fields.length >= LANGUAGES_MAX}
                onClick={() => {
                  languages.append(
                    { name: "", level: "" },
                    { shouldFocus: true },
                  );
                }}
              >
                <Plus aria-hidden="true" />
                Add language
              </Button>
            }
          >
            {languages.fields.length === 0 ? (
              <p className="text-sm text-muted-foreground">No language yet.</p>
            ) : (
              <ol className="flex flex-col gap-3">
                {languages.fields.map((item, index) => (
                  <li
                    key={item.id}
                    className="grid gap-3 rounded-md border p-3 sm:grid-cols-[1fr_1fr_auto] sm:items-start"
                  >
                    <TextField
                      control={control}
                      name={`languages.${index}.name`}
                      label="Language"
                      srSuffix={`${index + 1}`}
                    />
                    <TextField
                      control={control}
                      name={`languages.${index}.level`}
                      label="Level"
                      srSuffix={`of language ${index + 1}`}
                      placeholder="Native, C1, Intermediate"
                    />
                    <div className="sm:pt-6">
                      <ItemControls
                        index={index}
                        count={languages.fields.length}
                        name={`language ${index + 1}`}
                        onMove={languages.move}
                        onRemove={languages.remove}
                      />
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </Group>

          <Group
            title="Other links"
            description="GitHub, a portfolio, anything else worth a click."
            action={
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={links.fields.length >= LINKS_MAX}
                onClick={() => {
                  links.append({ label: "", url: "" }, { shouldFocus: true });
                }}
              >
                <Plus aria-hidden="true" />
                Add link
              </Button>
            }
          >
            {links.fields.length === 0 ? (
              <p className="text-sm text-muted-foreground">No other link.</p>
            ) : (
              <ol className="flex flex-col gap-3">
                {links.fields.map((item, index) => (
                  <li
                    key={item.id}
                    className="grid gap-3 rounded-md border p-3 sm:grid-cols-[1fr_1fr_auto] sm:items-start"
                  >
                    <TextField
                      control={control}
                      name={`links.${index}.label`}
                      label="Label"
                      srSuffix={`of link ${index + 1}`}
                    />
                    <TextField
                      control={control}
                      name={`links.${index}.url`}
                      label="Address"
                      srSuffix={`of link ${index + 1}`}
                      type="url"
                      inputMode="url"
                      spellCheck={false}
                    />
                    <div className="sm:pt-6">
                      <ItemControls
                        index={index}
                        count={links.fields.length}
                        name={`link ${index + 1}`}
                        onMove={links.move}
                        onRemove={links.remove}
                      />
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </Group>
          <SaveBar
            label="Identity"
            saving={saving}
            dirty={form.formState.isDirty}
          />
        </form>
      </Form>
      <ConflictDialog
        open={conflict}
        label="Identity"
        onKeepEditing={keepEditing}
        onReload={reload}
      />
    </div>
  );
}
