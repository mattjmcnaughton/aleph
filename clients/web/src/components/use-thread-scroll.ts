// Keeping the tail of a conversation in view (AL-230/AL-330).
//
// Shared by both rails, for the reason `isAbort` and `failureCopy` are shared:
// there is no state machine in it. It is a scroll container and one fact about
// where the learner is looking — and two copies of that fact would come to
// disagree about the only thing that matters here, which is when *not* to
// scroll.

import { type RefObject, useEffect, useRef } from "react";

/**
 * How close to the bottom still counts as being at it. Sub-pixel layout and a
 * rounded `clientHeight` mean a thread scrolled all the way down seldom reports
 * exactly zero, and a learner one pixel off the end is not reading back.
 */
const AT_BOTTOM_SLACK = 24;

export interface ThreadScroll {
  /** Goes on the scrolling thread container. */
  ref: RefObject<HTMLDivElement | null>;
  /** ...and so does this, which is how the hook knows where the learner is. */
  onScroll: () => void;
}

/**
 * Follow the tail of a thread that grows underneath the learner.
 *
 * Two rules, and the second is the one worth stating:
 *
 * 1. **Sending is a request to be at the bottom.** The question and the reply
 *    are both appended there, so a learner who scrolled up to re-read something
 *    and then asked about it is taken back down to what they asked. `sending`
 *    going true is that request.
 * 2. **A reply follows only the learner who is already following it.** Once
 *    they scroll up mid-stream they are reading, not waiting, and re-pinning
 *    them to the bottom on every delta would drag what they are reading off
 *    screen several times a second.
 */
export function useThreadScroll(sending: boolean): ThreadScroll {
  const ref = useRef<HTMLDivElement>(null);
  const following = useRef(true);

  const onScroll = () => {
    const thread = ref.current;
    if (thread === null) return;
    following.current =
      thread.scrollHeight - thread.scrollTop - thread.clientHeight <= AT_BOTTOM_SLACK;
  };

  // Declared above the effect below so that a send re-arms the follow for the
  // very commit that renders its question, rather than one render later.
  useEffect(() => {
    if (sending) following.current = true;
  }, [sending]);

  // No dependency list on purpose. The thread grows a few characters at a time
  // and there is nothing to compare it against, so "after every render, if we
  // are following, be at the bottom" is the rule *and* the implementation.
  useEffect(() => {
    const thread = ref.current;
    if (thread === null || !following.current) return;
    thread.scrollTop = thread.scrollHeight;
  });

  return { ref, onScroll };
}
