"""Shared Pydantic Topic/Guidance constraints, independent of agents and API DTOs.

Not domain logic: the domains package deliberately remains stdlib-only.
"""

from typing import Annotated

from pydantic import StringConstraints

TopicStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]
GuidanceStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
]
