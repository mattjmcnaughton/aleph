# Design: Nocturne, second pass

Nocturne is Aleph's visual system: dark, teal, phone first
([`CONTEXT.md`](CONTEXT.md#design)). This document records how the web client
applies it and why. The tokens live in
`src/aleph/web/frontend/tailwind.config.ts` and `src/index.css`; every screen
and state can be judged in the Storybook sandbox ([`storybook.md`](storybook.md)),
where `Foundations/Tokens` shows the palette and type and `Concepts/Directions`
shows the alternatives that were considered.

## The idea: a study, not an arcade

Aleph is somewhere an adult goes to read, think and check their understanding.
The interface should feel like a well-made book read by lamplight, and stay out
of the way of the passage. Four rules follow from that.

1. **Type carries the hierarchy.** Titles and reading text are set in a serif
   made for long reading on screens; the interface is a quiet sans. A label
   appears only where it says something the content below does not ("Lesson 5
   of 11, Lifetimes in practice", never "LESSON").
2. **Lists are ruled, cards are rare.** A list of paths, lessons or Briefs is
   rows separated by hairlines. A bordered card is reserved for one thing the
   learner acts on: the Continue target, a Proposal, a state that needs a
   decision.
3. **Each accent means one thing.** Teal is *forward*: the primary action, the
   learner's position, their progress. Iris is *the tutor* and what it
   proposes. Ember is the streak's flame. Danger is destructive or failed.
   Nothing is coloured for decoration.
4. **The primary action is the loudest thing on the screen, and there is one.**
   Destructive and account actions are quiet and one step away.

## Tokens

| Role | Token | Value |
| ---- | ----- | ----- |
| Page | `night` | `#11161b` |
| Raised | `surface`, `elevated` | `#182027`, `#202a32` |
| Text | `porcelain` / `mist` / `slate` | `#ebe6da` / `#a5a89f` / `#7f847d` |
| Hairline | `divider`, `faint` | `porcelain` at 12%, `#49535a` |
| Forward | `teal` | `#56b3ab` |
| Tutor | `iris` | `#9a92d8` |
| Streak | `ember` | `#e3a35a` |
| Failure | `danger` | `#ef8f82` |

Type:

| Use | Face | Class |
| --- | ---- | ----- |
| Page title | Literata 500 | `.page-title` |
| Reading (Read passage, Brief) | Literata, 17px / 1.7 | `.reading` |
| Interface | Instrument Sans | default |
| Code | JetBrains Mono | `font-mono` (code only, never labels) |
| Section label | Instrument Sans 13px, sentence case | `.kicker` |

The fonts are self-hosted through `@fontsource-variable/*` packages and bundled
by Vite, so nothing is fetched from a third party. Radius follows hierarchy:
controls 8px, cards 12px, sheets 16px. Shadows are for things that float
(menus, sheets); a card is separated by its border.

## Layout

- **Phone:** one column, 20px gutters, a 60px header holding the brand and the
  account menu. The breadcrumb is the way back and shows ancestors only. The
  tutor and shaping rails open from a bar docked at the bottom of the screen.
- **Desktop (`lg`, 1024px):** routes with a sidebar or rail use `Workspace`;
  single-column routes (Flow, Brief, Beat, the creation forms) use `.page`,
  which gives them a 700px measure. Reading text is capped near 68 characters.

## Rules for new surfaces

- Use the vocabulary in `CONTEXT.md` in every label.
- Start from an existing analog in Storybook and reuse its row, label and
  action treatments before adding one.
- Add a story for each new screen and state, and judge it at the iPhone and
  desktop viewports before shipping.
- Motion answers an action (opening, confirming) and respects
  `prefers-reduced-motion`. Entrances on load are reserved for the two
  celebrations (path complete, flow receipt).
