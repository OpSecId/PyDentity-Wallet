from typing import Any, Literal, Optional

from pydantic import Field

from .base import CustomBaseModel

VcalmExchangeStatus = Literal[
    "awaiting_presentation_consent",
    "awaiting_store_consent",
    "processing",
    "complete",
    "abandoned",
    "error",
]


class VcalmExchange(CustomBaseModel):
    id: str = Field()
    wallet_id: str = Field()
    exchange_url: str = Field()
    protocol: str = Field()
    status: VcalmExchangeStatus = Field()
    pending_vpr: Optional[dict[str, Any]] = None
    pending_vp: Optional[dict[str, Any]] = None
    context_message: Optional[dict[str, Any]] = None
    stored_credentials: int = Field(default=0)
    presented: bool = Field(default=False)
    step: int = Field(default=0)
    created_at: str = Field()
    expires_at: str = Field()
    preview: dict[str, Any] = Field(default_factory=dict)
    result: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
