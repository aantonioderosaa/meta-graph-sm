export default function Home() {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 p-8">
      <h1 className="text-3xl font-semibold tracking-tight">Meta-Graph</h1>
      <p className="max-w-xl text-center text-muted-foreground">
        Milestone 1 scaffold — Graph Explorer, Pipeline Monitor e Query Panel arriveranno
        nelle epic successive. Backend API: {apiUrl}
      </p>
    </main>
  );
}
