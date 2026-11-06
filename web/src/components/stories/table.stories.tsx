import type { Meta, StoryObj } from "@storybook/react-vite";
import { SendIcon } from "lucide-react";

import { statusLabel, statusTone } from "@/features/jobs/helpers/labels";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

type Application = {
  title: string;
  company: string;
  place: string;
  status: string;
  fit: number;
  updated: string;
};

const APPLICATIONS: Application[] = [
  {
    title: "Senior data engineer",
    company: "Lumen Freight",
    place: "Lyon, Hybrid",
    status: "interview",
    fit: 86,
    updated: "5 Nov",
  },
  {
    title: "Analytics engineer",
    company: "Atelier Brume",
    place: "Paris, Remote",
    status: "applied",
    fit: 78,
    updated: "4 Nov",
  },
  {
    title: "Data platform lead",
    company: "Kestrel Health",
    place: "Nantes, On site",
    status: "cv_ready",
    fit: 71,
    updated: "3 Nov",
  },
  {
    title: "Machine learning engineer",
    company: "Orchard Rail",
    place: "Bordeaux, Hybrid",
    status: "staged",
    fit: 64,
    updated: "1 Nov",
  },
  {
    title: "Backend engineer, Python",
    company: "Vireo Energy",
    place: "Lille, Remote",
    status: "rejected",
    fit: 59,
    updated: "29 Oct",
  },
];

function ApplicationsHeader() {
  return (
    <TableHeader>
      <TableRow>
        <TableHead>Posting</TableHead>
        <TableHead>Status</TableHead>
        <TableHead className="text-right">Fit</TableHead>
        <TableHead>Updated</TableHead>
        <TableHead>
          <span className="sr-only">Actions</span>
        </TableHead>
      </TableRow>
    </TableHeader>
  );
}

function ApplicationRow({ row }: { row: Application }) {
  return (
    <TableRow>
      <TableHead>
        <span className="flex flex-col gap-0.5">
          <span>{row.title}</span>
          <span className="text-xs font-normal text-muted-foreground">
            {row.company}, {row.place}
          </span>
        </span>
      </TableHead>
      <TableCell label="Status">
        <Badge variant="outline" className={statusTone(row.status)}>
          {statusLabel(row.status)}
        </Badge>
      </TableCell>
      <TableCell label="Fit" className="tabular-nums sm:text-right">
        {row.fit}
      </TableCell>
      <TableCell label="Updated" className="whitespace-nowrap">
        {row.updated}
      </TableCell>
      <TableCell className="sm:text-right">
        <Button variant="outline" size="sm">
          Open<span className="sr-only"> {row.title}</span>
        </Button>
      </TableCell>
    </TableRow>
  );
}

type Account = {
  name: string;
  initials: string;
  email: string;
  role: string;
  state: string;
  disabled?: boolean;
};

const ACCOUNTS: Account[] = [
  {
    name: "Camille Martin",
    initials: "CM",
    email: "camille.martin@example.org",
    role: "Admin",
    state: "Active",
  },
  {
    name: "Hugo Lefebvre",
    initials: "HL",
    email: "hugo.lefebvre@example.org",
    role: "Member",
    state: "Active",
  },
  {
    name: "Inès Moreau",
    initials: "IM",
    email: "ines.moreau@example.org",
    role: "Member",
    state: "Invited",
  },
  {
    name: "Théo Garnier",
    initials: "TG",
    email: "theo.garnier@example.org",
    role: "Member",
    state: "Disabled",
    disabled: true,
  },
];

