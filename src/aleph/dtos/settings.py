"""Learner Settings API DTOs (CONTEXT.md: Settings / Auto-draft).

Separate from the ORM model as always: the wire shape is the learner's
*effective* settings — every setting, defaults filled in — while the table
holds only the row of a learner who has changed something.
"""

from pydantic import BaseModel, ConfigDict


class SettingsDTO(BaseModel):
    """``GET``/``PATCH /settings``'s body, and ``user.settings`` on the session
    probe: every setting with its effective value. The defaults here restate
    ``services/user_settings.py``'s (pinned equal by a unit test) so a client
    reading an older payload still sees the right answer."""

    auto_draft_flashcards: bool = True


class SettingsUpdateDTO(BaseModel):
    """``PATCH /settings``'s body: any subset of the settings.

    ``extra="forbid"`` makes a misspelt or not-yet-shipped setting a ``422``
    rather than a silent no-op — a client that thinks it turned something off
    must never be told ``200``. An empty body is allowed and changes nothing.
    """

    model_config = ConfigDict(extra="forbid")

    auto_draft_flashcards: bool | None = None
