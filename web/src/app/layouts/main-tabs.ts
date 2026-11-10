import {
  Activity,
  Briefcase,
  Compass,
  Send,
  UserRound,
  type LucideIcon,
} from "lucide-react";

/** The main navigation, in its order at every width: the header's on a wide screen (Admin
 * follows for an admin), the bottom tab bar's on a phone. */
export const MAIN_TABS: readonly {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Applications: how many wait for the person, and the dot of those that need them. */
  counts?: true;
}[] = [
  { to: "/jobs", label: "Jobs", icon: Briefcase },
  { to: "/discover", label: "Discover", icon: Compass },
  { to: "/applications", label: "Applications", icon: Send, counts: true },
  { to: "/profile", label: "Profile", icon: UserRound },
  { to: "/activity", label: "Activity", icon: Activity },
];
