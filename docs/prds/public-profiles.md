# PRD — Public profiles

**Status:** Accepted 2026-11-24, built and shipped on 2026-11-24 (`public_profiles` defaults on; every profile starts off) · **Owner:** solo builder
**Companion to:** [Public profiles TDD](../tdds/public-profiles.md)
**Design:** Storybook `Screens/Public profiles`
([`public-profiles.stories.tsx`](../../src/aleph/web/frontend/src/stories/public-profiles.stories.tsx);
run `just storybook` and open *Screens → Public profiles*). Every screen below
is a story there, rendered by the real app.
**References:** [`CONTEXT.md`](../CONTEXT.md) · [`CONSTITUTION.md`](../../CONSTITUTION.md) ·
[`design.md`](../design.md)

## 1. Summary

A learner can turn on a **Public profile**: a page at `aleph.app/@handle` that
anyone can open **without signing in**. It shows the paths and Beats they chose
to share, plus their Daily streak and activity.

A visitor can **start their own** copy of a shared path or Beat. A copy carries
the original's **creation inputs** and nothing else: Topic, Level, Guidance and
Teaching style for a path, or Topic, Level and Guidance for a Beat. **It never
copies the outline, lessons, progress or Briefs.** The visitor lands on the
ordinary New path (or New beat) form, prefilled, and can change anything before
submitting. Aleph then drafts a new outline for them, the same way it does for any
path, so their path may differ from the original.

"Start this path" links to `/new?from=matt/<pathId>` (`/beats/new?from=…` for a
Beat). The form reads the inputs from the public profile endpoint and fills
itself in. The link carries a reference, never the inputs themselves, so
Guidance stays out of URLs, browser history and the sign-in redirect. The
create request also names its source, so the copy can be credited. Prefilling
the form is not new: a Learning summary **Suggestion** already opens New path
filled in, from search params.

## 2. Why

- **Showing what you are learning** is a reason to come back and a reason to
  share Aleph. A streak and a path on a page you can send someone does both.
- **Good paths travel through their inputs.** What made Matt's Rust path work is
  what he asked for ("I write Go at work…", "compare to Go where it helps"). A
  visitor gets the same benefit from those inputs, with an outline written
  around their own About you and Teaching style.
- **Copying inputs keeps the domain simple.** A copied outline would need rules
  for its lessons, for Changes applied to the original, and for credit. Copying
  inputs produces an ordinary new path with no ties to the original beyond a
  credit line.

## 3. What people see

| Frame | Who | What |
| ----- | --- | ---- |
| 1. Settings, off | Owner | *Settings → Public profile*, off by default. Nothing is public. |
| 2. Settings, on | Owner | A handle (`@matt`), Copy link, View as a visitor, and one checkbox per path and per Beat. "Daily streak and activity" is a separate checkbox. A note says exactly what visitors can see. |
| 3. Profile | Anyone | Name, handle, streak and the 7-week activity strip, shared paths (Learning, then Finished) and shared Beats. Signed-out visitors see a short invitation at the foot of the page. |
| 4. Shared path | Anyone | Title, Level, **How Matt started it** (the four creation inputs), Matt's outline with his progress, and one card: **Start this path** (signed in) or **Sign in to start this path**. |
| 5. Shared Beat | Anyone | Topic, Level, cadence, the Brief list (each Brief readable), and **Deploy this Beat**. |
| 6. New path, prefilled | Signed-in visitor | The real New path form, filled with Matt's inputs. Submitting creates the visitor's own path. |
| 7. The copy | Visitor | An ordinary new path, drafting its outline, with a quiet "Started from Matt McNaughton's path". |
| 8. Not here | Anyone | One page for a private profile, an unknown handle and an unshared path. |

## 4. Product decisions

1. **Opt-in twice.** The profile is off by default, and every path and Beat is
   unshared by default. Sharing is per item. Turning the profile off hides
   everything at once, keeping each item's choice for next time.
