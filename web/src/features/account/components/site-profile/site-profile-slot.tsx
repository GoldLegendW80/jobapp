import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { Button } from "@/components/ui/button";
import { Field, FieldError, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { errorDetail, describeError } from "@/lib/api";
import { useStartSiteProfileRun } from "@/features/apply/api/use-site-profile-run";

/** Explicit person-only start. Site autosave consent is requested before the first input. */
export function SiteProfileSlot({ initialUrl = "" }: { initialUrl?: string }) {
  const [params] = useSearchParams();
  const [url, setUrl] = useState(
    initialUrl || params.get("profile_site") || "",
  );
  const navigate = useNavigate();
  const start = useStartSiteProfileRun(url, {
    onSuccess: (run) => {
      void navigate(`/site-profiles/${run.id}`);
    },
  });
  return (
    <form
      className="flex max-w-2xl flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        start.mutate();
      }}
      aria-label="Complete my profile on this site"
    >
      <p className="text-sm text-muted-foreground">
        Use facts from your profile to complete a candidate profile. You review
        each section before saving. Existing values are kept until you choose
        Replace.
      </p>
      <Field className="gap-3">
        <FieldLabel htmlFor="site-profile-address">Site address</FieldLabel>
        <Input
          id="site-profile-address"
          type="url"
          required
          value={url}
          onChange={(event) => {
            setUrl(event.target.value);
          }}
          placeholder="https://portal.example/profile"
          autoComplete="url"
          disabled={start.isPending}
        />
        {start.error && (
          <FieldError>
            {errorDetail(start.error) ?? describeError(start.error)}
          </FieldError>
        )}
      </Field>
      <Button
        type="submit"
        className="w-fit"
        disabled={start.isPending || !url.trim()}
      >
        {start.isPending
          ? "Starting to complete your profile on this site"
          : "Complete my profile on this site"}
      </Button>
    </form>
  );
}
