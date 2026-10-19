-- App adoption (iOS PRD §7 supporting metric, TDD §12).
--
-- Per week: the % of active accounts with at least one 'ios' event that week.
-- "Active account": any learner action that week, from any client. Weeks are
-- date_trunc('week') in UTC (Monday-based). ``client`` is server-derived and
-- absent on events no request caused (the reconciler, the scheduled job), so
-- system work never makes an account look like an app user.
--
-- Events used: the learner actions below (account_id, client).
WITH learner_actions AS (
    SELECT
        attributes ->> 'account_id' AS account_id,
        attributes ->> 'client' AS client,
        date_trunc('week', start_timestamp) AS week
    FROM records
    WHERE span_name IN (
        'path_created', 'lesson_viewed', 'quick_check_attempted',
        'lesson_completed', 'path_deleted', 'tutor_message_sent',
        'tutor_check_answered', 'shaping_message_sent', 'change_applied',
        'change_undone', 'beat_deployed', 'brief_read'
    )
)
SELECT
    week,
    count(DISTINCT account_id) AS active_accounts,
    count(DISTINCT account_id) FILTER (WHERE client = 'ios') AS app_accounts,
    count(DISTINCT account_id) FILTER (WHERE client = 'ios')::float
        / nullif(count(DISTINCT account_id), 0) AS app_adoption
FROM learner_actions
GROUP BY week
ORDER BY week;
