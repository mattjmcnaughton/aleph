# PRD: Papers as Material

**Status:** proposal (2026-11-27). Not built.
**Design:** [TDD](../tdds/papers-material.md). Builds on
[Linked Material](linked-material.md) (v1.56.0, `material` flag) and on the
Material draft from the [Placement check TDD](../tdds/placement-check.md)
(D1–D3, local branch `explore/placement-check`, not yet on `main`).

## Problem

A learner who wants to understand a research paper has to read it cold, or
paste it into a chat tool. They want what Aleph does for a topic: a path that
teaches the paper's core concepts one at a time, starting from what they
already know. Linked Material reads articles and docs, but it can't read a
PDF, and an arXiv link read through Exa loses the equations and the paper's
section structure.

## What the learner can do

On New path, the **Material** field takes papers alongside today's items, up to
5 items in total:

- **An arXiv link** (`arxiv.org/abs/…`, `/pdf/…` or `/html/…`, or a bare ID
  like `2106.09685`). Aleph reads arXiv's own HTML version of the paper, which
  keeps its sections and equations. If arXiv has no HTML version, Aleph reads
  the PDF instead.
- **A PDF upload**: up to 20 MB and 60 pages, for papers that aren't on arXiv.
  A scanned PDF with no text layer is read too, if Matt approves the paid
  reader (TDD D5).

Material mode works as it does today. For papers the learner usually picks
**Teach this**: the path teaches the paper's core concepts, in the order the
concepts depend on each other, rather than the paper's section order.

The learner writes their familiarity in **Guidance**, as they do today, for
example "I know linear algebra and how transformers work, but not
fine-tuning." Aleph adds short **prep lessons** before the paper's lessons for
prerequisites the paper assumes and the learner says they lack. With no
Guidance about familiarity, Aleph adds at most one orientation lesson, as Teach
this does today.

Reading a paper takes a few seconds for an arXiv link, and up to a couple of
minutes for an uploaded PDF. The New path screen shows "Reading…" on that item
until it is done. If an item fails, it is marked with the reason, as today:
too many pages, password-protected, not a PDF, or no text Aleph could read.

Lessons show equations as typeset math, and refer to a paper's figures by
number and caption ("Figure 2 shows…"). Lessons don't show the paper's own
figure images in the first slices.

## The step before the outline

After every item is read and before the outline is built, the learner may take
the **Placement check** (a separate design, see its PRD). It reads the papers'
sections and core concepts as they are stored on the Material draft. This PRD
doesn't design it. Papers only guarantee that, when the draft is ready, each
paper has its sections and a list of core concepts and prerequisites.

## Out of scope

- The paper's figure images in lessons (TDD slice 4, which needs object storage).
- Reading LaTeX source from arXiv, and other preprint servers (bioRxiv, SSRN, ACL
  Anthology). A PDF downloaded from them can be uploaded.
- Keeping the uploaded PDF. Aleph keeps the extracted text and discards the
  file.
- Citations that link a lesson back to a page of the paper, and giving the tutor
  the paper. Linked Material defers both too.
- Carrying papers into a **Copy**.

## Success

Judged by reading real paths (Constitution §9) built from at least three papers
Matt picks, both with and without familiarity Guidance, before the flag is
turned on for anyone else. A good path teaches every core concept the paper
introduces, never teaches a concept before the concepts it depends on, and
leaves out prep lessons the Guidance says Matt doesn't need.
