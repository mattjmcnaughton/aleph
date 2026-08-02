"""Application configuration via environment variables."""

import datetime
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

# The config-selectable id (TDD §12/D9) that resolves to the deterministic stub
# model instead of an OpenRouter-backed one. Guarded out of production below.
STUB_MODEL_ID = "stub"

# Every ``Settings`` field that names a model slot, in declaration order. Single
# source of truth: the production stub guard (``_forbid_stub_in_production``)
# iterates it, and ``scripts/e2e_backend.py`` stubs each entry. A new slot added
# to ``Settings`` but missed by a hand-synced copy of this list would silently
# escape the guard — that is how ``stub`` would end up serving production
# tutoring. Add the field here and both call sites follow.
MODEL_SLOTS: tuple[str, ...] = (
    "model_outline",
    "model_lesson",
    "model_judge",
    "model_tutor",
    "model_shaper",
)

# The convenient dev default for ``session_secret_key``. It is published in this
# repo, so signing production cookies with it yields forgeable sessions; the
# production guard below rejects it (a bare emptiness check can never fire
# against a truthy default). Shared as a constant so the field default and the
# guard cannot drift apart.
DEV_SESSION_SECRET_KEY = "dev-session-secret-change-me"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    log_level: str = "INFO"
    log_format: str = "json"
    host: str = "0.0.0.0"
    port: int = 8000

    # Optional extra OTLP export target (empty by default, AL-005/AL-003): a
    # non-empty value adds a BatchSpanProcessor in ``telemetry.py``. Kept empty
    # in dev/CI so no exporter dials localhost:4318 (that connection-refused
    # spam was the AL-003 finding); Logfire export is gated by LOGFIRE_TOKEN.
    otel_exporter_otlp_endpoint: str = ""

    database_url: str = "postgresql+asyncpg://localhost:5432/aleph"

    # Generation timings (TDD §5.4 / §14). A model call is bounded by
    # ``generation_timeout_seconds`` so ``failed`` is always reached (no dead
    # spinners); a row stuck in ``generating`` past
    # ``generation_stale_after_seconds`` is treated as failed and re-claimable,
    # so a crashed/restarted process self-heals. Stale MUST exceed the timeout
    # (+ overhead), else a healthy slow generation gets double-claimed — a
    # tested invariant (``_check_generation_timings``), not a comment.
    generation_timeout_seconds: int = 60
    generation_stale_after_seconds: int = 180

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @model_validator(mode="after")
    def _check_generation_timings(self) -> "Settings":
        if self.generation_stale_after_seconds <= self.generation_timeout_seconds:
            msg = (
                "generation_stale_after_seconds "
                f"({self.generation_stale_after_seconds}) must exceed "
                f"generation_timeout_seconds ({self.generation_timeout_seconds}): "
                "otherwise a healthy slow generation is double-claimed (TDD §5.4)."
            )
            raise ValueError(msg)
        return self

    @property
    def generation_timeout(self) -> datetime.timedelta:
        """Per model-call timeout as a timedelta."""
        return datetime.timedelta(seconds=self.generation_timeout_seconds)

    @property
    def generation_stale_after(self) -> datetime.timedelta:
        """Stale-recovery window as a timedelta."""
        return datetime.timedelta(seconds=self.generation_stale_after_seconds)

    # --- AL-030: OpenRouter model routing (TDD §5.3, §14) ---------------------
    # Appended as a self-contained block; the model slots resolve through
    # ``services/openrouter.py``. ``ENV=production`` forbids the ``stub`` id so
    # the deterministic CI/e2e model can never be reached in prod.

    # Deployment environment. A closed ``Literal`` (not a free ``str``) so a
    # typo like ``ENV=prod`` is rejected at startup rather than silently
    # counting as non-production and disabling the stub guard below.
    env: Literal["development", "test", "production"] = "development"

    # OpenRouter credential (empty locally / in CI; the stub needs no key).
    openrouter_api_key: str = ""

    # The three model slots (TDD §8/§5.3). All start on one strong model — no
    # premature tiering; per-slot refinement is driven by evals + cost data.
    model_outline: str = "anthropic/claude-sonnet-5"
    model_lesson: str = "anthropic/claude-sonnet-5"
    # MODEL_JUDGE is **eval-only**: it is read by ``evals/`` (the Layer 2 binary
    # judge, TDD §11) and by nothing on the request path — asserted by
    # ``tests/unit/test_evals_judge.py``. Its refinement direction differs in
    # kind from the other two (§5.3): move it **cross-provider** (e.g.
    # ``openai/gpt-5.6-terra``), because LLM judges show self-preference bias
    # and a Claude judge grading Claude-written lessons would inflate the very
    # ≥ 90% ship gate the judge exists to make trustworthy. Switching provider
    # is this env var plus a re-run of ``just evals --agreement``; judge↔human
    # calibration is the real control either way (docs/evals.md).
    model_judge: str = "anthropic/claude-sonnet-5"

    # Comma-separated OpenRouter ids an admin may select per-request for the
    # outline/lesson slots (the picker allowlist, D14/§5.3), in display order.
    model_allowlist: str = (
        "anthropic/claude-sonnet-5,"
        "anthropic/claude-haiku-4-5,"
        "anthropic/claude-opus-4-8,"
        "openai/gpt-5.6-terra,"
        "minimax/minimax-m3"
    )

    @property
    def allowlist_ids(self) -> tuple[str, ...]:
        """Parsed ``model_allowlist``: stripped, empties dropped, order kept."""
        return tuple(
            candidate.strip()
            for candidate in self.model_allowlist.split(",")
            if candidate.strip()
        )

    @property
    def is_production(self) -> bool:
        """Whether this is a production deployment.

        Exact match: ``env`` is a closed ``Literal`` so no normalization is
        needed — any non-``production`` value could only be ``development`` or
        ``test``, and an out-of-set value never validates.
        """
        return self.env == "production"

    @model_validator(mode="after")
    def _forbid_stub_in_production(self) -> Self:
        """Fail fast at startup if the stub could be reached in production.

        The stub is the deterministic CI/e2e model (D9); reaching it in
        production would silently serve canned content, so it is rejected here
        rather than at resolution time. This covers both the fixed model
        slots *and* the admin picker's ``MODEL_ALLOWLIST`` — once AL-052's
        per-request picker lands, an allowlisted ``stub`` would let an admin
        select it in prod and call ``resolve_model("stub")``, bypassing a
        slot-only guard. The picker's reach grew again in Phase 2 (the tutor
        takes it as a per-message override, §5.3) and in Phase 2B (the shaper
        takes the same per-message override, Phase 2B §5.3/D10), so the
        allowlist arm covers those slots as well.

        The slot arm iterates :data:`MODEL_SLOTS`, so a new slot is guarded the
        moment it is listed there. ``tests/unit/test_config_models.py``
        parametrizes over an independent literal list — deliberately not over
        the constant, which would make the test tautological.
        """
        if self.is_production:
            offenders = [
                slot for slot in MODEL_SLOTS if getattr(self, slot) == STUB_MODEL_ID
            ]
            if STUB_MODEL_ID in self.allowlist_ids:
                offenders.append("model_allowlist")
            if offenders:
                joined = ", ".join(offenders)
                raise ValueError(
                    f"The 'stub' model is not allowed in production (ENV=production); "
                    f"offending slot(s): {joined}."
                )
        return self

    # --- AL-020: OIDC auth + session cookie (TDD §7/D2) -----------------------
    # Appended as a self-contained block. Habagou's provider-agnostic OIDC
    # config: Keycloak in dev/CI, Auth0 in prod (env-only later). The app is the
    # relying party and protects its own API with a first-party signed session
    # cookie holding only the local user UUID.

    # Display + client-registration name of the provider ("keycloak" | "auth0").
    oidc_provider: str = "keycloak"
    # Provider issuer used for OIDC discovery (dev: local Keycloak aleph realm).
    oidc_issuer: str = "http://127.0.0.1:18080/realms/aleph"
    # OIDC web-application client credentials.
    oidc_client_id: str = "aleph"
    oidc_client_secret: str = "aleph-dev-secret"
    # Requested identity claims.
    oidc_scopes: str = "openid profile email"
    # Normally empty; set only when a provider's discovery URL differs from
    # ``<issuer>/.well-known/openid-configuration``.
    oidc_metadata_url: str = ""

    # Signs the first-party session cookie (random Fly secret in production).
    session_secret_key: str = DEV_SESSION_SECRET_KEY
    # Requires HTTPS for the session cookie; ``true`` in production only (forced
    # on in production by ``_enforce_production_auth``).
    session_cookie_secure: bool = False

    @model_validator(mode="after")
    def _enforce_production_auth(self) -> Self:
        """Fail fast if a production deployment is missing real auth secrets.

        The dev defaults are deliberately convenient (a published session
        secret, local-Keycloak OIDC), so nothing but ``env`` distinguishes a
        real deploy from local dev. A bare "is it set?" guard can never fire
        against the truthy dev session secret — a prod deploy that forgets
        ``SESSION_SECRET_KEY`` would sign cookies with a public value, forging
        sessions and impersonating accounts. So in production we require a real
        session secret (non-empty and not the dev default) and non-empty OIDC
        credentials, and force ``session_cookie_secure`` on regardless of the
        supplied value (behind Fly's TLS proxy the cookie must be ``Secure``).
        Dev/test are untouched.
        """
        if not self.is_production:
            return self

        missing = [
            name
            for name in ("oidc_issuer", "oidc_client_id", "oidc_client_secret")
            if not getattr(self, name).strip()
        ]
        if not self.session_secret_key or (
            self.session_secret_key == DEV_SESSION_SECRET_KEY
        ):
            missing.append("session_secret_key")
        if missing:
            joined = ", ".join(missing)
            raise ValueError(
                "Production (ENV=production) requires real auth secrets; set a "
                "non-default value for: "
                f"{joined}."
            )

        # Secure-by-default in production: never emit a session cookie without
        # the ``Secure`` attribute, even if the deploy left the flag unset.
        self.session_cookie_secure = True
        return self

    # --- AL-040: orchestration caps & prefetch (TDD §5.4, §5.2, §14) ----------
    # Appended as a self-contained block (AL-020 also appends config; keep the
    # blocks separate). These map §14's provisional numbers onto env-overridable
    # settings. The service (``services/generation.py``) builds ``OutlineCaps`` /
    # ``LessonCaps`` from these explicitly and passes them to the agents as
    # run-time deps — the agents never read config (habagou purity rule).

    # Lessons generated ahead of the first incomplete one (``PREFETCH_N``, §14):
    # the prefetch window is ``first_incomplete_position + prefetch_n``.
    prefetch_n: int = 2

    # D7 continuity bound (``services/generation.py``'s ``build_prior_context``):
    # the most recent N generated Read passages a lesson's prompt carries
    # verbatim, regardless of how long the path is. Without this, continuity
    # context grows with path position — harmless at the old 30-lesson cap
    # (~19k tokens worst case, TDD §5.2) but unbounded once ``max_lessons_per_path``
    # stopped being a de facto continuity cap too (raised to 200, ~129k tokens
    # worst case: breaks 128k-context models in ``MODEL_ALLOWLIST`` and risks
    # ``GENERATION_TIMEOUT``). 30 is chosen so every path that could exist under
    # the OLD 30-lesson cap sees byte-identical continuity behaviour — only
    # paths longer than the old cap are affected, and even then nothing is
    # structurally lost: the lesson prompt already carries the full outline
    # (unit + lesson titles) via ``_load_outline``, so older lessons stay
    # *named* in the prompt, they just stop contributing full passage text.
    continuity_passages_max: int = 30

    # Outline sizing (§14): ``*_target``/band are prompt targets, ``max_*`` are
    # the hard validator caps the outline agent enforces (§5.1). Fed into
    # ``OutlineCaps``, whose own ``__post_init__`` rejects an incoherent set.
    #
    # ``max_units``/``lessons_per_unit_max``/``max_lessons_per_path`` are safety
    # CEILINGS, not product limits — they exist to bound a pathological or
    # adversarial outline, not to cap how big a legitimately large topic (or a
    # learner's Guidance asking for a bigger path) may be. Outline size follows
    # the topic and the learner's Guidance (CONTEXT.md); the ``*_target``/band
    # numbers are what that sizing aims for by default, and are what the caps
    # sit far above (``agents/outline.py``'s ``OutlineCaps`` docstring).
    outline_units_target: int = 5
    max_units: int = 25
    lessons_per_unit_min: int = 3
    lessons_per_unit_max: int = 8
    max_lessons_per_path: int = 200

    # Read-passage word band (``READ_PASSAGE_WORDS`` ~200-500, §14). Fed into
    # ``LessonCaps`` (the option count stays the fixed single-select 3-4 band).
    read_passage_words_min: int = 200
    read_passage_words_max: int = 500

    # --- AL-021: derived admin (TDD §7/D14) -----------------------------------
    # Appended as a self-contained block. Admin status is derived from the
    # user's email domain at request time (see ``aleph.authz.is_admin``), never
    # stored: the email is refreshed from the identity provider on every
    # sign-in, so classification self-heals and needs no migration or admin UI.

    # Comma-separated email domains whose users are admins. Matched exactly (no
    # subdomains) and case-insensitively against the part after the final
    # ``@``. Default is the sole first-party operator.
    admin_email_domains: str = "mattjmcnaughton.com"

    @property
    def admin_email_domain_set(self) -> frozenset[str]:
        """Parsed ``admin_email_domains``: lowercased, stripped, empties dropped."""
        return frozenset(
            domain.strip().lower()
            for domain in self.admin_email_domains.split(",")
            if domain.strip()
        )

    # --- AL-042: per-account daily rate limits (TDD §10 / §14 D13) ------------
    # Appended as a self-contained block. Cheap insurance on the §7 cost
    # guardrail: caps how many paths a learner may create and how many lessons
    # they may trigger generation for per calendar day, checked in the service
    # layer against real row counts (see ``services.rate_limit``). Admins are
    # exempt at the call site. A cap of 0 or negative disables that cap.
    rate_limit_paths_per_day: int = 10
    rate_limit_lesson_generations_per_day: int = 100

    # --- AL-041: reconciler & global concurrency bound (TDD §5.4, §14) --------
    # Appended as a self-contained block at the END of Settings (other AL-0xx
    # branches append their own config blocks; keep them separate to avoid merge
    # conflicts). These wire the in-process reconciler loop and the process-wide
    # semaphore that caps concurrent model calls (``services/lifecycle.py``).

    # How often the reconciler scans for claimable work — stale ``generating``
    # rows and paths with unfilled prefetch windows (``RECONCILER_INTERVAL``,
    # §14). A crashed generation chain resumes within one tick of the stale
    # timeout instead of waiting for a learner's poll. Must be positive
    # (``gt=0``): a non-positive interval would busy-spin the loop.
    reconciler_interval_seconds: float = Field(default=30.0, gt=0)

    # Process-wide ceiling on concurrent model calls (``MAX_CONCURRENT_GENERATIONS``,
    # §14). Per-path work is already serialized by the ordering invariant; this
    # bounds *aggregate* load across all paths so spend/latency spikes queue
    # instead of fanning out (e.g. 50 simultaneous path creations do not become
    # 50 concurrent model calls). Enforced by a semaphore wrapping each
    # generation (NOT the whole task — a whole-task bound would deadlock the
    # serial per-path chain that awaits its sub-generations inline). Must admit at
    # least one permit (``ge=1``): zero would deadlock every generation.
    max_concurrent_generations: int = Field(default=8, ge=1)

    # --- AL-005: Logfire instrumentation (TDD §9 / D11) -----------------------
    # Appended as a self-contained block at the END of Settings (other AL-0xx
    # branches append their own config blocks; keep them separate to avoid merge
    # conflicts). Logfire is the single telemetry sink — spans and structlog
    # events both flow to it (see ``telemetry.py`` / ``logging.py``).

    # The Logfire write token is the ONLY switch that turns on network export.
    # Unset (the dev/CI default) yields a clean no-op: with
    # ``send_to_logfire="if-token-present"`` no exporter is created and nothing
    # dials the network. Set the real project token via ``LOGFIRE_TOKEN`` in
    # production (a Fly secret).
    logfire_token: str = ""

    # --- AL-201: the tutor (Phase 2 TDD §5.3, §13, D4/D8/D9) ------------------
    # Appended as this phase's self-contained block at the END of Settings
    # (every AL-xxx branch appends its own block; keep them separate to avoid
    # merge conflicts). All numbers here are §13's provisional ones.

    # The fourth model slot, resolved through ``services/openrouter.py`` like the
    # rest. Starts on the same strong model as every other slot (D4's
    # uniform-start discipline); §5.3's refinement direction for this one is
    # *down* (e.g. ``anthropic/claude-haiku-4-5``) once tutor evals hold and TTFT
    # data favors it — streaming already hides most perceived latency, so the
    # move waits for evidence. **It is also listed in ``MODEL_SLOTS``** — the
    # production stub guard iterates that constant, so a slot missing from it
    # would let the deterministic stub serve production tutoring. Admins may
    # override it per message (never persisted, §5.3); the override rides the
    # same shared ``model_allowlist``.
    model_tutor: str = "anthropic/claude-sonnet-5"

    # Carried-history window in *turns* (a learner message + its tutor reply, as
    # a unit), most recent first, dropped rather than summarized (D6). Bounded
    # is the invariant — the number is tunable, and the summary upgrade slots in
    # behind ``services/tutor_context.py`` without touching this. Must be
    # positive (``ge=1``): a zero window would send every turn contextless.
    tutor_context_turns: int = Field(default=10, ge=1)

    # Whole-stream bound in seconds on a single reply: a hung provider ends in a
    # terminal ``error`` event, never a dead stream (§5.4). Must be positive
    # (``gt=0``): zero would time out every reply before its first token.
    tutor_reply_timeout: int = Field(default=90, gt=0)

    # Process-wide ceiling on concurrent tutor replies — deliberately its *own*
    # semaphore, isolated from ``max_concurrent_generations`` (D9): a learner
    # waiting mid-sentence must not queue behind batch prefetch work. Must admit
    # at least one permit (``ge=1``): zero would deadlock every reply.
    max_concurrent_tutor_replies: int = Field(default=8, ge=1)

    # PRD §5.7's cap knob, counted over live learner-message rows by the Phase 1
    # limiter. Ships **disabled** (D8): 0 or negative disables the cap, matching
    # the other ``rate_limit_*`` settings. The refund-proof usage table (one-tap
    # "new conversation" must not refund quota) is the recorded precondition for
    # ever raising this above 0 — deliberately not built while the cap is off.
    rate_limit_tutor_messages_per_day: int = 0

    # How often the SSE stream emits a ``: ping`` comment frame during model
    # silence (§5.4), in seconds, so proxy idle timeouts never kill a healthy
    # stream. Must be positive (``gt=0``): zero would busy-write the socket.
    sse_heartbeat_seconds: float = Field(default=15.0, gt=0)

    # --- AL-203: feature flags (epic #82, owner amendment 1) ------------------
    # Appended as a self-contained block at the END of Settings (every AL-xxx
    # branch appends its own; keep them separate to avoid merge conflicts).
    # Flags are defined in code (``services/feature_flags.py``); this setting
    # overrides their **global defaults**, so a flag flips without a code
    # deploy. Both launched flags (``tutor``, ``shaping``) default **on** in that
    # registry rather than here, so this setting is normally *empty* — it is the
    # override and the kill switch (``tutor:off``), not the statement of what is
    # live.
    #
    # Comma-separated ``key:on`` / ``key:off`` entries. Malformed entries are
    # dropped rather than raising: this is an operator knob turned under
    # pressure (a mid-incident kill switch), and a typo that refuses to boot is
    # a worse failure than one that leaves the default in place. Keys the code
    # registry does not know are ignored for the same reason — an entry left
    # behind by a deleted flag must never keep a deploy from starting. Per-user
    # database overrides (the admin API) still win over these defaults: this
    # moves the default, it does not force the flag on anyone holding an
    # override.
    feature_flag_defaults: str = ""

    @property
    def feature_flag_default_map(self) -> dict[str, bool]:
        """Parsed ``feature_flag_defaults``: malformed entries dropped."""
        parsed: dict[str, bool] = {}
        for entry in self.feature_flag_defaults.split(","):
            key, separator, state = entry.strip().partition(":")
            key, state = key.strip(), state.strip().lower()
            if separator and key and state in ("on", "off"):
                parsed[key] = state == "on"
        return parsed

    # --- AL-301: shaping (Phase 2B TDD §5.3, §13, D10/D11) --------------------
    # Appended as this phase's self-contained block at the END of Settings
    # (every AL-xxx branch appends its own block; keep them separate to avoid
    # merge conflicts). All numbers here are Phase 2B §13's provisional ones.
    #
    # Deliberately *absent* from this block: a carried-turn window, a reply
    # timeout and a concurrency bound for shaping replies. §13 reuses the tutor's
    # ``tutor_context_turns`` / ``tutor_reply_timeout`` /
    # ``max_concurrent_tutor_replies`` on purpose — one notion of "recent
    # conversation", and one budget shared by the two interactive reply kinds
    # (D11). Adding parallel knobs here would be the easy mistake; splitting them
    # is a trivial follow-up if Logfire ever shows contention.

    # The fifth model slot, resolved through ``services/openrouter.py`` like the
    # rest. Starts on the same strong model as every other slot (the
    # uniform-start discipline); §5.3's refinement direction for this one is *up
    # or sideways*, never down — proposal structure quality is the product, and a
    # bad Proposal burns learner trust plus real generation spend on Apply, while
    # TTFT matters less than for the tutor (the payoff is a card, not prose).
    # **It is also listed in ``MODEL_SLOTS``** — the production stub guard
    # iterates that constant, so a slot missing from it would let the
    # deterministic stub propose production path edits. Admins may override it
    # per message (never persisted, §5.3); the override rides the same shared
    # ``model_allowlist`` as the other slots.
    model_shaper: str = "anthropic/claude-sonnet-5"

    # Hard cap on the lessons a single Proposal may add or revise (§13): both the
    # validator's bound and the prompt's instruction, so one Proposal stays small
    # and legible (PRD §5.4) — a bigger ask becomes two Proposals rather than one
    # unreadable card. Must be positive (``ge=1``): a zero cap would reject every
    # Proposal the shaper could make.
    max_lessons_per_proposal: int = Field(default=5, ge=1)

    # PRD §7's cap knob for shaping messages, counted over live learner-message
    # rows by the Phase 1 limiter like the other ``rate_limit_*`` settings. Ships
    # **disabled**, the same posture as ``rate_limit_tutor_messages_per_day``: 0
    # or negative disables the cap. The refund-proof usage table (one-tap "new
    # conversation" must not refund quota) is the recorded precondition for ever
    # raising this above 0 — deliberately not built while the cap is off.
    rate_limit_shaping_messages_per_day: int = 0

    # --- Phase 5: streaks (TDD §13, D1/D12) -----------------------------------
    # Appended as this phase's self-contained block at the END of Settings
    # (every phase branch appends its own; keep them separate to avoid merge
    # conflicts). This is the **only** knob the slice adds — no model slot, no
    # timeout, no semaphore, no rate limiter, because D1's whole payoff is a
    # feature derived from existing rows rather than one with new machinery to
    # tune.

    # The activity strip's window (§8, D12): how many day-cells
    # ``services/progress_read.py`` asks ``domains.streaks.activity_window``
    # for, oldest first, ending "today". The pure domain module and the
    # repository both take no config (the purity rule, ``domains/__init__.py``)
    # — only the service reads this setting. Must be positive (``ge=1``): a
    # non-positive window would ask the domain for zero or negative cells.
    #
    # **49, not 45** — TDD §15's open window question, settled the way D12's own
    # geometry argues: the strip is a 7-row × 7-column week grid, and 7×7 is 49.
    # Shipping 45 into it meant four permanently blank leading cells and a rule
    # to produce them; asking for 49 deletes both and buys four more days of
    # history. The grid is exactly full, which is what makes "one column is one
    # week" true by construction rather than by a pad that happens to be right.
    streak_activity_window_days: int = Field(default=49, ge=1)


settings = Settings()
