/**
 * "About you": the person's Markdown document (owner, 2026-11-05: "just put informations on a .md
 * should be well rendered and editable", "a markdown that a model interpret"). Each part (a `##`
 * heading) is shown rendered by the safe renderer and edited in place, one at a time, with a live
 * preview; the text saves as the person types (AUTOSAVE_MS, as the CV editor). Done reads the
 * part with the model; under it, "What the app understood" lists the changes with one Apply, then
 * Undo. Without a model key the text still saves and a line under Done says what is missing. The
 * contents list is sticky on desktop and a row of chips on phones.
 */
import { RotateCw } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { describeError } from "@/lib/api";

import { useAbout } from "../api/about-api";
import { ABOUT_WORDS as W } from "../helpers/about-words";
import { AboutEditor } from "./about/about-editor";

export function AboutDocument() {
  const query = useAbout();
  if (query.isPending)
    return (
      <p
        role="status"
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        <Spinner aria-hidden="true" />
        {W.loading}
      </p>
    );
  if (query.isError)
    return (
      <Alert variant="destructive">
        <AlertTitle>{W.notLoaded}</AlertTitle>
        <AlertDescription>{describeError(query.error)}</AlertDescription>
        <Button
          variant="outline"
          size="sm"
          className="mt-2 w-fit"
          onClick={() => void query.refetch()}
        >
          <RotateCw aria-hidden="true" />
          Retry
        </Button>
      </Alert>
    );
  return <AboutEditor about={query.data} />;
}
