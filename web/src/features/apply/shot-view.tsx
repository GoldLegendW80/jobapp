import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";

import { screenshotUrl } from "./apply-api";

/** One screenshot of the form, small on the page, full size in a dialog when it is clicked. */
export function ShotView({
  applicationId,
  shotId,
  caption,
}: {
  applicationId: string;
  shotId: string;
  caption: string;
}) {
  const [open, setOpen] = useState(false);
  const src = screenshotUrl(applicationId, shotId);
  return (
    <figure className="flex min-w-0 flex-col gap-1">
      <figcaption className="text-sm font-medium">{caption}</figcaption>
      <Button
        type="button"
        variant="ghost"
        onClick={() => {
          setOpen(true);
        }}
        aria-label={`Enlarge: ${caption}`}
        className="h-auto max-h-80 items-start justify-start overflow-hidden border p-0 text-left whitespace-normal hover:bg-transparent dark:hover:bg-transparent"
      >
        <img
          src={src}
          alt={caption}
          loading="lazy"
          className="w-full object-cover object-top"
        />
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-[calc(100%-1rem)] sm:max-w-4xl">
          <DialogTitle>{caption}</DialogTitle>
          <DialogDescription className="sr-only">
            The whole page as the assistant saw it. Scroll to see all of it.
          </DialogDescription>
          <img src={src} alt={`${caption}, full size`} className="w-full" />
        </DialogContent>
      </Dialog>
    </figure>
  );
}
