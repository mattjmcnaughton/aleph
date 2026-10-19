// The shaping conversation's non-streaming wire seam (AL-330; Phase 2B TDD §6):
// the thread read + clear routes, their types, and the one query identity the
// shaping rail reads. The streamed send lives in `lib/tutor-stream.ts` with its
// 2A sibling, for that module's reason — it must read the body progressively,
// and there is one reader for both threads.
//
// **Why this is a separate module from `lib/tutor.ts`.** The two threads are
// separate by design (PRD §5.8: "the in-lesson rail never shows shaping turns,
// and vice versa"), and separate files are the cheapest way to make that
// structural rather than a rule someone has to remember: nothing here reaches
// into the 2A conversation's key, and nothing there can reach into this one.
//
// The thread is ordinary TanStack Query state, not a poll target (TDD §8) — the
// same posture 2A takes, for the same reason: a reply is request-scoped and
// arrives on its own stream. Applying a proposal is what invalidates the *path*
// outline query (AL-331), and that is a different query entirely.

import { queryOptions, skipToken } from "@tanstack/react-query";
import { ApiError, apiFetch, apiV1Path } from "./api";
import type { PathDetail, PathLesson, PathUnit } from "./api";
import type { TutorRole } from "./tutor";
import type { AddLessonsOperation, Proposal, ProposalOperation } from "./tutor-stream";

/**
 * A proposal's standing in the thread (TDD §4). **Derived server-side, never
 * stored** — *applied* when a live `path_changes` row references the message,
 * *undone* when that row is undone, *superseded* when a later proposal was
 * applied first and re-validation now fails, else *pending*. The client renders
 * it and never computes it: the same rule that keeps apply's staleness check on
 * the server keeps this off the client.
 */
export type ProposalResolution = "pending" | "applied" | "undone" | "superseded";

/** A proposal as a *read* thread carries it: the payload plus its resolution. */
export interface MessageProposal extends Proposal {
  resolution: ProposalResolution;
}

/**
 * One message in the shaping thread. Note what is absent next to
 * `ConversationMessage`: no `lesson_id`/`lesson_title`. A shaping turn is about
 * the path, so there is no lesson to record — the shape of the DTO is the shape
 * of the scope.
 */
export interface ShapingMessage {
  id: string;
  role: TutorRole;
  content: string;
  /** Set only on a tutor message that produced a Proposal (AL-331 renders it). */
  proposal: MessageProposal | null;
  created_at: string;
}

/** `GET /api/v1/paths/{id}/shaping/conversation` — object-wrapped, oldest first. */
export interface ShapingConversation {
  messages: ShapingMessage[];
}

/**
 * Which of the two operation shapes this is (D1). The wire union is **untagged**
 * — `agents/shaper.py` discriminates it structurally and so does this — so the
 * test is a field that only one shape has, not a `kind` string nobody sends.
 */
export function isAddLessons(operation: ProposalOperation): operation is AddLessonsOperation {
  return "lessons" in operation;
}

/** The whole shaping thread for a path. `200 {messages: []}` when none exists. */
export function getShapingConversation(pathId: string): Promise<ShapingConversation> {
  return apiFetch<ShapingConversation>(apiV1Path(`/paths/${pathId}/shaping/conversation`));
}

/**
 * **New conversation** (PRD §5.8): drop the shaping thread for this path. `204`
 * and idempotent. It does **not** touch the change history — a Change belongs to
 * the path, not to the conversation that proposed it (TDD D3, `SET NULL` on the
 * message FK), which is exactly why clearing a thread is safe to offer. Still
 * destructive and not undoable, so the caller MUST confirm first.
 */
export function clearShapingConversation(pathId: string): Promise<void> {
  return apiFetch<void>(apiV1Path(`/paths/${pathId}/shaping/conversation`), { method: "DELETE" });
}

/**
 * TanStack query key for one path's shaping thread. Its own namespace under
 * `"shaping"`, deliberately *not* a branch of `["tutor", …]` or of
 * `["paths", …]`: clearing one thread must never invalidate the other, and
 * marking a lesson complete (which invalidates the whole `paths` prefix) has no
 * business refetching a conversation.
 */
export function shapingConversationQueryKey(
  pathId: string,
): readonly ["shaping", "conversation", string] {
  return ["shaping", "conversation", pathId] as const;
}

/**
 * THE shaping conversation query — key + fetcher paired in one place (the house
 * rule from `sessionQueryOptions`). Pass `null` when the rail's entry point is
 * not rendered (flag off, or a path that is not `ready`): the query idles on
 * `skipToken`, so the gated surface costs no request at all. That is what makes
 * shipping dark actually dark.
 */
