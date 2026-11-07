import type { AdminInvite, AdminUser } from "@/features/admin/api/admin-api";
import { admin } from "@/test/auth";

const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const ago = (ms: number) => new Date(Date.now() - ms).toISOString();
const ahead = (ms: number) => new Date(Date.now() + ms).toISOString();
const id = (serial: number) =>
  `00000000-0000-4000-8000-${String(serial).padStart(12, "0")}`;

/** The accounts of a small install for the page stories: invented people, never a real one. */
export const ACCOUNTS: AdminUser[] = [
  {
    ...admin,
    is_active: true,
    last_login_at: ago(20 * 60_000),
    sessions: 2,
    browsers: 2,
  },
  {
    id: id(201),
    email: "ines.moreau@example.org",
    name: "Inès Moreau",
    role: "admin",
    is_active: true,
    created_at: ago(30 * DAY),
    last_login_at: ago(2 * DAY),
    sessions: 1,
    browsers: 2,
  },
  {
    id: id(202),
    email: "tomas.okafor@example.org",
    name: "Tomás Okafor",
    role: "member",
    is_active: true,
    created_at: ago(21 * DAY),
    last_login_at: ago(5 * HOUR),
    sessions: 3,
    browsers: 2,
  },
  {
    id: id(203),
    email: "priya.natarajan@example.org",
    name: "Priya Natarajan",
    role: "member",
    is_active: true,
    created_at: ago(9 * DAY),
    last_login_at: null,
    sessions: 0,
    browsers: 2,
  },
  {
    id: id(204),
    email: "lena.vogel@example.org",
    name: "",
    role: "member",
    is_active: false,
    created_at: ago(60 * DAY),
    last_login_at: ago(40 * DAY),
    sessions: 0,
    browsers: 2,
  },
];

const invite = (
  serial: number,
  changes: Partial<AdminInvite> & Pick<AdminInvite, "email">,
): AdminInvite => ({
  id: id(serial),
  kind: "invite",
  role: "member",
  status: "active",
  created_by_name: admin.name,
  created_at: ago(HOUR),
  expires_at: ahead(7 * DAY - HOUR),
  used_at: null,
  revoked_at: null,
  ...changes,
});

/** One link of every kind and status, newest first, as the api lists them. */
export const INVITES: AdminInvite[] = [
  invite(301, { email: "noor.haddad@example.org" }),
  invite(302, {
    email: "tomas.okafor@example.org",
    kind: "reset",
    role: null,
    created_at: ago(20 * 60_000),
    expires_at: ahead(40 * 60_000),
  }),
  invite(303, {
    email: "felix.brandt@example.org",
    role: "admin",
    created_by_name: "Inès Moreau",
    created_at: ago(2 * DAY),
    expires_at: ahead(5 * DAY),
  }),
  invite(304, {
    email: "priya.natarajan@example.org",
    status: "used",
    created_at: ago(10 * DAY),
    expires_at: ago(3 * DAY),
    used_at: ago(9 * DAY),
  }),
  invite(305, {
    email: "jonas.berg@example.org",
    status: "revoked",
    created_at: ago(12 * DAY),
    expires_at: ago(5 * DAY),
    revoked_at: ago(11 * DAY),
  }),
  invite(306, {
    email: "ada.lindqvist@example.org",
    status: "expired",
    created_by_name: null,
    created_at: ago(20 * DAY),
    expires_at: ago(13 * DAY),
  }),
];

/** GET /api/admin/users as the api answers it for `items`. */
export const usersPage = (items: AdminUser[]) => ({
  items,
  total: items.length,
  limit: 50,
  offset: 0,
  active_admins: items.filter((u) => u.is_active && u.role === "admin").length,
  browsers_max: 2,
});

/** GET /api/admin/invites as the api answers it for `items`. */
export const invitesPage = (items: AdminInvite[]) => ({
  items,
  total: items.length,
  limit: 20,
  offset: 0,
});

/** A long list of links, newest first, for the pages of the invites list. */
export const manyInvites = (count: number): AdminInvite[] =>
  Array.from({ length: count }, (_, index) =>
    invite(400 + index, {
      email: `e2e-friend-${String(index + 1)}@example.org`,
      created_at: ago((index + 1) * HOUR),
    }),
  );
