import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogTitle,
} from "./alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "./dialog";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "./sheet";

// Under prefers-reduced-motion the zoom and slide of a dialog or a sheet stop: the class wins
// over the open state's own animation (important, as `data-[state=open]:animate-in` is more
// specific), and the overlay stops fading too.
const REDUCED = "motion-reduce:animate-none!";

describe("reduced motion", () => {
  it("stops the enter animation of a dialog and its overlay", () => {
    render(
      <Dialog open>
        <DialogContent>
          <DialogTitle>Title</DialogTitle>
          <DialogDescription>Text</DialogDescription>
        </DialogContent>
      </Dialog>,
    );
    const content = screen.getByRole("dialog");
    expect(content.className).toContain(REDUCED);
    const overlay = document.querySelector("[class*='bg-black/50']");
    expect(overlay?.className).toContain(REDUCED);
  });

  it("stops the slide of a sheet", () => {
    render(
      <Sheet open>
        <SheetContent>
          <SheetTitle>Sheet</SheetTitle>
          <SheetDescription>Text</SheetDescription>
        </SheetContent>
      </Sheet>,
    );
    expect(screen.getByRole("dialog", { name: "Sheet" }).className).toContain(
      REDUCED,
    );
  });

  it("stops the zoom of an alert dialog", () => {
    render(
      <AlertDialog open>
        <AlertDialogContent>
          <AlertDialogTitle>Sure?</AlertDialogTitle>
          <AlertDialogDescription>Text</AlertDialogDescription>
        </AlertDialogContent>
      </AlertDialog>,
    );
    expect(
      screen.getByRole("alertdialog", { name: "Sure?" }).className,
    ).toContain(REDUCED);
  });
});
