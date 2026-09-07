"""Feedback vocabulary shared by the light CLI, HTTP and MCP."""

from typing import Literal, get_args

FeedbackCategory = Literal["quality", "pricing", "friction", "other"]
FEEDBACK_CATEGORIES = get_args(FeedbackCategory)
FEEDBACK_DESCRIPTION = (
    "Share a problem or suggestion about treg. Categories: quality (tool results), "
    "pricing (charges or prices), friction (using treg), other (requests or suggestions). "
    "Describe what happened in your own words. Include related call IDs if available. "
    "Omit private information, credentials, and raw requests, responses or logs."
)
