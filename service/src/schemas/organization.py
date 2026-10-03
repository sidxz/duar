"""Internal org directory schema (service-key surface)."""

import uuid

from pydantic import BaseModel


class OrgDirectoryEntry(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    is_public: bool
    enabled: bool
