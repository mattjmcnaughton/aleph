// Shared send gesture for the rail composers (tutor and shaping alike): a
// textarea has to hold both "type a question" and "send the question", and
// here plain `Enter` is reserved for the former — a learner mid-sentence who
// taps Enter for a line break gets a line break, never a send. Shift+Enter is
// the send gesture instead.
//
// **This is deliberately the inverse of the common convention.** Every
// mainstream chat UI sends on bare Enter and breaks the line on Shift+Enter;
// Aleph does the opposite because a question about a Read passage is often
// several lines long, and losing one to an accidental send costs more than a
// send costs an extra modifier. Do not "fix" the inversion — it is the asked-for
// behaviour, and both rails must keep agreeing on it.

import type { KeyboardEvent } from "react";

/**
 * Wire this to a composer `<textarea>`'s `onKeyDown`. Fires `onSend` for any
 * `Enter` with `shiftKey` held — other modifiers are ignored, so
 * Ctrl/Cmd/Alt+Shift+Enter send too (a stray modifier held alongside the
 * gesture is a fat-finger, not a different intent). Enter *without* Shift
 * always falls through to the textarea's own newline.
 *
 * Two things it refuses:
 *
 *  - **A key held down** (`event.repeat`), so leaning on Shift+Enter cannot
 *    fire the same send twice.
 *  - **An in-flight IME composition** (`isComposing`, and its legacy
 *    `keyCode === 229` spelling). Mid-composition the characters live in the
 *    IME's buffer, not in the controlled `value` — so without this guard a
 *    learner composing Japanese or Chinese would have `preventDefault` eat the
 *    commit chord and send whatever was committed *before* the phrase they are
 *    still typing.
 *
 * Disabled/streaming composers do not need a guard here: the rails' own
 * `send` already refuses while `status === "streaming"` (the same guard the
 * form's submit and the suggestion chips rely on), so this simply calls it.
 */
export function handleComposerKeyDown(
  event: KeyboardEvent<HTMLTextAreaElement>,
  onSend: () => void,
): void {
  if (event.nativeEvent.isComposing || event.keyCode === 229) return;
  if (event.key !== "Enter" || !event.shiftKey || event.repeat) return;
  event.preventDefault();
  onSend();
}
