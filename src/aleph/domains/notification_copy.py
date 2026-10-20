"""Plain lock-screen copy and delivery metadata, with no I/O."""

from dataclasses import dataclass
from enum import StrEnum


class NotificationKind(StrEnum):
    PATH_READY = "path_ready"
    REMINDER = "reminder"
    BRIEF_PUBLISHED = "brief_published"


@dataclass(frozen=True)
class PushMessage:
    kind: NotificationKind
    body: str
    target: str
    tag: str

    @property
    def ttl(self) -> int:
        return {
            NotificationKind.PATH_READY: 3600,
            NotificationKind.REMINDER: 21600,
            NotificationKind.BRIEF_PUBLISHED: 86400,
        }[self.kind]

    def payload(self, base_url: str) -> dict:
        return {
            "web_push": 8030,
            "notification": {
                "title": "aleph",
                "body": self.body,
                "navigate": base_url.rstrip("/") + self.target,
                "tag": self.tag,
                "lang": "en",
            },
        }


def path_ready_message(path_id: str, title: str, outcome: str) -> PushMessage:
    body = {
        "ready": f"Your path on {title[:200]} is ready.",
        "refused": "We couldn't make a path on that topic.",
        "failed": "Your path didn't finish building. Tap to retry.",
    }[outcome]
    return PushMessage(
        NotificationKind.PATH_READY, body, f"/paths/{path_id}", f"path-{path_id}"
    )


def reminder_message(
    local_date: str, target: str = "/", streak: int = 0
) -> PushMessage:
    body = (
        f"Keep your {streak}-day streak going."
        if streak
        else "Pick up where you left off."
    )
    return PushMessage(
        NotificationKind.REMINDER, body, target, f"reminder-{local_date}"
    )


def brief_published_message(brief_id: str, topic: str, number: int) -> PushMessage:
    return PushMessage(
        NotificationKind.BRIEF_PUBLISHED,
        f"{topic[:200]}: Brief #{number} is ready.",
        f"/briefs/{brief_id}",
        f"brief-{brief_id}",
    )
