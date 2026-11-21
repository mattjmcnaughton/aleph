import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryHistory, createRouter } from "@tanstack/react-router";
import { useState } from "react";
import { makeQueryClient } from "../app/app";
import { routeTree } from "../routeTree.gen";

/**
 * The real app (routes, auth gate, header, query cache) opened at `url`, with a
 * memory history so a story never touches Storybook's own iframe URL. A screen
 * story is one of these plus the fake data it seeds.
 */
export function AppAt({ url }: { url: string }) {
  const [queryClient] = useState(makeQueryClient);
  const [router] = useState(() =>
    createRouter({
      routeTree,
      context: { queryClient },
      history: createMemoryHistory({ initialEntries: [url] }),
    }),
  );
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
