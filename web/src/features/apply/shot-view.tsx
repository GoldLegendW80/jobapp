import { useState } from "react";

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
  title,
}: {
  applicationId: string;
  shotId: string;
  title: string;
}) {
  const [open, setOpen] = useState(false);
  const src = screenshotUrl(applicationId, shotId);
  return (
    <figure className="flex min-w-0 flex-col gap-1">
      <figcaption className="text-sm font-medium">{title}</figcaption>
      <button
        type="button"
        onClick={() => {
          setOpen(true);
        }}
        aria-label={`Enlarge: ${title}`}
        className="max-h-80 overflow-hidden rounded-md border text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-hidden"
      >
        <img
          src={src}
          alt={title}
          loading="lazy"
          className="w-full object-cover object-top"
        />
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-[calc(100%-1rem)] sm:max-w-4xl">
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className="sr-only">
            The whole page as the assistant saw it. Scroll to see all of it.
          </DialogDescription>
          <img src={src} alt={`${title}, full size`} className="w-full" />
        </DialogContent>
      </Dialog>
    </figure>
  );
}
