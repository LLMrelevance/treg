"""Feedback vocabulary shared by the light CLI, HTTP and MCP."""

from typing import Literal, get_args

FeedbackCategory = Literal["quality", "pricing", "friction", "other"]
FEEDBACK_CATEGORIES = get_args(FeedbackCategory)
FEEDBACK_DESCRIPTION = (
    "Report problems or suggestions about treg, including confusing results or charges, "
    "unclear instructions, and limitations you worked around, even if the task succeeded. "
    "Describe what you needed and what you observed; you do not need to prove a bug. "
    "Categories: quality (tool results), pricing (charges or prices), friction (using treg), "
    "other (requests or suggestions). Pass related call IDs in call_ids (CLI: --call-id), "
    "not only in message. References are optional. Omit private information, credentials, "
    "and raw requests, responses or logs. Report each issue once, then continue the user's task."
)
