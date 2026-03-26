from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SkillRequest(BaseModel):
    capability: str = Field(..., description="The capability to resolve and execute")
    input_data: Dict[str, Any] = Field(default_factory=dict)
    agent_id: str = Field(default="default-agent")


class ResolutionStrategy(str, Enum):
    NATIVE_CAPABILITY = "native_capability"
    LOCAL_CACHE = "local_cache"
    CLAWHUB_RETRIEVAL = "clawhub_retrieval"
    SYNTHESIS = "synthesis"


class PublishState(str, Enum):
    ACTIVE = "active"
    QUARANTINED = "quarantined"


class CapabilityGap(BaseModel):
    capability: str
    reason: str
    attempted_strategies: List[str] = Field(default_factory=list)


class SkillResponse(BaseModel):
    success: bool
    capability: str
    result: Optional[Any] = None
    resolution_strategy: Optional[ResolutionStrategy] = None
    publish_state: Optional[PublishState] = None
    capability_gaps: List[CapabilityGap] = Field(default_factory=list)
    error: Optional[str] = None
