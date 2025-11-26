# bots/base.py

from dataclasses import dataclass
from typing import Optional


@dataclass
class ApplyResult:
    status: str  # "success", "failed", "retry"
    message: str = ""


class BaseATSBot:
    async def apply(self, job, user):
        raise NotImplementedError("Subclasses must implement apply()")

@dataclass
class ApplyResult:
    status: str
    message: str
    screenshot_url: Optional[str] = None