export function shapingConversationQueryOptions(pathId: string | null) {
  return queryOptions({
    queryKey: shapingConversationQueryKey(pathId ?? "idle"),
    queryFn: pathId === null ? skipToken : () => getShapingConversation(pathId),
  });
}

// --- The write half: Apply, Undo, Change history (AL-331, TDD §5.6–§5.8) -----
//
// Everything below is AL-331's. It is deliberately in this module and not a
// third one: Apply is what turns a **Proposal** (above) into a **Change**, and
// the two are one contract read from two ends. What stays out is anything with
// state in it — the card's per-message apply state and the sheet's open/closed
// live in `use-shaping-rail.ts`, and every decision *derived* from the wire is a
// pure function here, so it can be asserted without a render.

/** The edit a Change applied (`PathChangeKind`) — the closed vocabulary (D1). */
export type ChangeKind = "add_lessons" | "revise_lesson";

/** Whether a Change is in force (`PathChangeStatus`). Undo is a status, never a delete. */
export type ChangeStatus = "applied" | "undone";

/**
 * One applied **Change**, as `GET /paths/{id}/changes` reports it (`ChangeDTO`).
 *
 * A record, not a second edit surface (PRD §5.5) — there is no payload here, and
 * deliberately no "undoable" flag: whether undo is still open is the D2
 * engagement re-check run at undo time, because a learner can start a lesson
 * between this list rendering and the tap. The `409 engaged` is the enforcer.
 */
export interface Change {
  id: string;
  /** Plain language, the server's own words — the sheet never rewrites it. */
  summary: string;
  /** Plural: one Apply may carry both shapes, and lands as one Change. */
  kinds: ChangeKind[];
  status: ChangeStatus;
  applied_at: string;
  /** Non-null only on an undone Change. */
  undone_at: string | null;
}

/** `GET /api/v1/paths/{id}/changes` — object-wrapped, **newest first**. */
export interface ChangeHistory {
  changes: Change[];
}

/**
 * `POST /api/v1/messages/{id}/apply-proposal` → `200 {change, path}`.
 *
 * `path` is byte-for-byte what `GET /paths/{id}` returns, which is the whole
 * point of it being here: the caller drops it straight into the outline query's
 * cache and the rail's **ghost rows** become real (teal) rows in one round trip,
 * with no second fetch and no guess about what landed. Requesting it also kicks
 * Phase 1's prefetch driver server-side (TDD §5.6 step 4).
 */
export interface ApplyProposalResult {
  change: Change;
  path: PathDetail;
}

/**
 * Every `details.reason` a coded `409` can carry (`ShapingConflictReason`).
 *
 * Declared as a value, not just a type, because the client has to *recognise* a
 * reason it was told rather than trust it: an unknown string (a reason added
 * server-side after this build shipped) must fall back to the generic failure
 * copy instead of being funnelled into whichever card state a guess produced.
 */
export const SHAPING_CONFLICT_REASONS = [
  "already_applied",
  "already_undone",
  "not_applied",
  "not_latest",
  "path_cap_reached",
  "insert_position_taken",
  "revision_target_engaged",
  "title_conflict",
  "positions_shifted",
  "invalid_proposal",
  "target_generating",
  "engaged",
] as const;

export type ShapingConflictReason = (typeof SHAPING_CONFLICT_REASONS)[number];

/**
 * The five ways a refusal is *acted on* — `ShapingConflictReason`'s own grouping
 * (docs/api.md), which is the only thing the card and the sheet branch on:
 *
 * - `nothing_to_do` — the path is already in the state that was asked for.
 * - `ask_again` — the Proposal was valid when drafted and is not any more (D5).
 *   Re-asking is the way forward; retrying the same payload never is.
 * - `retry` — a prefetch holds the claim; the same tap works in a moment.
 * - `not_latest` — nothing is wrong with this Change; it is not on top of the
 *   stack (undo is LIFO).
 * - `closed` — the learner met the content, so this Change is permanent history.
 */
export type ConflictGroup = "nothing_to_do" | "ask_again" | "retry" | "not_latest" | "closed";

