import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorDetail, describeError } from "@/lib/api";
import { startRun } from "./api";

/** Explicit person-only start. Site autosave consent is requested before the first input. */
export function SiteProfileSlot({ initialUrl = "" }: { initialUrl?: string }) {
  const [params] = useSearchParams();
  const [url, setUrl] = useState(
    initialUrl || params.get("profile_site") || "",
  );
  const navigate = useNavigate();
  const start = useMutation({
    mutationFn: () => startRun(url),
    onSuccess: (run) => {
      void navigate(`/site-profiles/${run.id}`);
    },
  });
  return (
    <form
      className="space-y-3 border-t pt-4"
      onSubmit={(event) => {
        event.preventDefault();
        start.mutate();
      }}
      aria-label="Complete my profile on this site"
    >
      <p className="text-sm font-medium">Complete my profile on this site</p>
      <p className="text-sm text-muted-foreground">
        Use facts from your profile to complete a candidate profile. You review
        each section before saving. Existing values are kept until you choose
        Replace.
      </p>
      <Label htmlFor="site-profile-address">Site address</Label>
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
        <p role="alert" className="text-sm text-destructive">
          {errorDetail(start.error) ?? describeError(start.error)}
        </p>
      )}
      <Button type="submit" disabled={start.isPending || !url.trim()}>
        {start.isPending
          ? "Starting profile run"
          : "Complete my profile on this site"}
      </Button>
    </form>
  );
}
