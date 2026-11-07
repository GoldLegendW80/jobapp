import type { ComponentType } from "react";

import type { LayoutProps } from "./parts";
import { AcademicCv, AcademicLetter } from "./templates/academic";
import { AccentCv, AccentLetter } from "./templates/accent";
import { BannerCv, BannerLetter } from "./templates/banner";
import { BoldCv, BoldLetter } from "./templates/bold";
import { CardsCv, CardsLetter } from "./templates/cards";
import { ClassicCv, ClassicLetter } from "./templates/classic";
import {
  ColumnsHeaderCv,
  ColumnsHeaderLetter,
} from "./templates/columns-header";
import { CompactCv, CompactLetter } from "./templates/compact";
import { ElegantCv, ElegantLetter } from "./templates/elegant";
import { ExecutiveCv, ExecutiveLetter } from "./templates/executive";
import { GridCv, GridLetter } from "./templates/grid";
import { MinimalCv, MinimalLetter } from "./templates/minimal";
import { MonoCv, MonoLetter } from "./templates/mono";
import { NordicCv, NordicLetter } from "./templates/nordic";
import { PlainCv, PlainLetter } from "./templates/plain";
import { PortraitCv, PortraitLetter } from "./templates/portrait";
import { SerifCv, SerifLetter } from "./templates/serif";
import { SidebarCv, SidebarLetter } from "./templates/sidebar";
import { SplitRightCv, SplitRightLetter } from "./templates/split-right";
import { TimelineCv, TimelineLetter } from "./templates/timeline";

export interface TemplateLayout {
  cv: ComponentType<LayoutProps>;
  letter: ComponentType<LayoutProps>;
}

/** The page of each bundled template (api/app/documents/templates/<slug>), drawn here. */
export const LAYOUTS: Readonly<Record<string, TemplateLayout>> = {
  academic: { cv: AcademicCv, letter: AcademicLetter },
  accent: { cv: AccentCv, letter: AccentLetter },
  banner: { cv: BannerCv, letter: BannerLetter },
  bold: { cv: BoldCv, letter: BoldLetter },
  cards: { cv: CardsCv, letter: CardsLetter },
  classic: { cv: ClassicCv, letter: ClassicLetter },
  "columns-header": { cv: ColumnsHeaderCv, letter: ColumnsHeaderLetter },
  compact: { cv: CompactCv, letter: CompactLetter },
  elegant: { cv: ElegantCv, letter: ElegantLetter },
  executive: { cv: ExecutiveCv, letter: ExecutiveLetter },
  grid: { cv: GridCv, letter: GridLetter },
  minimal: { cv: MinimalCv, letter: MinimalLetter },
  mono: { cv: MonoCv, letter: MonoLetter },
  nordic: { cv: NordicCv, letter: NordicLetter },
  plain: { cv: PlainCv, letter: PlainLetter },
  portrait: { cv: PortraitCv, letter: PortraitLetter },
  serif: { cv: SerifCv, letter: SerifLetter },
  sidebar: { cv: SidebarCv, letter: SidebarLetter },
  "split-right": { cv: SplitRightCv, letter: SplitRightLetter },
  timeline: { cv: TimelineCv, letter: TimelineLetter },
};

/** What the renderer prints a template it does not know with (templates.DEFAULT). */
export const DEFAULT_TEMPLATE = "classic";
