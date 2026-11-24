import { HttpResponse, http } from "msw";
import {
  API_V1_BASE,
  type PublicBeat,
  type PublicBrief,
  type PublicPath,
  type PublicProfile,
  type PublicProfileSettings,
} from "../lib/api";

// Contract-shaped fakes for Public profiles (docs/api.md): the owner settings
// and the anonymous reads. Everything absent is the same `404 not_found`, as
// on the real server (TDD D5).

interface PublicStore {
  profiles: Map<string, PublicProfile>;
  paths: Map<string, PublicPath>;
  beats: Map<string, PublicBeat>;
  briefs: Map<string, PublicBrief>;
}

const emptyStore = (): PublicStore => ({
  profiles: new Map(),
  paths: new Map(),
  beats: new Map(),
  briefs: new Map(),
});

const defaultSettings = (): PublicProfileSettings => ({
  handle: null,
  profile_public: false,
  profile_show_activity: true,
});

let store = emptyStore();
let settings = defaultSettings();
let takenHandles = new Set<string>();
let settingsBodies: PublicProfileSettings[] = [];

export function resetPublic(): void {
  store = emptyStore();
  settings = defaultSettings();
  takenHandles = new Set();
  settingsBodies = [];
}

export function seedPublicProfile(profile: PublicProfile): void {
  store.profiles.set(profile.handle, profile);
}

export function seedPublicPath(path: PublicPath): void {
  store.paths.set(`${path.owner.handle}/${path.id}`, path);
}

export function seedPublicBeat(beat: PublicBeat): void {
  store.beats.set(`${beat.owner.handle}/${beat.id}`, beat);
}

export function seedPublicBrief(brief: PublicBrief): void {
  store.briefs.set(`${brief.owner.handle}/${brief.beat_id}/${brief.id}`, brief);
}

export function configurePublicSettings(
  overrides: Partial<PublicProfileSettings> & { takenHandles?: string[] },
): void {
  const { takenHandles: taken, ...rest } = overrides;
  settings = { ...settings, ...rest };
  if (taken) takenHandles = new Set(taken);
}

/** Every `PUT /account/public-profile` body, in order (for assertions). */
export function publicSettingsBodies(): PublicProfileSettings[] {
  return settingsBodies;
}

/** Credit for a Copy, as the server resolves it: only for a public path. */
export function publicPathCredit(
  pathId: unknown,
): { display_name: string; handle: string; path_id: string } | null {
  for (const path of store.paths.values()) {
    if (path.id === pathId) {
      return { display_name: path.owner.display_name, handle: path.owner.handle, path_id: path.id };
    }
  }
  return null;
}

export function publicBeatCredit(
  beatId: unknown,
): { display_name: string; handle: string; beat_id: string } | null {
  for (const beat of store.beats.values()) {
    if (beat.id === beatId) {
      return { display_name: beat.owner.display_name, handle: beat.owner.handle, beat_id: beat.id };
    }
  }
  return null;
}

function notFound() {
  return HttpResponse.json(
    { error: { code: "not_found", message: "not found", request_id: "test" } },
    { status: 404 },
  );
}

function found<T>(value: T | undefined) {
  return value === undefined ? notFound() : HttpResponse.json(value);
}

function error(status: number, code: string, message: string, details?: unknown) {
  return HttpResponse.json(
    { error: { code, message, request_id: "test", ...(details ? { details } : {}) } },
    { status },
  );
}

const HANDLE = /^[a-z0-9-]{3,30}$/;
const RESERVED = new Set(["admin", "aleph", "settings", "new", "login", "api", "auth"]);

