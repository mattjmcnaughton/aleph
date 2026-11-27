# PRD: Paths from linked Material

**Status:** first slice built, behind the `material` flag (dark; admins on).
**Design:** [TDD](../tdds/linked-material.md). Built on the exploration "Paths from
linked Material" (2026-11-26).

## Problem

A learner often already has the thing they want to learn from: an article, a docs
site, a talk, a podcast episode, a book. Today they can only name a Topic, and the
path Aleph builds ignores what they had in hand.

## What the learner can do

On New path, under the topic, a **Material** field takes up to 5 items:

- **A link** to an article, or to a docs site with "read the pages under this link
  too" (up to 30 pages, same host and path).
- **Pasted text**, including a transcript. A YouTube or podcast link opens a
  transcript box with copy instructions: Aleph does not fetch video or audio.
- **An AI summary** pasted from NotebookLM, ChatGPT, Claude or similar, for
  content Aleph cannot read itself, such as a book.

With Material added they choose a **Material mode**:

- **Teach this:** the path teaches the Material and nothing more. Short Material
  makes a short path (as few as 2 lessons).
- **Start from this:** the Material is the core; Aleph adds what comes before and
  after it at a normal path size.

Aleph reads the Material when they press Build. If an item cannot be read (a
paywall, a page that needs a browser, a dead link, a video link), no path is
created and that item is marked with the reason and a "Paste the text instead"
action.

## Out of scope for this slice

- A separate preview step that shows the captured pages before Build.
- Showing learners where a lesson came from, and giving the tutor the Material.
- Fetching transcripts, audio transcription, file uploads, book text.
- Carrying Material into a **Copy**.

## Success

Judged by reading real Teach this and Start from this paths built from the
exploration's examples (Constitution §9), before the flag is turned on.
