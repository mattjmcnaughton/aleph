# TDD: Papers as Material

**Status:** proposal (2026-11-27). Not built. *Decided here* marks a choice the
author made that Matt can overturn; **Needs Matt** marks one he must make.
**PRD:** [papers-material](../prds/papers-material.md). Builds on the
[Linked Material TDD](linked-material.md) and the Material draft in the
[Placement check TDD](placement-check.md) D1–D3 (local branch
`explore/placement-check`, not yet on `main`). Vocabulary:
[CONTEXT.md](../CONTEXT.md) *Material*, *Material snapshot*, *Material mode*,
*Grounding plan*, *Guidance*.

## Proposed vocabulary

To add to CONTEXT.md when slice 1 lands:

- **Paper**: a Material item that is a research paper, read from an arXiv link
  or an uploaded PDF. Not "document", "article" or "upload".
- **Core concept**: one idea a paper introduces or depends on to make its
  argument, named by the paper's digest with the sections that teach it and
  the concepts it builds on.
- **Prep lesson**: a lesson before a paper's lessons that teaches one
  prerequisite the paper assumes, grounded in no section.

## Prototype results

Measured on this orb (2 vCPU, 4 GB) on *Attention Is All You Need* (15 pages)
and *LoRA* (26 pages). Scripts are in `.amp/in/orb-backup/paperproto/`, outside
the repository.

| Reader | Time | Peak RSS | Headings | Equations | Licence |
| --- | --- | --- | --- | --- | --- |
| arXiv HTML + BeautifulSoup | 0.3 s / 0.6 s | 27 / 35 MB | 25 / 42, correct | 103 / 357 as LaTeX | n/a (bs4 MIT) |
| pypdf | 1.1 s / 4.4 s | 72 / 114 MB | none; LoRA table rows read as headings | flattened: `softmax( QK T √dk )V` | BSD |
| pymupdf4llm | 8 s / 49 s | 391 / 416 MB | 26 / 40, correct | flattened | **AGPL** |
| docling, no formula model | 67–78 s (Attention) | 2.0 GB | 27, correct | `<!-- formula-not-decoded -->` | MIT |
| docling, formula model | 229 s (Attention) | 2.8 GB | 27 | 6 recovered as LaTeX | MIT |

The docling install is 1.5 GB of Python packages plus model weights, against a
234 MB install for the light readers.

Five more arXiv IDs (ResNet, VAE, GPT-3, Bahdanau attention, Mistral 7B) all
have an HTML version (`200`), and neither sampled HTML page has a LaTeXML
`ltx_ERROR` node. The e-print endpoint returns a gzip of LaTeX source.

Hostile PDFs, read with pypdf in a child process with `RLIMIT_AS` 768 MB,
`RLIMIT_CPU` 30 s, `RLIMIT_FSIZE` 0 and a 45 s timeout:

- a 3 MB PDF whose one content stream inflates to about 1 GB failed in 0.9 s
  on pypdf's own decompression limit (`LimitReachedError`);
- a 10,000-page PDF was refused on page count in 2.0 s, before any text was
  extracted.

## Decisions

- **D1. arXiv HTML first, then the PDF; never the LaTeX source.** *Decided
  here.* An arXiv link or bare ID is normalised to `<id>v<n>` (the latest
  version when none is given, read from the export API). Aleph fetches
  `https://arxiv.org/html/<id>v<n>` and converts LaTeXML to Markdown: headings
  from `h1`–`h4`, each `<math>` as its `alttext` in `$…$`, display equations
  (`table.ltx_equation`) as `$$…$$` paragraphs, and each figure or table as a
  `[Figure n: caption]` line. The bibliography is dropped; appendices are kept
  with their headings. When HTML is `404`, or more than 5% of its math nodes
  are `ltx_ERROR`, Aleph reads `https://arxiv.org/pdf/<id>v<n>` through the PDF
  reader (D5). Reading the e-print source is rejected: it is an untrusted tar
  of TeX with arbitrary macros and many files, and arXiv's HTML is already its
  own conversion of that source.
- **D2. Aleph fetches arXiv itself, on fixed hosts only.** Linked Material D2
  says Aleph's servers never fetch a learner-supplied URL. arXiv is a bounded
  exception: the learner supplies an ID that must match
  `^\d{4}\.\d{4,5}(v\d+)?$` (or an old-style `archive/1234567` ID), and Aleph
  builds the URL on `arxiv.org` or `export.arxiv.org` from it. Redirects off
  those hosts fail the item. Exa `/contents` is not used for papers because it
  returns plain text without the equations. Requests send an identifying
  `User-Agent` and stay within arXiv's one-request-per-3-seconds API guidance,
  through a process-wide limiter. Dev, CI and e2e use a stub reader, as
  Linked Material does.
