-- Return through the app, part 1 (iOS PRD §7 primary metric, TDD §12).
--
-- Among accounts that use both clients, the share of their Active days on which
-- the app was used. "Active day" is CONTEXT.md's: a Day with at least one lesson
-- completion (from either client). "The app was used" that Day: the account
-- emitted at least one learner-action event carrying client = 'ios'. "Uses both
-- clients": the account has learner actions with client = 'web' AND with
-- client = 'ios'. "Day" is a calendar day in UTC, like every query here (see
-- metrics.md "Notes & known limits").
--
-- ``client`` is server-derived (cookie -> web, the mapped iOS bearer client ->
-- ios, any other bearer client -> unknown) and absent on events no request
-- caused, so system work (the reconciler, the scheduled job) never counts as use.
--
-- Events used: the learner actions below (client), lesson_completed (Active days).
WITH learner_actions AS (
    SELECT
        attributes ->> 'account_id' AS account_id,
        attributes ->> 'client' AS client,
        date_trunc('day', start_timestamp) AS day
    FROM records
    WHERE span_name IN (
        'path_created', 'lesson_viewed', 'quick_check_attempted',
        'lesson_completed', 'path_deleted', 'tutor_message_sent',
        'tutor_check_answered', 'shaping_message_sent', 'change_applied',
        'change_undone', 'beat_deployed', 'brief_read'
    )
),
both_clients AS (
    SELECT account_id
    FROM learner_actions
    GROUP BY account_id
    HAVING count(*) FILTER (WHERE client = 'web') > 0
       AND count(*) FILTER (WHERE client = 'ios') > 0
),
active_days AS (
    SELECT DISTINCT
        attributes ->> 'account_id' AS account_id,
        date_trunc('day', start_timestamp) AS day
    FROM records
    WHERE span_name = 'lesson_completed'
),
app_days AS (
    SELECT DISTINCT account_id, day
    FROM learner_actions
    WHERE client = 'ios'
)
SELECT
    count(DISTINCT a.account_id) AS accounts_on_both_clients,
    count(*) AS active_days,
    count(app.day)::float / nullif(count(*), 0) AS app_active_day_share
FROM active_days AS a
JOIN both_clients AS b ON b.account_id = a.account_id
LEFT JOIN app_days AS app ON app.account_id = a.account_id AND app.day = a.day;
