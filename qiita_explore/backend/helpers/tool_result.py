"""ToolResult — what every chat tool returns. Its own module so tool modules
(helpers/agent_tools.py, helpers/study_tools.py) can share it without importing
each other."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ToolResult:
    text: str                          # Fed back to the model as a tool message
    label: str                         # Shown in step_done UI
    detail: str = ""                   # Shown as sub-label in step_done UI
    ui_payload: Optional[dict] = None  # If set, emitted as a `ui` SSE event
    executed: bool = True              # False when no real work happened (e.g.
                                       # empty-input early return) — such calls
                                       # must not consume a search-budget slot.
