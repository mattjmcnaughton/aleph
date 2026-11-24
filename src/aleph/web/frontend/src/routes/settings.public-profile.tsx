import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { Breadcrumbs } from "../components/breadcrumbs";
import { SectionHeader } from "../components/section-header";
import { PRIMARY_CTA, SECONDARY_CTA, StateCard } from "../components/state-card";
import { Workspace } from "../components/workspace";
import {
  ApiError,
  BEATS_LIST_QUERY_KEY,
  type BeatList,
  PATHS_LIST_QUERY_KEY,
  PUBLIC_PROFILE_SETTINGS_QUERY_KEY,
  type PathList,
  type PublicProfileSettings,
  beatsListQueryOptions,
  getPublicProfileSettings,
  pathsListQueryOptions,
  putPublicProfileSettings,
  setBeatShared,
  setPathShared,
} from "../lib/api";
import { cadenceLabel } from "../lib/beats";
import { useFeatureFlag } from "../lib/feature-flags";
import { levelLabel } from "../lib/onboarding";

// Settings → Public profile (Public profiles PRD §3 frames 1–2, TDD §6): the
// switch, the Handle, and what to show, one path or Beat at a time.
export const Route = createFileRoute("/settings/public-profile")({
  component: PublicProfileSettingsPage,
});

const HANDLE_ERRORS: Record<string, string> = {
  handle_taken: "That handle is taken. Try another.",
  handle_rejected: "Use 3 to 30 lowercase letters, numbers or hyphens. Some names are reserved.",
  handle_required: "Pick a handle first.",
  handle_locked: "Your handle can't be changed.",
};

function PublicProfileSettingsPage() {
  const enabled = useFeatureFlag("public_profiles");
  const settings = useQuery({
    queryKey: PUBLIC_PROFILE_SETTINGS_QUERY_KEY,
    queryFn: getPublicProfileSettings,
    enabled,
  });

  return (
    <Workspace testid="public-profile-settings" width="lesson">
      <Breadcrumbs current="Public profile" />
      <p className="kicker">Settings</p>
      <h1 className="mt-2 page-title">Public profile</h1>
      <p className="mt-3 text-base leading-6 text-mist">
        A page anyone can open, without signing in, showing the paths and Beats you pick. Visitors
        can start their own copy of anything on it.
      </p>
      {!enabled ? (
        <StateCard testid="public-profile-unavailable" spacing="mt-7">
          <p className="text-sm text-mist">Public profiles aren't available yet.</p>
        </StateCard>
      ) : settings.data ? (
        <SettingsBody settings={settings.data} />
      ) : settings.isError ? (
        <p role="alert" className="mt-7 text-sm text-danger">
          We couldn't load your profile settings. Try again.
        </p>
      ) : (
        <p className="mt-7 text-sm text-mist">Loading…</p>
      )}
    </Workspace>
  );
}

