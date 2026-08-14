// The disclosure chevron: right when a section is closed, down when it is open.
//
// Extracted from `sidebar.tsx`, which drew it first, once the home screen's own
// section headers became collapsible too — a disclosure triangle that points a
// different way, or turns a different corner radius, on two surfaces of the
// same app is the kind of drift `list-row.tsx` and `section-header.tsx` were
// both written to end.

export function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg viewBox="0 0 16 16" width="11" height="11" fill="none" aria-hidden="true">
      {open ? (
        <path
          d="M4 6l4 4 4-4"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ) : (
        <path
          d="M6 4l4 4-4 4"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      )}
    </svg>
  );
}
