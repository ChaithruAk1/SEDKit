"""Who is asking, as SED records it.

Only a sign-in proves who someone is. In developer mode and on the command line the Windows account name is recorded
instead, marked as not proven, because nobody had to show it was them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

Method = Literal["microsoft", "google", "github", "developer_mode", "windows"]
Channel = Literal["dashboard", "command_line"]


@dataclass(frozen=True)
class Actor:
    id: str  # the verified e-mail address, or "windows:<account>" when nobody proved who it was
    name: str  # for display
    method: Method  # how they were identified
    verified: bool  # True only when an identity provider proved it
    channel: Channel

    @property
    def reviewer(self) -> str:
        """The name a decision records: the verified address, or `windows:<account>` when nobody proved it. The prefix
        keeps an unproven Windows name (which anyone can set) from ever reading like a signed-in address."""
        return self.id


def windows_user() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"


def developer_actor() -> Actor:
    user = windows_user()
    return Actor(id=f"windows:{user}", name=user, method="developer_mode", verified=False, channel="dashboard")


def command_line_actor() -> Actor:
    user = windows_user()
    return Actor(id=f"windows:{user}", name=user, method="windows", verified=False, channel="command_line")