const CONFLICT_GROUPS: Record<ShapingConflictReason, ConflictGroup> = {
  already_applied: "nothing_to_do",
  already_undone: "nothing_to_do",
  not_applied: "nothing_to_do",
  not_latest: "not_latest",
  path_cap_reached: "ask_again",
  insert_position_taken: "ask_again",
  revision_target_engaged: "ask_again",
  title_conflict: "ask_again",
  positions_shifted: "ask_again",
  invalid_proposal: "ask_again",
  target_generating: "retry",
  engaged: "closed",
};

/**
 * The coded reason on a `409 conflict`, or null for anything else.
 *
 * Null covers three different failures on purpose — a `409` whose reason this
 * build does not know, an ordinary `500`, a dropped connection — because the
 * card treats them identically: say what the server said (or the transport copy)
 * and leave **Apply** where it is. Only a *recognised* reason may move the card
 * into a state, since every one of those states makes a claim about the path.
 */
export function conflictReasonOf(error: unknown): ShapingConflictReason | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const reason = (error.details as { reason?: unknown } | undefined)?.reason;
  return SHAPING_CONFLICT_REASONS.find((known) => known === reason) ?? null;
}

/**
 * The group of an already-extracted reason. Deliberately the *only* accessor:
 * every caller stores the reason first (the card renders it, the hook branches
 * on it), so an error-to-group shortcut would only ever re-derive what its
 * caller already had.
 */
export function conflictGroup(reason: ShapingConflictReason): ConflictGroup {
  return CONFLICT_GROUPS[reason];
}

/**
 * The proposal card's states (TDD §8), plus one the client owns alone.
 *
 * `dismissed` is **Not now**: pure UI dismissal, no request, nothing persisted
 * (PRD §5.4 — declining is never destructive and needs no confirmation). It is
 * therefore the one state that does not survive a reload, which is exactly
 * right: the Proposal is still `pending` server-side, so a learner who dismissed
 * it and came back finds the offer intact rather than silently spent.
 */
export type ProposalCardState =
  | "pending"
  | "applying"
  | "applied"
  | "stale"
  | "undone"
  | "dismissed";

export interface ProposalCardInput {
  /** The server-derived resolution off the thread read (TDD §4). */
  resolution: ProposalResolution;
  /** This card's Apply is in flight. */
  applying: boolean;
  /** This card's Apply answered with a Change — known before any refetch. */
  applied: boolean;
  /** The learner tapped **Not now**. */
  dismissed: boolean;
  /** The coded reason this card's last Apply was refused with, if any. */
  conflict: ShapingConflictReason | null;
}

/**
 * The card's state, from the five things that can be true about it.
 *
 * The order below *is* the precedence, and each step is a claim:
 *
 * 1. An apply in flight outranks everything — the learner is watching it.
 * 2. What actually happened to the path outranks what the learner asked for, so
 *    an applied (or undone) proposal never renders as dismissed.
 * 3. A refusal only moves the card when it says the path changed under it: the
 *    ask-again family renders `stale`, and the two nothing-to-do reasons settle
 *    the card into the state the server says it is already in.
 * 4. `target_generating` deliberately falls through to `pending` — nothing is
 *    wrong, the same tap works in a moment, and the card keeps **Apply**.
 */
export function proposalCardState({
  resolution,
  applying,
  applied,
  dismissed,
  conflict,
}: ProposalCardInput): ProposalCardState {
  if (applying) return "applying";
  if (applied || resolution === "applied" || conflict === "already_applied") return "applied";
  if (resolution === "undone" || conflict === "already_undone") return "undone";
  if (resolution === "superseded") return "stale";
  if (conflict !== null && CONFLICT_GROUPS[conflict] === "ask_again") return "stale";
  if (dismissed) return "dismissed";
  return "pending";
}

/**
 * The **cost line** (PRD §5.4: *"adds 2 lessons ≈ 10 min"*) — the plain
 * statement of scale a learner consents to.
 *
 * Derived from the payload rather than read off the agent's `summary`, because
 * the count and the minutes are facts about the operations: a summary that
 * disagreed with them would be the one thing on this card the learner could not
 * check. A Revision contributes no lessons and no minutes — it re-teaches a slot
 * the path already has — so it gets its own clause instead of inflating the
 * addition's.
 */
export function proposalCostLine(proposal: Proposal): string {
  let lessons = 0;
  let minutes = 0;
  let revisions = 0;
  for (const operation of proposal.operations) {
    if (isAddLessons(operation)) {
      lessons += operation.lessons.length;
      minutes += operation.estimated_minutes;
    } else {
      revisions += 1;
    }
  }
  const parts: string[] = [];
  if (lessons > 0) parts.push(`Adds ${lessons} ${plural(lessons, "lesson")} ≈ ${minutes} min`);
  if (revisions > 0) parts.push(`Revises ${revisions} ${plural(revisions, "lesson")}`);
  return parts.join(" · ");
}

