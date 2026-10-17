# Store the learner's last-seen time zone

**Status:** accepted (2026-10-17). Partly supersedes the Phase 6 PRD §4.2 and `CONTEXT.md` (*Anchor day*), which deliberately stored no time zone and read local time only from each request's `tz_offset_minutes`.

The iOS app adds work that has to run while no request is in flight:
- daily push **Reminders** at a learner-chosen local hour;
- an hourly scheduled job that runs Beats on their **Anchor day** and publishes Briefs while the learner is away.

Neither can read a request's offset. We store the learner's **last-seen time zone** on their account instead. It is an IANA zone name, so daylight saving is handled correctly. Every client reports it, silently, on its requests, and the server keeps the latest value. Reminders and the scheduled job read it. A request that carries its own offset still uses that offset for itself.

**Considered options**
- *Local notifications scheduled on the phone:* rejected. The phone can't see a lesson completed on the web, and the Beats job would still have no time zone.
- *A time zone attached to each phone's push registration:* rejected. Learners without a phone still need one for the Beats job.
- *A user-chosen time zone setting:* rejected. It would be one more setting to maintain, and it goes stale when the learner travels.

**Consequences:** "last seen" is stale only until the learner's next request after travelling. The time zone is display-free: no settings screen shows it.
