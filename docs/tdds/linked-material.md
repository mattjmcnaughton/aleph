# TDD: Paths from linked Material

**PRD:** [linked-material](../prds/linked-material.md). Vocabulary:
[CONTEXT.md](../CONTEXT.md) *Material*, *Material snapshot*, *Material mode*,
*Grounding plan*.

## Decisions

- **D1. Read at create, no draft state.** `POST /api/v1/paths` reads the Material
  synchronously before inserting the path. A failed item is a `422` with a reason
  per item; no path or snapshot is written (Constitution §4). The exploration's
  preview step and its expiring "material draft" are deferred.
- **D2. Exa reads links.** `ExaMaterialReader` calls Exa `/contents` (with
  `subpages` for docs), so Aleph's servers never fetch a learner-supplied URL.
  Crawled pages outside the root's host and path prefix are dropped
  (`domains.material.in_crawl_scope`). Exa errors are `RetrievalUnavailableError`
  → `503`, never an empty snapshot. Dev, CI and e2e use `StubMaterialReader`
  (selected when `MODEL_DEFAULT=stub`).
- **D3. Sections, not embeddings.** Each item's text is cut along headings, then
  5-minute transcript windows, then whole, with any section over 8,000 characters
  split on paragraphs (`domains.material.split_sections`). Sections are numbered
  1..N across the path.
- **D4. Grounding plan on the outline.** `LessonOutline.material_sections` names
  section ids. `validate_outline` rejects ids the path does not own, any ids on a
  path without Material, and, in Teach this, a lesson naming none (`ModelRetry`).
  Persisted on `lessons.material_sections`.
- **D5. Prompts.** The outline sees every section whole when the snapshot is at
  most 40,000 characters, otherwise each section's first 500. A lesson sees only
  its mapped sections, up to 32,000 characters. Both ride a delimited
  `<material>` block, neutralized against its own tags, as untrusted data
  (`agents/_material.py`).
- **D6. Sizing.** Teach this replaces the usual sizing paragraph with a target of
  about one lesson per 6,000 characters (2–30), about five per unit
  (`domains.material.teach_size`). The hard caps in `OutlineCaps` are unchanged.
- **D7. Frozen, owned, not copied.** The snapshot is written in the path's create
  transaction and cascades with it. Nothing re-reads it. A Copy does not carry
  Material; the copier attaches their own.

## Schema (`0026_linked_material`)

- `materials(id, path_id → paths CASCADE, position, kind, title, url)`,
  unique `(path_id, position)`.
- `material_sections(id, material_id → materials CASCADE, path_id → paths
  CASCADE, position, title, text)`, unique `(path_id, position)`.
- `paths.material_mode text NULL` with a check for `teach`/`start`.
- `lessons.material_sections integer[] NULL`.

Additive and independent of `0026`; the merge queue sets its `down_revision`.

## Limits

5 items; 200,000 characters per pasted item; 30 crawled pages per docs link;
400,000 characters per path; an item under 200 characters is `empty`.

## Verification

- `tests/unit/test_material.py`: sectioning, crawl scope, sizing, the grounding
  validator, delimiting, per-item failures, the Exa reader over a mock transport,
  and the stub following a grounding plan.
- `tests/integration/test_material_api.py`: create through the real API and
  Postgres stores the snapshot, grounds every lesson, and sends a lesson only its
  sections; an unreadable item creates nothing; the flag gates it.
- `src/app/linked-material.test.tsx`: the request body, the transcript box, and a
  failed item shown on that item.
- Storybook: *Screens/Linked Material*.