function plural(count: number, noun: string): string {
  return count === 1 ? noun : `${noun}s`;
}

// --- Ghost rows (TDD D14: merged client-side, no preview endpoint) -----------

/** A proposed lesson previewed in place — iris, not teal, and not yet real. */
export interface GhostLessonRow {
  kind: "ghost";
  /** Stable within one merge; ghosts have no id until a Change creates them. */
  key: string;
  title: string;
}

/** A real path row, carrying whether a pending Revision names it. */
export interface RealLessonRow {
  kind: "real";
  lesson: PathLesson;
  /** A pending **Revision** targets this lesson — the "will be revised" marker. */
  revising: boolean;
}

export type OutlineRow = GhostLessonRow | RealLessonRow;

/** A unit as the path rail draws it, real or proposed. */
export interface OutlineUnitView {
  id: string;
  title: string;
  /** True for a unit an Addition proposes creating. */
  ghost: boolean;
  lessons: OutlineRow[];
}

/**
 * Merge a pending Proposal into the outline for preview — the whole of "ghost
 * rows" (D14: the payload is already the full statement of the edit, so a server
 * preview endpoint would be a second implementation of it).
 *
 * **Positions resolve against the payload's own snapshot.** An
 * `insert_at_position` is a `position_in_path` in the path as it stood when the
 * Proposal was drafted, and every real row still carries that number — so each
 * operation is placed by *finding* its slot rather than by counting rows that
 * earlier operations have already displaced. Two Additions naming the same slot
 * therefore preview in payload order, which is the order Apply will insert them.
 *
 * It is a **preview and only a preview**: an operation whose slot is no longer
 * on the path (a Change landed in between) degrades to what it can honestly
 * draw rather than throwing or inventing one — an Addition previews at the end
 * of the outline, which is where Apply puts a position past the end of the path,
 * and a Revision naming a lesson that is gone marks nothing at all. Apply
 * re-validates against live state and answers a coded `409` (D5) — that is where
 * staleness is decided, never here.
 */
export function mergeProposalIntoOutline(
  units: readonly PathUnit[],
  proposal: Proposal | null,
): OutlineUnitView[] {
  const revising = new Set<string>();
  if (proposal) {
    for (const operation of proposal.operations) {
      if (!isAddLessons(operation)) revising.add(operation.lesson_id);
    }
  }

  const merged: OutlineUnitView[] = units.map((unit) => ({
    id: unit.id,
    title: unit.title,
    ghost: false,
    lessons: unit.lessons.map(
      (lesson): OutlineRow => ({ kind: "real", lesson, revising: revising.has(lesson.id) }),
    ),
  }));
  if (proposal === null) return merged;

  proposal.operations.forEach((operation, index) => {
    if (!isAddLessons(operation)) return;
    const ghosts: OutlineRow[] = operation.lessons.map((lesson, position) => ({
      kind: "ghost",
      key: `ghost-${index}-${position}`,
      title: lesson.title,
    }));
    if (ghosts.length === 0) return;

    const slot = findSlot(merged, operation.insert_at_position);
    if (operation.new_unit === null) {
      // Into an existing unit, immediately before the row it displaces. Past the
      // end of the path (or an outline with no rows at all) it lands last, which
      // is where Apply will put it.
      const target = slot ?? lastUnitEnd(merged);
      if (target === null) {
        merged.push(ghostUnit(index, "Proposed lessons", ghosts));
        return;
      }
      merged[target.unit].lessons.splice(target.row, 0, ...ghosts);
      return;
    }
    // A new unit is drawn whole, where the apply transaction lands it.
    merged.splice(
      newUnitIndex(merged, slot),
      0,
      ghostUnit(index, operation.new_unit.title, ghosts),
    );
  });

  return merged;
}

/**
 * Where a **new unit** goes in the merged view — the backend's own rule, so the
 * preview cannot promise an order Apply will not produce.
 *
 * `services/shaping.py::_apply_additions` does not place the unit directly: it
 * inserts the lessons and re-derives unit order from lesson order afterwards
 * (`_renumber_units`). That comes out two ways, and only two:
 *
 * - At a **unit boundary** (the slot is the first row of its unit) the new
 *   lessons take positions ahead of every one of that unit's, so the new unit
 *   precedes it.
 * - **Mid-unit** the new lessons split the holding unit, which keeps the rows
 *   above them; the unit therefore still starts earlier, and the new one "lands
 *   as the new unit following the one it split" (that docstring, verbatim).
 *
 * A slot that is not on the path at all appends, exactly as the addition path
 * above does.
 */
