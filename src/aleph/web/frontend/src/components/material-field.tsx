import { useId, useState } from "react";
import type { MaterialFailureReason, MaterialMode } from "../lib/api";
import {
  FAILURE_COPY,
  KIND_LABEL,
  MATERIAL_MAX_ITEMS,
  MATERIAL_MIN_WORDS,
  MATERIAL_TEXT_MAX_LENGTH,
  type MaterialDraft,
  type PasteSource,
  countWords,
  guessLinkKind,
  normalizeLink,
  hostLabel,
  parseUrl,
  pasteSource,
} from "./material-items";
import { PRIMARY_CTA } from "./state-card";

// New path's Material field (CONTEXT.md: *Material*, *Material mode*). Items
// are kept locally and read by the server when the path is created; a failed
// read comes back per item and is shown on that item (`failures`).

type PasteKind = "text" | "ai_summary";
type Entry =
  | { state: "link"; url: string }
  | { state: "paste"; kind: PasteKind; source?: PasteSource; url?: string };

const PASTE_HELP: Record<PasteKind | PasteSource, { heading: string; steps: string[] }> = {
  video: {
    heading: "Paste the video's transcript",
    steps: [
      "Open the video on YouTube and tap …more under the title.",
      "Scroll down and tap Show transcript.",
      "Copy the whole transcript and paste it here. Keep the timestamps: Aleph uses them to split the video into sections.",
    ],
  },
  podcast: {
    heading: "Paste the episode's transcript",
    steps: [
      "Look for a transcript in your podcast app or on the show's website.",
      "Copy all of it and paste it here.",
    ],
  },
  text: {
    heading: "Paste text",
    steps: ["Paste the text you want the path to build from."],
  },
  ai_summary: {
    heading: "Paste an AI summary",
    steps: [
      "Paste a summary you made with NotebookLM, ChatGPT, Claude or another AI tool, for example of a book or a long course.",
      "Aleph treats it as a map of what to learn: lessons follow its outline.",
    ],
  },
};

const LINK_BUTTON = "min-h-11 text-sm font-medium text-teal hover:text-teal-bright";
const INPUT =
  "w-full rounded-md border border-divider bg-surface px-4 py-3 text-base text-porcelain placeholder:text-slate focus:border-teal focus:outline-none";

export interface MaterialFieldProps {
  items: MaterialDraft[];
  onItemsChange: (items: MaterialDraft[]) => void;
  mode: MaterialMode;
  onModeChange: (mode: MaterialMode) => void;
  /** Reasons from the last create, by item index. */
  failures?: Partial<Record<number, MaterialFailureReason>>;
  /** Opens on this entry; stories use it to show the paste box. */
  initialEntry?: Entry;
}

