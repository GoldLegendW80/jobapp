import { HealthCard } from "@/features/health/health-card";

export default function App() {
  return (
    <div className="min-h-dvh px-4 py-12 sm:py-20">
      <main className="mx-auto flex max-w-md flex-col items-center gap-8">
        <header className="flex flex-col items-center gap-2 text-center">
          <h1 className="text-3xl font-semibold tracking-tight">jobapp</h1>
          <p className="text-muted-foreground">
            Find postings, tailor documents, apply.
          </p>
        </header>
        <HealthCard />
      </main>
    </div>
  );
}