function newUnitIndex(
  units: readonly OutlineUnitView[],
  slot: { unit: number; row: number } | null,
): number {
  if (slot === null) return units.length;
  // "First row of its unit" means first *real* row: ghosts an earlier operation
  // already previewed above it do not make this a mid-unit insertion.
  const boundary = units[slot.unit].lessons.slice(0, slot.row).every((row) => row.kind === "ghost");
  return boundary ? slot.unit : slot.unit + 1;
}

/** Where `position_in_path` sits in the merged view, or null if it is not there. */
function findSlot(
  units: readonly OutlineUnitView[],
  position: number,
): { unit: number; row: number } | null {
  for (const [unit, view] of units.entries()) {
    const row = view.lessons.findIndex(
      (candidate) => candidate.kind === "real" && candidate.lesson.position_in_path === position,
    );
    if (row !== -1) return { unit, row };
  }
  return null;
}

/** The end of the last unit — where an Addition past the end of the path goes. */
function lastUnitEnd(units: readonly OutlineUnitView[]): { unit: number; row: number } | null {
  if (units.length === 0) return null;
  const unit = units.length - 1;
  return { unit, row: units[unit].lessons.length };
}

function ghostUnit(index: number, title: string, lessons: OutlineRow[]): OutlineUnitView {
  return { id: `ghost-unit-${index}`, title, ghost: true, lessons };
}

// --- Undo is last-in-first-out ----------------------------------------------

/**
 * The one Change a client may offer **Undo** for: the newest still-`applied`
 * one (docs/api.md, "Why undo is LIFO").
 *
 * A Change stores its inverse as *absolute* positions recorded against the path
 * as it stood when it was applied, so replaying them under a later Change is
 * wrong in ways nothing in the payload can relate — the restriction is the
 * correctness boundary, not a simplification. The history is newest first, so
 * this is the first live row; an already-undone Change above does not block the
 * one below it, because undoing it is exactly what unblocked it.
 *
 * This is a **convenience**, not the rule: the server answers `409 not_latest`
 * regardless, and it is still the enforcer (as `409 engaged` is for engagement,
 * which no client can derive at all).
 */
export function undoableChangeId(changes: readonly Change[]): string | null {
  return changes.find((change) => change.status === "applied")?.id ?? null;
}

// --- Requests ----------------------------------------------------------------

/**
 * **Apply** the Proposal on a shaping message (TDD §5.6) — the learner's tap,
 * and the only write path into path structure. Always an explicit call from an
 * explicit control: nothing infers it from conversation text.
 */
export function applyProposal(messageId: string): Promise<ApplyProposalResult> {
  return apiFetch<ApplyProposalResult>(apiV1Path(`/messages/${messageId}/apply-proposal`), {
    method: "POST",
  });
}

/** **Undo** a Change (TDD §5.7) — `204`, and the path is restored exactly. */
export function undoChange(changeId: string): Promise<void> {
  return apiFetch<void>(apiV1Path(`/changes/${changeId}/undo`), { method: "POST" });
}

/** The path's **Change history**, newest first. Survives "new conversation". */
export function getChangeHistory(pathId: string): Promise<ChangeHistory> {
  return apiFetch<ChangeHistory>(apiV1Path(`/paths/${pathId}/changes`));
}

/**
 * TanStack query key for one path's Change history — under `"shaping"` with the
 * thread, but a **sibling** of it, because the two have different lifetimes:
 * "new conversation" empties the thread and leaves this list untouched (D3), and
 * Apply/Undo move this list without touching the thread's messages.
 */
export function changeHistoryQueryKey(pathId: string): readonly ["shaping", "changes", string] {
  return ["shaping", "changes", pathId] as const;
}

/**
 * THE Change-history query. Pass `null` whenever the sheet is not open (or the
 * surface is dark): the query idles on `skipToken`, so the history costs a
 * request exactly when a learner asks to read it — and a gated-off surface still
 * costs none at all, which is what makes shipping dark actually dark.
 */
export function changeHistoryQueryOptions(pathId: string | null) {
  return queryOptions({
    queryKey: changeHistoryQueryKey(pathId ?? "idle"),
    queryFn: pathId === null ? skipToken : () => getChangeHistory(pathId),
  });
}
