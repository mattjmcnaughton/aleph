# TDD: Lesson visuals

Status: built on `feature/lesson-visuals` (2026-11-22). Owner-approved security
model; shipped enabled, no flag. Vocabulary: **Visual** in
[`CONTEXT.md`](../CONTEXT.md). Constitution: §9 amendment of 2026-11-22.

## What it is

The lesson agent may attach at most one interactive **Visual** to a lesson: a
small HTML body (markup + inline script) the learner manipulates. It is the
third rung after prose and ```mermaid; most lessons get none, and the prose must
teach without it and state the key numbers.

## Decisions

- **D1 Stored beside the passage.** Table `lesson_visuals` (migration 0023):
  `id` (UUID, the visual's identifier), `lesson_id` → `lessons` `ON DELETE
  CASCADE`, `kind` (`interactive`), `title`, `alt`, `source`, `kit_version`.
  The passage places it with an empty fence: ```` ```visual <uuid> ````.
- **D2 UUIDs, minted by the server.** The agent writes a placeholder ref
  (`visual-1`) in both the fence and its `Visual`; `_persist_lesson` mints the
  UUID and rewrites the fence in the same fenced transaction as the passage and
  Quick check. The fence grammar (strict: one ref, empty body) lives in
  `domains/visuals.py`, shared by the validator, persistence and continuity.
- **D3 Validation is sanity, not security.** `validate_visuals` (ModelRetry):
  ≤1 visual; each placeholder fenced exactly once; every fence declared; non-empty
  title/alt/source; source ≤ 12 KB, calls `kit.ready()`, no
  `iframe/object/embed/link/base`, no `script src`, no external URLs (the SVG
  namespace is allowed). The source is outside the word band.
- **D4 The sandbox is the defense.** `components/lesson-visual.tsx` mounts a
  fresh `<iframe sandbox="allow-scripts" srcdoc referrerpolicy="no-referrer">`
  per visual (React `key`) — no `allow-same-origin`, so the frame has an opaque
  origin: no cookies, storage, `parent.document` or credentialed API calls. The
  srcdoc is the kit (CSP `default-src 'none'; script-src 'unsafe-inline';
  style-src 'unsafe-inline'`, Nocturne tokens as CSS variables, system fonts,
  `row`/`muted`/`ghost` classes, `window.kit.ready/fail`, an `error` listener)
  around the agent body. The host accepts messages only from that frame's
  `contentWindow` and only `{type:"status",status:"ok"|"error"}` and
  `{type:"height",height}` (clamped 60–640px). Height comes from
  `document.body` observed with `ResizeObserver` (observing `<html>` does not
  work: it is pinned to the frame height). Error, or no `ok` within 3 s → the
  frame is removed and `alt` shows as text. Source never reaches
  `dangerouslySetInnerHTML`.
- **D5 Shaping.** A Revision snapshots each visual (with its id) into the
  Change payload's `RevisionSnapshot.visuals` and deletes it with the content;
  Undo re-creates the rows under the same ids so restored fences resolve.
  Older payloads without the key read as no visuals.
- **D6 Context.** Continuity strips ```visual fences from earlier passages; the
  tutor gets a `<lesson-visual>` block with title + alt, never source.
- **D7 Stub.** `services/stub_model.py` gives lesson 1 (only) a compound-interest
  visual; `[force-visual-probe]` swaps in a probe that tries to escape the
  sandbox. Later lessons are byte-identical to before.

## Not built

- No server-side render check (a headless browser at generation, as the
  prototype sketched); a broken visual degrades to its alt text in the client.
- One `kind`; `kit_version` 1. Changing the kit's API means a new version, kept
  side by side so stored visuals keep working.
