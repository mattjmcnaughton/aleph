"""pydantic-ai agent definitions (habagou purity rules, TDD §5.1).

Agents bind **no model** and import no config, services, or DB — the model is
supplied at run time (via ``agent.run(..., model=...)`` or resolved through
``services/openrouter.py``). This package currently holds only the agents'
**output schemas** (AL-030): the outline/lesson Pydantic models the deterministic
stub model must satisfy. The assembled agents (system prompts, tools, output
validators) land in AL-031 (outline) and AL-032 (lesson) and build on these.
"""