export const publicHandlers = [
  http.get(`${API_V1_BASE}/account/public-profile`, () => HttpResponse.json(settings)),
  http.put(`${API_V1_BASE}/account/public-profile`, async ({ request }) => {
    const body = (await request.json()) as PublicProfileSettings;
    settingsBodies.push(body);
    let handle = settings.handle;
    if (body.handle !== null) {
      const wanted = body.handle.trim().toLowerCase();
      if (!HANDLE.test(wanted) || RESERVED.has(wanted)) {
        return error(422, "handle_rejected", "That handle can't be used.", {
          reason: RESERVED.has(wanted) ? "reserved" : "bad_characters",
        });
      }
      if (handle === null) {
        if (takenHandles.has(wanted)) return error(409, "handle_taken", "That handle is taken.");
        handle = wanted;
      } else if (handle !== wanted) {
        return error(409, "handle_locked", "Your handle can't be changed.");
      }
    }
    if (body.profile_public && handle === null) {
      return error(422, "handle_required", "Pick a handle before turning your profile on.");
    }
    settings = {
      handle,
      profile_public: body.profile_public,
      profile_show_activity: body.profile_show_activity,
    };
    return HttpResponse.json(settings);
  }),
  http.get(`${API_V1_BASE}/public/profiles/:handle`, ({ params }) =>
    found(store.profiles.get(String(params.handle))),
  ),
  http.get(`${API_V1_BASE}/public/profiles/:handle/paths/:id`, ({ params }) =>
    found(store.paths.get(`${params.handle}/${params.id}`)),
  ),
  http.get(`${API_V1_BASE}/public/profiles/:handle/beats/:id`, ({ params }) =>
    found(store.beats.get(`${params.handle}/${params.id}`)),
  ),
  http.get(`${API_V1_BASE}/public/profiles/:handle/beats/:beatId/briefs/:id`, ({ params }) =>
    found(store.briefs.get(`${params.handle}/${params.beatId}/${params.id}`)),
  ),
];

// --- One owner's published world, shared by tests and stories --------------

export const MATT = { display_name: "Matt McNaughton", handle: "matt" };
export const RUST_PATH_ID = "7a1c5e2b-3d4f-4a6b-8c9d-0e1f2a3b4c5d";
export const AI_BEAT_ID = "9b2d6f3c-4e5a-4b7c-9d0e-1f2a3b4c5d6e";
export const AI_BRIEF_ID = "4c3e7a4d-5f6b-4c8d-ae1f-2a3b4c5d6e7f";

export const RUST_PUBLIC_PATH: PublicPath = {
  id: RUST_PATH_ID,
  owner: MATT,
  title: "Rust ownership & lifetimes",
  topic: "Rust ownership and lifetimes",
  level: "some_experience",
  guidance: "I write Go at work and keep fighting the borrow checker on side projects.",
  teaching_style: "Short passages, lots of small code examples, compare to Go where it helps.",
  done: 4,
  total: 11,
  units: [
    {
      title: "Ownership from first principles",
      lessons: [
        { title: "Moves, copies and the stack", complete: true, current: false },
        { title: "The borrow checker's three rules", complete: true, current: false },
        { title: "Shared and mutable borrows", complete: true, current: false },
        { title: "Slices and borrowed views", complete: true, current: false },
      ],
    },
    {
      title: "Lifetimes in practice",
      lessons: [
        { title: "Reading lifetime annotations", complete: false, current: true },
        { title: "Elision and when it fails", complete: false, current: false },
        { title: "Lifetimes in structs", complete: false, current: false },
        { title: "'static, honestly", complete: false, current: false },
      ],
    },
    {
      title: "Smart pointers",
      lessons: [
        { title: "Box and the heap", complete: false, current: false },
        { title: "Rc, Arc and shared ownership", complete: false, current: false },
        { title: "RefCell and interior mutability", complete: false, current: false },
      ],
    },
  ],
};