function SettingsBody({ settings }: { settings: PublicProfileSettings }) {
  const queryClient = useQueryClient();
  const [handle, setHandle] = useState("");
  const [choosing, setChoosing] = useState(false);
  const save = useMutation({
    mutationFn: putPublicProfileSettings,
    onSuccess: (saved) => {
      queryClient.setQueryData(PUBLIC_PROFILE_SETTINGS_QUERY_KEY, saved);
      setChoosing(false);
    },
  });
  const errorCode = save.error instanceof ApiError ? save.error.code : null;
  const on = settings.profile_public;

  function update(change: Partial<PublicProfileSettings>) {
    save.mutate({ ...settings, ...change });
  }

  function toggle() {
    if (on) update({ profile_public: false });
    else if (settings.handle) update({ profile_public: true });
    else setChoosing(true);
  }

  return (
    <>
      <div className="mt-7 flex items-center justify-between gap-4 rounded-lg border border-divider bg-surface px-4 py-4">
        <div>
          <p className="font-semibold" id="public-profile-switch-label">
            Show my public profile
          </p>
          <p className="text-sm text-mist">{on ? "On. Anyone with the link can see it." : "Off"}</p>
        </div>
        <Switch
          on={on || choosing}
          labelledBy="public-profile-switch-label"
          disabled={save.isPending}
          onToggle={choosing ? () => setChoosing(false) : toggle}
        />
      </div>

      {save.isError && !choosing ? (
        <p role="alert" data-testid="public-profile-error" className="mt-3 text-sm text-danger">
          {(errorCode && HANDLE_ERRORS[errorCode]) ?? "That didn't save. Try again."}
        </p>
      ) : null}

      {choosing ? (
        <form
          className="mt-7"
          onSubmit={(event) => {
            event.preventDefault();
            update({ handle: handle.trim().toLowerCase(), profile_public: true });
          }}
        >
          <label htmlFor="public-handle" className="kicker">
            Pick your handle
          </label>
          <div className="mt-2 flex min-h-12 items-center rounded-md border border-faint bg-surface px-3 focus-within:border-teal">
            <span className="text-mist">{window.location.host}/@</span>
            <input
              id="public-handle"
              data-testid="public-handle-input"
              value={handle}
              onChange={(event) => setHandle(event.target.value)}
              maxLength={30}
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              className="min-w-0 flex-1 bg-transparent font-semibold text-porcelain focus:outline-none"
            />
          </div>
          <p className="mt-2 text-sm leading-6 text-mist">
            Lowercase letters, numbers and hyphens. Your handle is permanent once set.
          </p>
          {save.isError ? (
            <p role="alert" data-testid="public-handle-error" className="mt-2 text-sm text-danger">
              {(errorCode && HANDLE_ERRORS[errorCode]) ?? "That didn't save. Try again."}
            </p>
          ) : null}
          <WhatVisitorsSee />
          <button
            type="submit"
            disabled={handle.trim().length < 3 || save.isPending}
            className={`mt-6 ${PRIMARY_CTA}`}
          >
            {save.isPending ? "Turning on…" : "Turn on my profile"}
          </button>
        </form>
      ) : on && settings.handle ? (
        <OnState settings={settings} handle={settings.handle} onUpdate={update} />
      ) : (
        <p className="mt-6 text-sm leading-6 text-mist">
          Nothing is public until you turn this on and pick what to show.
        </p>
      )}
    </>
  );
}

function OnState({
  settings,
  handle,
  onUpdate,
}: {
  settings: PublicProfileSettings;
  handle: string;
  onUpdate: (change: Partial<PublicProfileSettings>) => void;
}) {
  const analystEnabled = useFeatureFlag("analyst");
  const paths = useQuery(pathsListQueryOptions);
  const beats = useQuery(beatsListQueryOptions(analystEnabled));
  const [copied, setCopied] = useState(false);
  const link = `${window.location.origin}/@${handle}`;

  const pathList = paths.data?.paths ?? [];
  const beatList = beats.data?.beats ?? [];

  return (
    <>
      <div className="mt-7">
        <p className="kicker">Your link</p>
        <span
          data-testid="public-profile-link"
          className="mt-2 flex min-h-12 items-center rounded-md border border-faint bg-surface px-3"
        >
          <span className="text-mist">{window.location.host}/@</span>
          <span className="font-semibold">{handle}</span>
        </span>
      </div>
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          className={`${SECONDARY_CTA} !min-h-11 !py-2 text-sm`}
          onClick={async () => {
            await navigator.clipboard?.writeText(link);
            setCopied(true);
          }}
        >
          {copied ? "Copied" : "Copy link"}
        </button>
        <Link
          to="/@{$handle}"
          params={{ handle }}
          data-testid="view-as-visitor"
          className={`${SECONDARY_CTA} !min-h-11 !py-2 text-sm`}
        >
          View as a visitor
        </Link>
      </div>

      <SectionHeader
        kicker="Paths on your profile"
        spacing="mt-9"
        summary={`${pathList.filter((path) => path.shared).length} of ${pathList.length} shown`}
      />
      <ul>
        {pathList.map((path) => (
          <SharedRow
            key={path.id}
            id={path.id}
            kind="path"
            title={path.title}
            detail={`${levelLabel(path.level)} · ${pathDetail(path.progress)}`}
            shared={path.shared}
          />
        ))}
      </ul>
      {paths.isSuccess && pathList.length === 0 ? (
        <p className="mt-3 text-sm text-mist">You have no paths yet.</p>
      ) : null}

      {analystEnabled && beatList.length > 0 ? (
        <>
          <SectionHeader
            kicker="Beats on your profile"
            spacing="mt-9"
            summary={`${beatList.filter((beat) => beat.shared).length} of ${beatList.length} shown`}
          />
          <ul>
            {beatList.map((beat) => (
              <SharedRow
                key={beat.id}
                id={beat.id}
                kind="beat"
                title={beat.topic}
                detail={cadenceLabel(beat.cadence)}
                shared={beat.shared}
              />
            ))}
          </ul>
        </>
      ) : null}

      <SectionHeader kicker="Also shown" spacing="mt-9" />
      <ul>
        <CheckRow
          testid="share-activity"
          title="Daily streak and activity"
          detail="The last 7 weeks, across all your paths"
          checked={settings.profile_show_activity}
          onChange={(checked) => onUpdate({ profile_show_activity: checked })}
        />
      </ul>

      <WhatVisitorsSee />
    </>
  );
}