2. **What a visitor sees of a path:** title, Level, Topic, Guidance, Teaching
   style, unit and lesson titles, and the owner's progress through them. **Never**
   Read passages, Quick checks or answers, Visuals, tutor or shaping
   conversations, Change history, web search settings, or About you.
3. **Guidance and Teaching style are public on a shared path,** because they are
   what a copy carries. The settings note says so in plain words before the owner
   shares anything. (Open question Q1: a per-path toggle to hide them.)
4. **A copy is a new path from the same creation inputs.** The outline is
   generated fresh by the outline agent for the copier. Nothing links the copy to
   the original's structure, and later changes to either path do not affect the
   other. Lessons are written with the copier's About you. Web search starts off,
   as on any new path.
5. **Nothing is created without the copier's submit** (Constitution §2). "Start
   this path" only opens the prefilled form, like a Suggestion. The path-creation
   rate limit applies as usual.
6. **Credit, not a link.** A copied path shows "Started from {name}'s path" while
   the source is still shared, and nothing once it isn't. The owner's shared path
   shows how many copies were started (open question Q2).
7. **A shared Beat shows its Briefs in full,** with their Sources. Briefs are
   cited reports and never change after they are published (CONTEXT: *Brief*),
   so publishing them is low-risk. Reading a Brief as a visitor never marks it read.
8. **A copied Beat** opens New beat prefilled with Topic, Level and Guidance; the
   visitor picks their own Anchor day, and Briefs start at #1.
9. **Absence looks the same.** A private profile, an unknown handle and an
   unshared or deleted item all give one "Nothing to see here" page, so a URL
   never confirms that something exists.
10. **The handle is chosen by the learner** when they first turn the profile on:
    3–30 characters, lowercase letters, digits and `-`, unique. The sign-in
    username is not used, because it is derived from the identity provider. In
    the first slice a handle can't be changed or released, so no one else can
    take it over along with the old links (TDD D8).
11. **Sharing is live,** and the settings page says so before anything is
    shared. A shared path shows its current Teaching style, and a shared Beat
    publishes its future Briefs. The activity strip covers every path, private
    ones included, so it has its own checkbox. Lesson titles are generated and
    can echo About you or Guidance, so "View as a visitor" is the preview.

## 5. Success

- **Owner adoption:** share of active learners with a public profile on.
- **Copies started:** paths created from a shared path (by source), and the share
  of those that reach Activation (CONTEXT: *Activated learner*).
- **Signed-out visitors who sign up** after viewing a profile.

Workflow IDs for tests: **PP1** turn on and share, **PP2** view a profile signed
out, **PP3** start a path from a shared path, **PP4** deploy a Beat from a shared
Beat, **PP5** private or unknown reads as absent.

## 6. Out of scope

Following, feeds or notifications about other learners; comments or reactions;
search or a directory of profiles; copying an outline or lessons; sharing a
single lesson or Brief on its own; avatars or uploaded photos; per-IP rate
limiting of public reads (owner's call); search-engine indexing (pages send
`noindex` until decided otherwise).

## 7. Vocabulary (added to CONTEXT.md)

- **Public profile**: a learner's opt-in public page, readable without signing in.
- **Handle**: the learner-chosen name in a Public profile's URL; distinct from the
  sign-in username.
- **Shared** (path or Beat): one the owner has picked to appear on their Public
  profile. Visible only while the profile is on.
- **Copy**: a new path or Beat started from a shared one's creation inputs. Never
  its outline, lessons, progress or Briefs. Its **source** is credited.

## 8. Open questions

- **Q1.** Should an owner be able to share a path but hide its Guidance and
  Teaching style? A copy would then carry only Topic and Level. Recommendation:
  not in the first slice; the settings note is enough.
- **Q2.** Show copy counts publicly ("14 people started a copy"), only to the
  owner, or not at all? Recommendation: only to the owner at first.
- **Q3.** Should Finished paths and the activity strip be on by default once the
  profile is on? The storyboard assumes yes for the strip.