function AccountsTable({ busy = false }: { busy?: boolean }) {
  return (
    <Table busy={busy}>
      <TableCaption className="sr-only">Accounts, by name</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>E-mail</TableHead>
          <TableHead>Role</TableHead>
          <TableHead>State</TableHead>
          <TableHead>
            <span className="sr-only">Actions</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {ACCOUNTS.map((account, index) => (
          <TableRow key={account.email} data-disabled={account.disabled}>
            <TableHead>
              <span className="flex items-center gap-2">
                <Avatar size="sm" aria-hidden="true">
                  <AvatarFallback>{account.initials}</AvatarFallback>
                </Avatar>
                {account.name}
              </span>
            </TableHead>
            <TableCell label="E-mail" className="break-all">
              {account.email}
            </TableCell>
            <TableCell label="Role">{account.role}</TableCell>
            <TableCell label="State">{account.state}</TableCell>
            <TableCell className="sm:text-right">
              {busy && index === 1 ? (
                <Button variant="outline" size="sm" disabled>
                  <Spinner aria-hidden="true" role={undefined} />
                  Disabling
                </Button>
              ) : (
                <Button variant="outline" size="sm">
                  {account.disabled ? "Enable" : "Disable"}
                  <span className="sr-only"> {account.name}</span>
                </Button>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

const meta = {
  title: "Components/Data display/Table",
  component: Table,
  tags: ["autodocs"],
  parameters: {
    docs: {
      description: {
        component:
          "Every table of the app: records the person scans and compares, such as applications, postings, accounts. A list table (stacked, the default) turns each row into a card under 640px, each cell led by its label; a comparison table passes stacked={false}, keeps its columns and scrolls sideways inside its border.",
      },
    },
  },
} satisfies Meta<typeof Table>;

export default meta;
type Story = StoryObj<typeof meta>;

/** A list table: the row header names the record, every other cell has a label for the phone layout. */
export const Typical: Story = {
  render: () => (
    <Table>
      <TableCaption className="sr-only">
        Your applications, newest first
      </TableCaption>
      <ApplicationsHeader />
      <TableBody>
        {APPLICATIONS.map((row) => (
          <ApplicationRow key={row.title} row={row} />
        ))}
      </TableBody>
    </Table>
  ),
};

/** A visible caption when no heading above the table already names it. */
export const WithCaption: Story = {
  render: () => (
    <Table>
      <TableCaption>Your applications, newest first</TableCaption>
      <ApplicationsHeader />
      <TableBody>
        {APPLICATIONS.slice(0, 3).map((row) => (
          <ApplicationRow key={row.title} row={row} />
        ))}
      </TableBody>
    </Table>
  ),
};

/** A disabled record (an account switched off) is greyed with data-disabled on its row. */
export const DisabledRow: Story = {
  render: () => <AccountsTable />,
};

/** A comparison table keeps its columns at every width and scrolls sideways inside its border. */
export const Comparison: Story = {
  render: () => (
    <Table stacked={false} className="min-w-xl">
      <TableCaption className="sr-only">
        Three postings compared side by side
      </TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>
            <span className="sr-only">Detail</span>
          </TableHead>
          <TableHead>Lumen Freight</TableHead>
          <TableHead>Atelier Brume</TableHead>
          <TableHead>Kestrel Health</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableRow>
          <TableHead>Role</TableHead>
          <TableCell>Senior data engineer</TableCell>
          <TableCell>Analytics engineer</TableCell>
          <TableCell>Data platform lead</TableCell>
        </TableRow>
        <TableRow>
          <TableHead>Pay</TableHead>
          <TableCell>55,000 to 65,000 EUR a year</TableCell>
          <TableCell>48,000 to 56,000 EUR a year</TableCell>
          <TableCell>70,000 to 80,000 EUR a year</TableCell>
        </TableRow>
        <TableRow>
          <TableHead>Work mode</TableHead>
          <TableCell>Hybrid, Lyon</TableCell>
          <TableCell>Remote</TableCell>
          <TableCell>On site, Nantes</TableCell>
        </TableRow>
        <TableRow>
          <TableHead>Contract</TableHead>
          <TableCell>Permanent</TableCell>
          <TableCell>Permanent</TableCell>
          <TableCell>Fixed term</TableCell>
        </TableRow>
        <TableRow>
          <TableHead>Fit score</TableHead>
          <TableCell className="tabular-nums">86</TableCell>
          <TableCell className="tabular-nums">78</TableCell>
          <TableCell className="tabular-nums">71</TableCell>
        </TableRow>
      </TableBody>
    </Table>
  ),
};

/** Nothing to list yet: one row holds an Empty that says why and offers the next step. */
export const EmptyTable: Story = {
  name: "Empty",
  render: () => (
    <Table>
      <TableCaption className="sr-only">Your applications</TableCaption>
      <ApplicationsHeader />
      <TableBody>
        <TableRow className="hover:bg-transparent sm:hover:bg-transparent">
          <TableCell colSpan={5}>
            <Empty>
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <SendIcon />
                </EmptyMedia>
                <EmptyTitle>No applications yet</EmptyTitle>
                <EmptyDescription>
                  Like a posting in Discover and the app writes its CV and
                  letter. Your applications show up here.
                </EmptyDescription>
              </EmptyHeader>
              <EmptyContent>
                <Button>Open Discover</Button>
              </EmptyContent>
            </Empty>
          </TableCell>
        </TableRow>
      </TableBody>
    </Table>
  ),
};

/** Busy while a change is saved: the table dims and is announced busy, the row's button says what is happening. */
export const Busy: Story = {
  render: () => <AccountsTable busy />,
};

/** Loading: Skeleton rows in the shape of the real ones, under the real header. */
export const Loading: Story = {
  render: () => (
    <div role="status" aria-label="Loading your applications">
      <Table>
        <ApplicationsHeader />
        <TableBody>
          {[0, 1, 2, 3].map((index) => (
            <TableRow key={index} aria-hidden="true">
              <TableHead>
                <span className="flex flex-col gap-1.5">
                  <Skeleton className="h-4 w-40" />
                  <Skeleton className="h-3 w-28" />
                </span>
              </TableHead>
              <TableCell label="Status">
                <Skeleton className="h-5 w-20 rounded-full" />
              </TableCell>
              <TableCell label="Fit">
                <Skeleton className="h-4 w-6 sm:ml-auto" />
              </TableCell>
              <TableCell label="Updated">
                <Skeleton className="h-4 w-12" />
              </TableCell>
              <TableCell>
                <Skeleton className="h-8 w-16 rounded-full sm:ml-auto" />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  ),
};

/** Long titles and company names wrap inside their cell; the badge and the date never wrap. */
export const LongText: Story = {
  render: () => (
    <Table>
      <TableCaption className="sr-only">Your applications</TableCaption>
      <ApplicationsHeader />
      <TableBody>
        <ApplicationRow
          row={{
            title:
              "Senior staff machine learning platform engineer, recommendations and search infrastructure",
            company: "Kestrel Health Research and Clinical Data Services",
            place: "Saint-Herblain, near Nantes, Hybrid",
            status: "shortlisted",
            fit: 71,
            updated: "3 Nov",
          }}
        />
        {APPLICATIONS.slice(1, 2).map((row) => (
          <ApplicationRow key={row.title} row={row} />
        ))}
      </TableBody>
    </Table>
  ),
};

/** Many rows: the same table, longer. The header stays one line; rows keep one height per content. */
export const ManyRows: Story = {
  render: () => (
    <Table>
      <TableCaption className="sr-only">Your applications</TableCaption>
      <ApplicationsHeader />
      <TableBody>
        {[...APPLICATIONS, ...APPLICATIONS, ...APPLICATIONS].map(
          (row, index) => (
            <ApplicationRow
              key={`${row.title}-${String(index)}`}
              row={{ ...row, fit: row.fit - index }}
            />
          ),
        )}
      </TableBody>
    </Table>
  ),
};