function pathDetail(progress: { completed_lessons: number; total_lessons: number }): string {
  if (progress.total_lessons > 0 && progress.completed_lessons === progress.total_lessons) {
    return "Complete";
  }
  if (progress.completed_lessons === 0) return "Not started";
  return `${progress.completed_lessons} of ${progress.total_lessons}`;
}

/** The plain-words note, shown before anything is shared (TDD §0 decision 2). */
function WhatVisitorsSee() {
  return (
    <p data-testid="public-profile-note" className="mt-6 text-sm leading-6 text-mist">
      Sharing is live. On a shared path, visitors see how you started it (topic, level, guidance and
      its current teaching style), its outline and your progress, and can start their own from the
      same inputs. A shared Beat shows every Brief, including future ones, in full. Your lessons,
      Quick check answers, tutor conversations and About you stay private. Lesson titles can echo
      your guidance or About you, so check "View as a visitor" before you share.
    </p>
  );
}

function SharedRow({
  id,
  kind,
  title,
  detail,
  shared,
}: {
  id: string;
  kind: "path" | "beat";
  title: string;
  detail: string;
  shared: boolean;
}) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (next: boolean) => (kind === "path" ? setPathShared : setBeatShared)(id, next),
    onSuccess: ({ shared: saved }) => {
      if (kind === "path") {
        queryClient.setQueryData<PathList>(PATHS_LIST_QUERY_KEY, (list) =>
          list
            ? {
                ...list,
                paths: list.paths.map((path) =>
                  path.id === id ? { ...path, shared: saved } : path,
                ),
              }
            : list,
        );
      } else {
        queryClient.setQueryData<BeatList>(BEATS_LIST_QUERY_KEY, (list) =>
          list
            ? {
                ...list,
                beats: list.beats.map((beat) =>
                  beat.id === id ? { ...beat, shared: saved } : beat,
                ),
              }
            : list,
        );
      }
    },
  });
  return (
    <CheckRow
      testid={`share-${kind}`}
      title={title}
      detail={mutation.isError ? "That didn't save. Try again." : detail}
      checked={mutation.isPending ? !!mutation.variables : shared}
      disabled={mutation.isPending}
      onChange={(checked) => mutation.mutate(checked)}
    />
  );
}

function CheckRow({
  testid,
  title,
  detail,
  checked,
  disabled,
  onChange,
}: {
  testid: string;
  title: string;
  detail: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <li className="border-b border-divider">
      <label
        data-testid={testid}
        className="flex min-h-[56px] cursor-pointer items-center gap-3 py-3"
      >
        <input
          type="checkbox"
          className="h-5 w-5 shrink-0 accent-teal"
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span className="min-w-0">
          <span className="line-clamp-2 block text-base font-semibold leading-snug">{title}</span>
          <span className="block text-sm text-mist">{detail}</span>
        </span>
      </label>
    </li>
  );
}

function Switch({
  on,
  labelledBy,
  disabled,
  onToggle,
}: {
  on: boolean;
  labelledBy: string;
  disabled: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-labelledby={labelledBy}
      data-testid="public-profile-switch"
      disabled={disabled}
      onClick={onToggle}
      className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors disabled:opacity-60 ${
        on ? "bg-teal" : "bg-elevated ring-1 ring-faint"
      }`}
    >
      <span
        className={`absolute h-5 w-5 rounded-full transition-transform ${
          on ? "translate-x-6 bg-night" : "translate-x-1 bg-mist"
        }`}
      />
    </button>
  );
}
