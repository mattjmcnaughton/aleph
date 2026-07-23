import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/")({
  component: Index,
});

function Index() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-24">
      <h1 className="text-4xl font-bold">aleph</h1>
      <p className="mt-4 text-lg text-gray-600">
        Mobile-friendly AI tutor: name a topic, get a generated learning path
      </p>
    </main>
  );
}
