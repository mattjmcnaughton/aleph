// Home's one "start something new" affordance.
//
// Aleph has two top-level things a learner can start — a **Path** and a **Beat**
// (CONTEXT: "the top-level sibling of a Path") — and they were reached from two
// unrelated places on the home screen: a filled button up in the hero for a
// path, and a text link buried in the Beats section header for a Beat, which
// only exists at all once you have scrolled to a section you may have
// collapsed. This is the GitHub-style menu both now hang off: one control, top
// right, that asks *what kind of thing* rather than assuming.
//
// **A menu only when there is a choice.** With the `analyst` flag off there is
// exactly one kind of thing to start, and a dropdown holding a single item is
// strictly worse than the button it replaced — a tap that reveals one option is
// a tap that bought nothing. So a one-item menu renders as the plain primary
// button it has always been, with that item's own label and testid. The branch
// lives here rather than at the call site so home never has to know which shape
// it got.

import { Link, useNavigate } from "@tanstack/react-router";
import { type RefObject, useEffect, useId, useRef, useState } from "react";

/**
 * Compact and secondary, at every width. Starting a ninth path is not what a
 * returning learner came to do, so this no longer takes the full-width primary
 * slot above Continue; the first-run empty state carries its own primary CTA.
 */
const NEW_TRIGGER =
  "inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded-md border border-faint px-3.5 text-sm font-semibold text-porcelain transition-colors hover:border-mist";

function PlusIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true">
      <path d="M8 3v10M3 8h10" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  );
}

export interface NewMenuItem {
  /** A literal route TanStack has already generated (`/new`, `/beats/new`). */
  to: string;
  label: string;
  /** The one line under the label: what this kind of thing is *for*. */
  description: string;
  testid: string;
}

function CaretIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" fill="none" aria-hidden="true">
      <path
        d="M4 6.5l4 4 4-4"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function NewMenu({
  items,
  triggerRef,
}: {
  items: readonly NewMenuItem[];
  /** Home's focus target after a delete empties the list (its C3 rule). */
  triggerRef?: RefObject<HTMLButtonElement | null>;
}) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const containerRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const localTriggerRef = useRef<HTMLButtonElement>(null);
  const buttonRef = triggerRef ?? localTriggerRef;

  // Menu-button convention (WAI-ARIA): opening moves focus into the menu, so a
  // keyboard user is never left on a trigger with a list they cannot reach.
  useEffect(() => {
    if (!open) return;
    menuRef.current?.querySelector<HTMLElement>("[role='menuitem']")?.focus();
  }, [open]);

  // A click anywhere else dismisses it. `pointerdown` rather than `click`: a
  // menu that survives until mouseup swallows the first press on whatever is
  // underneath it, which on this page is a row's Delete button.
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [open]);

  const single = items.length === 1 ? items[0] : undefined;
  if (single) {
    return (
      <button
        type="button"
        ref={buttonRef}
        data-testid={single.testid}
        onClick={() => navigate({ to: single.to as never })}
        className={NEW_TRIGGER}
      >
        <PlusIcon />
        {single.label}
      </button>
    );
  }

  /** Move focus by `step` within the open menu, wrapping at both ends. */
  function focusItem(step: number, from?: HTMLElement) {
    const options = Array.from(
      menuRef.current?.querySelectorAll<HTMLElement>("[role='menuitem']") ?? [],
    );
    if (options.length === 0) return;
    const index = from ? options.indexOf(from) : -1;
    const next = (index + step + options.length) % options.length;
    options[next]?.focus();
  }

  function close(restoreFocus: boolean) {
    setOpen(false);
    if (restoreFocus) buttonRef.current?.focus();
  }

  return (
    <div ref={containerRef} className="relative shrink-0">
      <button
        type="button"
        ref={buttonRef}
        data-testid="new-menu-button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((wasOpen) => !wasOpen)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            setOpen(true);
          }
        }}
        className={NEW_TRIGGER}
      >
        <PlusIcon />
        New
        <CaretIcon />
      </button>

      {open ? (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          data-testid="new-menu"
          aria-label="Start something new"
          // Right-aligned and full-width below `lg`: at 390px an absolutely
          // positioned menu sized to its widest item would otherwise hang off
          // the screen and give home a horizontal scrollbar (the phone rule
          // every surface here is held to).
          className="absolute right-0 z-20 mt-2 w-72 max-w-[calc(100vw-2.5rem)] overflow-hidden rounded-lg border border-divider bg-surface p-1.5 shadow-lg"
          onKeyDown={(event) => {
            const active = document.activeElement as HTMLElement | null;
            if (event.key === "Escape") {
              event.preventDefault();
              close(true);
            } else if (event.key === "ArrowDown") {
              event.preventDefault();
              focusItem(1, active ?? undefined);
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              focusItem(-1, active ?? undefined);
            }
          }}
        >
          {items.map((item) => (
            <Link
              key={item.to}
              // The `to` union is wider than this component can usefully model;
              // every call site passes a literal generated route.
              to={item.to as never}
              role="menuitem"
              data-testid={item.testid}
              onClick={() => setOpen(false)}
              className="block rounded-md px-3 py-2.5 text-left transition-colors hover:bg-elevated focus-visible:bg-elevated focus-visible:outline-none"
            >
              <span className="block text-sm font-semibold text-porcelain">{item.label}</span>
              <span className="mt-0.5 block text-sm leading-5 text-mist">{item.description}</span>
            </Link>
          ))}
        </div>
      ) : null}
    </div>
  );
}