- **D3. Papers ride the Material draft.** Placement check D1 adds
  `material_drafts`. Papers need two additive changes to it, sent to that
  thread on 2026-11-27:
  - each item has a `state` (`reading`, `ready` or `failed`) and a `reason`
    in the draft's small `item_states` column (Placement check D3), so a
    failed paper is marked on its own item. `items` holds only the sections
    and digest. The draft is
    `ready` when every item is ready, and `failed` when any item failed;
  - `POST /api/v1/material-drafts` also accepts `multipart/form-data`: the
    `material` list as a JSON part, and one file part per
    `{kind: "pdf", file: "<part name>"}` item.

  arXiv items are read inside the request (under 2 seconds in the prototype)
  and are `ready` at once. PDF items are `reading`: the route writes the bytes
  to a temp file, spawns the extraction task, and returns. The client polls
  `GET /material-drafts/{id}`. A machine restart loses the temp file, so the
  stale `reading` draft reads as `failed` and the learner adds the file again
  (Constitution §4: an interrupted read is a visible failure, never an empty
  snapshot). This is why papers need no object storage (D9).
- **D4. Limits.** *Decided here.* Per PDF: 20 MB, 50 pages, `%PDF-` magic
  bytes, not encrypted. Per paper: 150,000 extracted characters, after the
  bibliography is dropped. Per path: today's 5 items and 400,000 characters.
  Reasons added to `MaterialFailure`: `too_many_pages`, `encrypted`,
  `not_a_pdf`, `no_text_layer`. The size limit is enforced while the upload
  streams, before the whole body is in memory. One PDF extraction runs at a time per machine (a semaphore),
  so a burst of uploads queues rather than exhausting the 512 MB app VM.
- **D5. PDF reader: Mistral OCR through OpenRouter.** Matt chose option A on
  2026-11-27. Every PDF is first
  checked by pypdf in a limited child process (D6): magic bytes, encryption,
  page count. Then one of these reads the text:

  | Option | Quality | Security | Cost |
  | --- | --- | --- | --- |
  | **A. Mistral OCR through OpenRouter** (`file-parser`, engine `mistral-ocr`). *Recommended.* | Markdown with headings and LaTeX equations; reads scans. | The parsing happens at Mistral, never on Aleph's machine; the bytes go to a vendor that already receives the Material text. | $2 per 1,000 pages: about $0.03–0.12 per paper. Billed to the existing OpenRouter key, so no new account. |
  | B. docling on a separate Fly app | Headings right; equations only with the 229 s formula model. | Strongest local isolation (D6), but a second app to run. | Needs about 4 GB: a `performance-2x` Machine at about $0.09 an hour for 2–4 minutes is about $0.01 per paper, plus a 2 GB image. Needs the file passed in, so object storage or a private push. |
  | C. pypdf in the child process | Plain text, no headings, equations flattened. Lessons would teach from mangled math. | In the app machine, limited by rlimits only. | Free. |

  pymupdf4llm is ruled out: it is AGPL, and Aleph is an MIT-licensed network
  service. OpenRouter's free `cloudflare-ai` engine was not measured; it does
  not read scans, and its equation output is unknown. It is worth one test
  before choosing A.
- **D6. Sandboxing untrusted PDFs.** *Decided here.* The pre-check (and
  option C, if chosen) runs as `python -I -c <reader>` in a child process with
  an empty environment, so no secret reaches it, `cwd=/`, `RLIMIT_AS`,
  `RLIMIT_CPU` 30 s, `RLIMIT_FSIZE` 0, `RLIMIT_NOFILE` 16, a 45 s wall timeout,
  and output capped at the per-paper limit. `RLIMIT_AS` was 768 MB in the
  prototype; on the 512 MB production VM it must be about 300 MB, which needs
  measuring against the 114 MB peak RSS seen above. The child keeps the app's network: denying it needs a network
  namespace, and the image runs as the unprivileged `app` user (`Dockerfile`),
  so that would need extra privileges (not tried on Fly). pypdf makes no
  network calls, so this is accepted for the pre-check. Option B goes further: a separate Fly app created with its own
  `--network` (a custom 6PN, so it cannot reach Neon or the app), a network
  policy that denies all egress, and one Machine per job with `auto_destroy`.
  gVisor, nsjail and bubblewrap were considered and rejected: they need
  namespace privileges the unprivileged app user lacks (not tried on Fly), and add a binary to the
  image for a risk the rlimits already bound.
