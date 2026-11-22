# Storybook: the design sandbox

Storybook renders Aleph's real routes and components against the same in-memory
MSW fakes the unit tests use, seeded with one realistic learner's data. Use it to
design, compare and review UI on every screen and state without a backend,
Keycloak or model provider.

```sh
cd src/aleph/web/frontend
pnpm storybook          # dev server on :6006 (or `just storybook`)
pnpm build-storybook    # static build into storybook-static/
```

In an orb, run it as a supervised service and open the portal it prints:

```sh
amp orb service start storybook --port 6006 --portal \
  --command 'cd src/aleph/web/frontend && pnpm exec storybook dev -p 6006 --host 0.0.0.0 --no-open --ci'
```

`--host 0.0.0.0` matters: without it Storybook answers the portal's hostname
with `403 Invalid host`.

## Layout

| File | What it holds |
| ---- | ------------- |
| `.storybook/main.ts` | Story globs, the MSW worker's static dir. |
| `.storybook/preview.tsx` | Global CSS, the MSW worker with every app fake, a per-story reset of the fakes, phone/tablet/desktop viewports (iPhone by default). |
| `src/stories/world.ts` | The mock **world**: paths at every stage, lessons with Markdown, code and Mermaid, a tutor thread with a Tutor check, a pending Proposal, a 49-day activity history, Beats with published and Skipped Briefs. |
| `src/stories/app-at.tsx` | `<AppAt url="/paths/…" />`: the real app at a URL, on a memory history. |
| `src/stories/screens.stories.tsx` | One story per screen and state: Home, Login, New path, Path, Lesson, Flow, Beat, Brief, Settings. |
| `src/stories/components.stories.tsx` | Shared components in isolation. |
| `src/stories/foundations.stories.tsx` | The design tokens from `tailwind.config.ts`. |
| `src/stories/concepts.stories.tsx` | `Concepts/Directions`: the design directions considered for Nocturne, side by side ([`design.md`](design.md)). |

## Adding a story

- **A new state of a screen:** add a story to `screens.stories.tsx` with the
  URL and a `beforeEach` that seeds or configures the fakes, for example
  `configurePaths({ rateLimited: true })`. The preview resets every fake before
  each story, and the meta's `beforeEach` seeds the whole world first.
- **A different session or endpoint:** `beforeEach: ({ msw }) => { msw.use(handler) }`.
- **A state reached by interaction,** such as an error after submit: a `play`
  function using `storybook/test`.
- **Alternative designs:** put exploratory variants under a `Concepts/…` title
  so they sit beside, not inside, the stories that show the shipped UI.

Use the vocabulary in [`CONTEXT.md`](CONTEXT.md) in story names and mock copy.
Keep the mock data plausible: designs are judged on it.