export function MaterialField({
  items,
  onItemsChange,
  mode,
  onModeChange,
  failures = {},
  initialEntry = { state: "link", url: "" },
}: MaterialFieldProps) {
  const [entry, setEntry] = useState<Entry>(initialEntry);
  const nextId = useId();
  const [count, setCount] = useState(0);

  function add(item: Omit<MaterialDraft, "id">) {
    setCount((n) => n + 1);
    onItemsChange([...items, { ...item, id: `${nextId}-${count}` }]);
    setEntry({ state: "link", url: "" });
  }

  function addLink(url: string) {
    const source = pasteSource(url);
    if (source) {
      setEntry({ state: "paste", kind: "text", source, url });
      return;
    }
    add({ kind: guessLinkKind(url), url });
  }

  function update(id: string, change: Partial<MaterialDraft>) {
    onItemsChange(items.map((item) => (item.id === id ? { ...item, ...change } : item)));
  }

  return (
    <section className="mt-6" aria-labelledby="material-heading" data-testid="material-field">
      <h2 id="material-heading" className="text-sm font-medium text-porcelain">
        Material
      </h2>
      <p className="mt-1 text-sm leading-6 text-mist">
        Optional. Add an article, a docs site, an arXiv paper, a transcript or an AI summary and
        Aleph builds the path from it. Aleph keeps a copy of the text with the path.
      </p>

      {items.length > 0 ? (
        <ul className="mt-3 border-t border-divider" data-testid="material-list">
          {items.map((item, index) => {
            const failure = failures[index];
            return (
              <li
                key={item.id}
                data-testid="material-item"
                className="border-b border-divider py-3"
                aria-invalid={failure ? true : undefined}
              >
                <div className="flex items-start gap-3">
                  <span className="mt-0.5 inline-flex h-6 shrink-0 items-center rounded border border-divider px-2 text-xs font-medium text-mist">
                    {KIND_LABEL[item.kind]}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="break-words text-[15px] font-medium text-porcelain">
                      {item.title ?? (item.url ? hostLabel(item.url) : KIND_LABEL[item.kind])}
                    </p>
                    <p className="mt-0.5 break-all text-sm leading-5 text-mist">
                      {item.text !== undefined
                        ? `${countWords(item.text).toLocaleString()} words pasted`
                        : item.url}
                    </p>
                    {item.kind === "article" || item.kind === "docs" ? (
                      <label className="mt-1 flex min-h-11 cursor-pointer items-center gap-3 text-sm text-mist">
                        <input
                          type="checkbox"
                          className="h-5 w-5 accent-teal"
                          checked={item.kind === "docs"}
                          onChange={(event) =>
                            update(item.id, { kind: event.target.checked ? "docs" : "article" })
                          }
                        />
                        Read the pages under this link too (up to 30)
                      </label>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    className="min-h-11 shrink-0 text-sm text-mist hover:text-porcelain"
                    onClick={() => onItemsChange(items.filter((other) => other.id !== item.id))}
                  >
                    Remove
                  </button>
                </div>
                {failure ? (
                  <div
                    role="alert"
                    className="mt-2 rounded-md border border-danger-border/60 bg-danger-bg px-4 py-3 text-sm leading-6 text-danger"
                  >
                    <p>{FAILURE_COPY[failure]}</p>
                    {item.url && failure !== "too_large" ? (
                      <button
                        type="button"
                        className="mt-1 min-h-11 font-medium text-porcelain underline"
                        onClick={() => {
                          onItemsChange(items.filter((other) => other.id !== item.id));
                          setEntry({
                            state: "paste",
                            kind: "text",
                            source: pasteSource(item.url ?? "") ?? undefined,
                            url: item.url,
                          });
                        }}
                      >
                        Paste the text instead
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}

      {items.length >= MATERIAL_MAX_ITEMS ? (
        <p className="mt-3 text-sm text-slate">You can add up to {MATERIAL_MAX_ITEMS} items.</p>
      ) : entry.state === "paste" ? (
        <PasteBox
          entry={entry}
          onCancel={() => setEntry({ state: "link", url: "" })}
          onAdd={(title, text) =>
            add({ kind: entry.kind, title: title || undefined, text, url: entry.url })
          }
        />
      ) : (
        <LinkEntry
          url={entry.url}
          onChange={(url) => setEntry({ state: "link", url })}
          onSubmit={addLink}
          onPaste={(kind) => setEntry({ state: "paste", kind })}
        />
      )}

      {items.length > 0 ? <ModeChoice mode={mode} onChange={onModeChange} /> : null}
    </section>
  );
}

function LinkEntry({
  url,
  onChange,
  onSubmit,
  onPaste,
}: {
  url: string;
  onChange: (url: string) => void;
  onSubmit: (url: string) => void;
  onPaste: (kind: PasteKind) => void;
}) {
  const link = normalizeLink(url);
  const valid = parseUrl(link) !== null;
  const source = valid ? pasteSource(link) : null;
  // Not a <form>: this sits inside New path's form, and forms don't nest.
  return (
    <div className="mt-3">
      <label htmlFor="material-url" className="sr-only">
        Link to Material
      </label>
      <div className="flex gap-2">
        <input
          id="material-url"
          type="text"
          inputMode="url"
          value={url}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              if (valid) onSubmit(link);
            }
          }}
          placeholder="Paste a link or an arXiv ID"
          className={`min-w-0 flex-1 ${INPUT}`}
        />
        <button
          type="button"
          disabled={!valid}
          onClick={() => onSubmit(link)}
          className="min-h-11 shrink-0 rounded-md border border-faint px-4 text-sm font-semibold text-porcelain transition-colors hover:border-mist disabled:cursor-not-allowed disabled:opacity-50"
        >
          Add
        </button>
      </div>
      {source ? (
        <p className="mt-2 text-sm leading-6 text-slate">
          Looks like a {source === "video" ? "video" : "podcast episode"}. You'll paste its
          transcript next.
        </p>
      ) : null}
      <div className="mt-1 flex flex-wrap gap-x-5">
        <button type="button" className={LINK_BUTTON} onClick={() => onPaste("text")}>
          Or paste text
        </button>
        <button type="button" className={LINK_BUTTON} onClick={() => onPaste("ai_summary")}>
          Paste an AI summary
        </button>
      </div>
    </div>
  );
}

function PasteBox({
  entry,
  onCancel,
  onAdd,
}: {
  entry: Extract<Entry, { state: "paste" }>;
  onCancel: () => void;
  onAdd: (title: string, text: string) => void;
}) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const help = PASTE_HELP[entry.source ?? entry.kind];
  const words = countWords(text);
  const noun = entry.source ? "transcript" : entry.kind === "ai_summary" ? "summary" : "text";
  return (
    <section
      data-testid="material-paste"
      className="mt-4 rounded-lg border border-divider bg-surface px-5 py-5"
    >
      <h3 className="text-[15px] font-semibold text-porcelain">{help.heading}</h3>
      {entry.url ? <p className="mt-1 break-all text-sm text-mist">{entry.url}</p> : null}
      <ol className="mt-3 list-decimal space-y-1 pl-5 text-sm leading-6 text-mist">
        {help.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      <label htmlFor="material-paste-title" className="mt-5 block text-sm font-medium">
        Name <span className="font-normal text-slate">(optional)</span>
      </label>
      <input
        id="material-paste-title"
        type="text"
        value={title}
        maxLength={200}
        onChange={(event) => setTitle(event.target.value)}
        placeholder="e.g. Chapter 3 notes"
        className={`mt-2 ${INPUT} bg-night`}
      />
      <label htmlFor="material-paste-text" className="mt-4 block text-sm font-medium">
        {noun[0].toUpperCase() + noun.slice(1)}
      </label>
      <textarea
        id="material-paste-text"
        rows={8}
        value={text}
        maxLength={MATERIAL_TEXT_MAX_LENGTH}
        onChange={(event) => setText(event.target.value)}
        placeholder={entry.source === "video" ? "0:00 Welcome everyone…" : "Paste here"}
        className={`mt-2 resize-y font-mono text-sm leading-6 ${INPUT} bg-night`}
      />
      <p className="mt-2 text-sm leading-6 text-slate" aria-live="polite">
        {words === 0
          ? "Nothing pasted yet."
          : words < MATERIAL_MIN_WORDS
            ? `${words} words. That's too short to build lessons from.`
            : `${words.toLocaleString()} words`}
      </p>
      <div className="mt-4 grid gap-2">
        <button
          type="button"
          disabled={words < MATERIAL_MIN_WORDS}
          className={PRIMARY_CTA}
          onClick={() => onAdd(title.trim(), text.trim())}
        >
          Add {noun}
        </button>
        <button
          type="button"
          className="min-h-11 text-sm text-mist hover:text-porcelain"
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>
    </section>
  );
}

const MODES: { value: MaterialMode; label: string; help: string }[] = [
  {
    value: "teach",
    label: "Teach this",
    help: "Lessons stay inside your Material. Short Material makes a short path.",
  },
  {
    value: "start",
    label: "Start from this",
    help: "Your Material is the core; Aleph adds what comes before and after it.",
  },
];

function ModeChoice({
  mode,
  onChange,
}: {
  mode: MaterialMode;
  onChange: (mode: MaterialMode) => void;
}) {
  return (
    <fieldset className="mt-5">
      <legend className="text-sm font-medium text-porcelain">How should Aleph use it?</legend>
      <div className="mt-3 grid gap-2">
        {MODES.map((option) => (
          <label
            key={option.value}
            className={`flex cursor-pointer items-start gap-3 rounded-md border px-4 py-3 ${
              mode === option.value ? "border-teal bg-elevated" : "border-divider"
            }`}
          >
            <input
              type="radio"
              name="material_mode"
              value={option.value}
              checked={mode === option.value}
              onChange={() => onChange(option.value)}
              className="mt-1 h-4 w-4 accent-teal"
            />
            <span>
              <span className="block text-[15px] font-medium text-porcelain">{option.label}</span>
              <span className="block text-sm leading-6 text-mist">{option.help}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
