import { createContext } from "react";

import type { Form } from "@/features/documents/helpers/edit-form";

/** The form of the draft the page edits; none on a page that is only read (an older version). */
export const PageForm = createContext<Form | null>(null);
