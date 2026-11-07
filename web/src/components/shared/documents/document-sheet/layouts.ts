import type { ComponentType } from "react";

import type { LayoutProps } from "./parts";
import { BandCv, BandLetter } from "./templates/band";
import { LedgerCv, LedgerLetter } from "./templates/ledger";
import { PhotoColumnsCv, PhotoColumnsLetter } from "./templates/photo-columns";

export interface TemplateLayout {
  cv: ComponentType<LayoutProps>;
  letter: ComponentType<LayoutProps>;
}

/** The page of each bundled template (api/app/documents/templates/<slug>), drawn here. */
export const LAYOUTS: Readonly<Record<string, TemplateLayout>> = {
  band: { cv: BandCv, letter: BandLetter },
  ledger: { cv: LedgerCv, letter: LedgerLetter },
  "photo-columns": { cv: PhotoColumnsCv, letter: PhotoColumnsLetter },
};

/** What the renderer prints a template it does not know with (templates.DEFAULT). */
export const DEFAULT_TEMPLATE = "band";