- **D7. Core concepts before the outline.** *Decided here.* When a paper is
  read, one model call writes its **digest** and stores it on the draft item
  before the item turns `ready`:

  ```json
  {"concepts": [{"id": "c3", "name": "Scaled dot-product attention",
                 "sections": [7, 8], "builds_on": ["c1"]}],
   "prerequisites": [{"name": "Softmax", "why": "used in eq. 1"}]}
  ```

  `builds_on` must name concepts in the same digest and must not form a cycle;
  `sections` must name the paper's own sections. Both are checked in
  `domains/material.py` with `ModelRetry`, like the grounding plan. The digest
  sees the paper whole up to 40,000 characters, otherwise its headings and each
  section's first 500 characters, as the outline does today. The digest is
  copied to `materials.digest jsonb` when Build claims the draft.
- **D8. Incremental lessons and prep lessons.** *Decided here.* For a path
  with papers, the outline prompt gets the digest beside the sections, and the
  outline names, per lesson, `concepts` (digest ids) or, for a prep lesson,
  `prerequisite` (a digest prerequisite name). `validate_outline` adds three
  rules:
  - in Teach this, every concept is taught by some lesson;
  - a lesson teaching a concept comes after the lessons teaching every concept
    it `builds_on`;
  - a prep lesson names a prerequisite from a digest, names no section, comes
    before every paper lesson, and there are at most 4 of them, or 1 when
    Guidance says nothing about familiarity (the outline decides that; the cap
    is the only hard rule).

  Today's Teach this rule that every lesson names a section exempts prep
  lessons. Guidance stays the one place the learner states their familiarity:
  no new field. A Placement check result, when the learner took one, is one
  more outline input that design owns. Teach this sizing (Linked Material D6)
  is replaced for papers by the concept count: about one lesson per concept,
  2–30 lessons. Prep lessons are not persisted as a kind: their lesson rows
  have no `material_sections`, so their prompts get no Material, as a Start
  from this extension lesson does today.
- **D9. No object storage now.** *Decided here.* The slices store only text,
  in Postgres, as Linked Material does: a paper is 35,000–77,000 characters in
  the prototype, so 1,000 papers are well under 0.5 GB. Uploaded PDFs live in a
  temp file only until they are read (D3). Object storage becomes necessary
  for slice 4 (figure images), or for option B (passing the PDF to the
  extractor Machine). When it does, the choice is:

  | Store | Storage | Requests | Egress | Free each month |
  | --- | --- | --- | --- | --- |
  | Tigris (`fly storage create`) | $0.02/GB | $0.005 per 1,000 writes, $0.0005 per 1,000 reads | free | 5 GB, 10,000 writes, 100,000 reads |
  | Cloudflare R2 | $0.015/GB | $4.50 per million writes, $0.36 per million reads | free | 10 GB, 1M writes, 10M reads |
  | AWS S3 Standard (us-east-1) | $0.023/GB | $0.005 per 1,000 writes, $0.0004 per 1,000 reads | $0.09/GB after 100 GB | 5 GB for 12 months only |

  At Aleph's scale (figures for a few hundred papers, a few hundred MB) each of
  Tigris and R2 costs nothing. Tigris is the recommendation: it is billed through Fly,
  set up with one `flyctl` command that writes the credentials as app secrets,
  and needs no new vendor account. R2 is the cheaper choice past its free tier.
  Prices are from the vendors' pages, read 2026-11-27.
- **D10. Equations render with KaTeX.** *Decided here.* Lessons on papers will
  write LaTeX. The web client adds `remark-math` and `rehype-katex`
  (thin wrappers whose last releases were 2023 and 2024) plus `katex` as a
  direct dependency, so the parser that handles untrusted LaTeX (last release
  2026-10) gets updates without waiting on the wrapper, to
  `components/markdown.tsx`, with KaTeX's default `trust: false` (no `\href`,
  `\url` or `\htmlData`), `maxExpand` and `maxSize` limits, and errors rendered
  as the source text in red. KaTeX emits its own HTML; it does not turn on raw
  HTML for the Markdown, so the no-`rehype-raw` rule stands. The KaTeX CSS and
  fonts are about 300 KB, loaded only when a passage contains `$`. The lesson
  and Quick check prompts are told to write inline math as `$…$` and display
  math as `$$…$$`. Because the renderer is shared, every surface that uses
  `Markdown` (Read passage, Quick check explanation, Briefs, the tutor and
  shaping rails) gets math, not only papers. Quick check options are rendered
  as plain text today (`routes/lessons.$lessonId.tsx`), so they need an inline
  Markdown render to show math. Matt agreed on 2026-11-27 that math should
  render everywhere; currency like `$5` needs `singleDollarTextMath: false` or the
  prompt rule, to be settled in slice 1's tests.
