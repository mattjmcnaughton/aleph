// Frontend mirror of the flashcard shape caps the backend enforces
// (`agents/flashcard.py`'s `FlashcardCaps` defaults; `dtos/flashcards.py`'s
// `UpdateCardRequest` validator reuses the very same agent predicates against
// them — AL-410 plan §4). A learner-written card obeys the same word caps as
// an agent-written one; this is the client half of that rule: disable Save
// before a doomed `PATCH` ever leaves the browser, with live word counts so
// the cap is visible on screen rather than discovered from a failed request
// (AL-410 plan §6).
//
// The three predicates below are a deliberately narrow port of
// `is_non_empty` / `within_word_cap` / `sides_differ` from `agents/flashcard.py`
// — not a shared package, because the frontend cannot import Python. Kept
// intentionally close to the backend's own names and shapes so the two stay
// easy to eyeball against each other if the backend's caps ever move.

/** Mirrors `FlashcardCaps.front_words_max`'s default (`agents/flashcard.py`). */
export const FRONT_WORDS_MAX = 25;

/** Mirrors `FlashcardCaps.back_words_max`'s default (`agents/flashcard.py`). */
export const BACK_WORDS_MAX = 60;

/**
 * Mirrors `agents/flashcard.py`'s `count_words`: split on whitespace, count
 * tokens — never a prose-aware count a regex could only approximate
 * differently. Python's `str.split()` (no arguments) discards leading/
 * trailing whitespace and treats a run of whitespace as one separator; a bare
 * `text.split(/\s+/)` in JS does not (a leading-whitespace string yields a
 * spurious empty first token), so the text is trimmed first to match.
 */
export function countWords(text: string): number {
  const trimmed = text.trim();
  return trimmed === "" ? 0 : trimmed.split(/\s+/).length;
}

/** Mirrors `agents/flashcard.py`'s `is_non_empty`. */
function isNonEmpty(text: string): boolean {
  return text.trim() !== "";
}

/** Mirrors `agents/flashcard.py`'s `within_word_cap`. */
function withinWordCap(text: string, maximum: number): boolean {
  return countWords(text) <= maximum;
}

/**
 * Mirrors `agents/flashcard.py`'s `sides_differ`: compared case- and
 * whitespace-insensitively, so two sides differing only in casing or padding
 * still count as identical. `toLowerCase()` stands in for Python's
 * `casefold()` — a close-enough approximation for this client-side gate,
 * since the backend's own validator is what actually enforces the rule.
 */
function sidesDiffer(front: string, back: string): boolean {
  return front.trim().toLowerCase() !== back.trim().toLowerCase();
}

/**
 * True when a `PATCH /flashcards/{id}` body of `{front, back}` would pass
 * the backend's `UpdateCardRequest` validator — the gate `card-row.tsx` uses
 * to disable Save before an invalid edit is ever sent (AL-410 plan §6).
 */
export function canSaveCardEdit(front: string, back: string): boolean {
  return (
    isNonEmpty(front) &&
    isNonEmpty(back) &&
    withinWordCap(front, FRONT_WORDS_MAX) &&
    withinWordCap(back, BACK_WORDS_MAX) &&
    sidesDiffer(front, back)
  );
}
