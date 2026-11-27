"""Material wire types (CONTEXT.md: *Material*, *Material mode*).

``MaterialInput`` is one item in ``POST /api/v1/paths``'s ``material`` list; a
link (``article`` or ``docs``) carries a URL, a paste (``text`` or
``ai_summary``) carries the text and may carry the link it was copied from.
"""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, StringConstraints, model_validator

from aleph.domains.material import LINK_KINDS, MAX_TEXT_CHARS, MaterialKind

UrlStr = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, max_length=2048, pattern=r"^https?://[^\s/$.?#][^\s]*$"
    ),
]
MaterialTitleStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
MaterialTextStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT_CHARS),
]


class MaterialInput(BaseModel):
    kind: MaterialKind
    url: UrlStr | None = None
    title: MaterialTitleStr | None = None
    text: MaterialTextStr | None = None

    @model_validator(mode="after")
    def _shape_matches_kind(self) -> Self:
        if self.kind in LINK_KINDS:
            if self.url is None or self.text is not None:
                raise ValueError("a link needs a url and no text")
        elif self.text is None:
            raise ValueError("pasted Material needs its text")
        return self


class MaterialDTO(BaseModel):
    """One item of a path's Material, as the path detail shows it."""

    kind: MaterialKind
    title: str
    url: str | None
