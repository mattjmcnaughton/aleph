"""Pure domain logic — no I/O, no app imports (TDD §3).

Modules here derive product rules from **plain data**: they import stdlib only
(``dataclasses``, ``enum``, ``datetime``, ``collections.abc``) and never touch
SQLAlchemy models, repositories, services, config, or the DB. That keeps the
rules trivially testable (pure data in, pure data out) and reusable across the
service layer, the eval harness, and any future caller.

The **boundary contract**: services map ORM rows to the small frozen input
dataclasses defined here (:class:`~aleph.domains.progression.LessonProgress`,
:class:`~aleph.domains.grading.Attempt`) before calling in, and map the returned
plain values back onto their rows/DTOs. The domain never sees an ORM object.
"""
