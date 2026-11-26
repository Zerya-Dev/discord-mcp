"""Who an MCP request acts for, and how the host can authenticate requests itself."""

import contextvars
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from zerya_discord_mcp.access.grants import Grant


@dataclass(frozen=True, slots=True)
class Principal:
    """Who a request acts for."""

    user_id: int
    grant: Grant | None = None

    @property
    def can_write(self) -> bool:
        return self.grant is None or self.grant.can_write


type Authenticate = Callable[[Mapping[str, str]], Awaitable[Principal | None]]

current_principal: contextvars.ContextVar[Principal | None] = contextvars.ContextVar("zdm_principal", default=None)