- **D11. Figures as captions first.** *Decided here.* Slices 1–3 keep
  `[Figure n: caption]` lines in the section text, so a lesson can say what a
  figure shows, and redraw it as Mermaid or a Visual when that helps. Slice 4
  copies arXiv HTML figure images (`arxiv.org/html/<id>/x1.png`) or OCR'd
  images into object storage and serves them from Aleph's own origin.
  Hotlinking arXiv is rejected: it would need an `img-src` exception and send
  learners' IP addresses to a third party.
- **D12. The hook before the outline.** Papers write sections and a digest to
  the draft, then set the item `ready`. Nothing between `ready` and Build
  belongs to papers: the Placement check runs there, reads `state = ready`,
  sections and (optionally) the digest, and does no PDF work. The outline reads
  the snapshot and digest after Build claims the draft, so a Placement check
  result reaches it with no change to papers.

## Schema

Specified here; the Space writes the migration.

- `materials.kind` gains `arxiv` and `pdf` (a text column checked by the DTO; no
  migration).
- `materials.digest jsonb NULL` (D7), and `materials.source_url text NULL`: the
  arXiv HTML or PDF URL actually read, for an arXiv item.
- `lessons.material_concepts text[] NULL` (D8): the digest concept ids a lesson
  teaches. Prep lessons store none.
- `material_drafts` as in Placement check D1 and D3: per-item `state` and
  `reason` in `item_states`, sections and digest in `items` (up to about
  400,000 characters). Build copies `items` into `materials` and
  `material_sections` under `SELECT … FOR UPDATE`, then clears `items` and
  `placement` in the same transaction; the hourly tick clears them on expired
  drafts, and the slim row is deleted 48 hours after creation so the daily
  draft limit still counts it. A PDF extraction that finishes after its draft
  expired or was claimed writes nothing: its update requires the item still
  `reading`, `consumed_at` null and `expires_at > now()` (the tick clears
  expired drafts without setting `consumed_at`). Build claims only a draft
  whose `state` is `ready`.

Bulky text stays off the hot paths. Postgres compresses any value over about
2 KB and stores it out of line in the table's TOAST table, leaving a small
pointer in the row, and reads it only when a query selects that column. So:
the section text lives only in `material_sections`, which is read by
`(path_id, position)` for one lesson at a time; `materials.digest` is
`deferred()` in the ORM so the path detail's `list_for_path` doesn't load it;
and the draft poll (`GET /material-drafts/{id}`) selects only `state` and
`item_states`; a jsonb projection of `items` would still decompress all of it.

## Size and slices

Each slice is one PR behind the `material` flag, which stays admin-only.

| Slice | Content | Rough size |
| --- | --- | --- |
| 1. arXiv papers | ID parsing, the arXiv reader and its stub, LaTeXML to Markdown, the digest agent and its validator, concept order and prep lessons in the outline validator, KaTeX in the renderer, the arXiv item in the Material field. Read inside the request, so it can land before or after the Material draft. | ~1,200 lines with tests; 1 migration (digest, concepts) |
| 2. PDF upload | Multipart on the draft, the streaming size limit, the limited pre-check child, the chosen reader (D5), `reading` items and polling, the upload control. Needs the Material draft on `main`. | ~900 lines; no migration beyond the draft's |
| 3. arXiv PDF fallback | arXiv without HTML goes through slice 2's reader. | ~150 lines |
| 4. Figures | Object storage (D9), figure images copied at read time, served from Aleph, placed in lessons. | ~800 lines, a new secret and bucket; deferred until slices 1–3 are judged |

## Verification

- `tests/unit/test_material.py`: arXiv ID parsing (new and old style, versions,
  hostile hosts in `/abs/` links), LaTeXML to Markdown on two committed HTML
  fixtures, the digest validator (unknown sections, cycles), the outline rules
  for concept order and prep lessons, each with a counterexample.
- `tests/unit/test_pdf_check.py`: the limited child refuses the deflate bomb,
  the 10,000-page PDF, an encrypted PDF and a non-PDF, each with its reason,
  inside the time limit. Fixtures are generated in the test, as in the
  prototype's `sandbox_probe.py`.
- `tests/integration/test_material_api.py`: a draft with an arXiv item and a
  PDF item goes `reading` then `ready` through the real API and Postgres with
  stub readers; a failed PDF marks only its item; Build copies the digest.
- `src/components/markdown.test.tsx`: inline and display math render; `\href`
  with `javascript:` renders as text; a `$5` price stays text.
- Storybook: a lesson with equations, and the Material field with a reading
  and a failed paper.
- Quality: Matt reads real paths (PRD §Success). No test claims it.
