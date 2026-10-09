"""FastAPI 依赖。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.config import Settings, get_settings
from app.db import Database, get_database
from app.llm import LlmClient, get_llm_client
from app.observability import Observability, get_observability

SettingsDep = Annotated[Settings, Depends(get_settings)]
DatabaseDep = Annotated[Database, Depends(get_database)]
LlmDep = Annotated[LlmClient, Depends(get_llm_client)]
ObservabilityDep = Annotated[Observability, Depends(get_observability)]