export const AI_PUBLIC_BEAT: PublicBeat = {
  id: AI_BEAT_ID,
  owner: MATT,
  topic: "AI infrastructure",
  level: "work_in_it",
  guidance: "Inference costs and the hardware behind them; skip model launches.",
  cadence: "weekly",
  briefs: [
    {
      id: AI_BRIEF_ID,
      number: 3,
      published_on: "2026-11-17",
      title: "Inference is getting cheaper faster than training",
      source_count: 3,
    },
    {
      id: "5d4f8b5e-6a7c-4d9e-bf2a-3b4c5d6e7f80",
      number: 2,
      published_on: "2026-11-10",
      title: "Batching schedulers, explained",
      source_count: 4,
    },
    {
      id: "6e5a9c6f-7b8d-4eaf-8a3b-4c5d6e7f8091",
      number: 1,
      published_on: "2026-11-03",
      title: "Where the GPUs actually are",
      source_count: 7,
    },
  ],
  next_cursor: null,
};

export const AI_PUBLIC_BRIEF: PublicBrief = {
  id: AI_BRIEF_ID,
  beat_id: AI_BEAT_ID,
  beat_topic: "AI infrastructure",
  owner: MATT,
  number: 3,
  published_on: "2026-11-17",
  title: "Inference is getting cheaper faster than training",
  body_markdown: [
    "Serving a token got roughly **40% cheaper** since September, mostly from batching and new kernels rather than new hardware.",
    "",
    "## What changed",
    "",
    "- Continuous batching is now the default in the two largest open serving stacks.",
    "- Speculative decoding moved from papers into production configs.",
    "",
    "```mermaid",
    "graph LR; Request-->Batcher-->GPU",
    "```",
  ].join("\n"),
  sources: [
    {
      position: 1,
      publisher: "Northlake Systems Review",
      title: "Continuous batching in production",
      published_on: "2026-11-12",
      url: "https://example.com/continuous-batching",
    },
    {
      position: 2,
      publisher: "Serving Weekly",
      title: "Speculative decoding, a year on",
      published_on: "2026-11-14",
      url: "https://example.com/speculative",
    },
    {
      position: 3,
      publisher: "An unlinkable source",
      title: "Notes from a talk",
      published_on: "2026-11-15",
      url: "javascript:alert(1)",
    },
  ],
};

function activityStrip(): PublicProfile["activity"] {
  const today = new Date("2026-11-24T12:00:00Z");
  const activity = Array.from({ length: 49 }, (_, index) => {
    const day = new Date(today);
    day.setUTCDate(today.getUTCDate() - (48 - index));
    const count = index > 36 ? 1 + (index % 3) : index % 4 === 0 ? 0 : index % 3;
    return { date: day.toISOString().slice(0, 10), count };
  });
  return { current_streak: 12, best_streak: 19, activity };
}

export const MATT_PROFILE: PublicProfile = {
  ...MATT,
  member_since: "2026-08-14",
  activity: activityStrip(),
  paths: [
    {
      id: RUST_PATH_ID,
      title: RUST_PUBLIC_PATH.title,
      level: "some_experience",
      done: 4,
      total: 11,
      current_streak: 12,
    },
    {
      id: "8c3e7a4d-0000-4c8d-ae1f-2a3b4c5d6e70",
      title: "Bayesian statistics",
      level: "some_experience",
      done: 7,
      total: 9,
      current_streak: 0,
    },
    {
      id: "8c3e7a4d-1111-4c8d-ae1f-2a3b4c5d6e71",
      title: "The history of the printing press",
      level: "new_to_it",
      done: 8,
      total: 8,
      current_streak: 0,
    },
  ],
  beats: [
    {
      id: AI_BEAT_ID,
      topic: "AI infrastructure",
      level: "work_in_it",
      cadence: "weekly",
      latest_brief_title: "Inference is getting cheaper faster than training",
      brief_count: 3,
    },
  ],
};

/** Matt's profile, his Rust path, his AI Beat and its latest Brief. */
export function seedPublicWorld(): void {
  seedPublicProfile(MATT_PROFILE);
  seedPublicPath(RUST_PUBLIC_PATH);
  seedPublicBeat(AI_PUBLIC_BEAT);
  seedPublicBrief(AI_PUBLIC_BRIEF);
}
