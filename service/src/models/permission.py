import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.database import Base


class ResourcePermission(Base):
    __tablename__ = "resource_permissions"
    __table_args__ = (
        UniqueConstraint(
            "service_name", "resource_type", "resource_id", name="uq_resource_identity"
        ),
        CheckConstraint("visibility IN ('private', 'workspace')", name="ck_visibility"),
        Index("ix_resource_permissions_workspace", "workspace_id"),
        Index("ix_resource_permissions_owner", "owner_id"),
        Index(
            "ix_resource_permissions_lookup",
            "service_name",
            "resource_type",
            "resource_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    service_name: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    visibility: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="workspace"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    shares: Mapped[list["ResourceShare"]] = relationship(
        back_populates="resource_permission", cascade="all, delete-orphan"
    )


class ResourceShare(Base):
    __tablename__ = "resource_shares"
    __table_args__ = (
        UniqueConstraint(
            "resource_permission_id",
            "grantee_type",
            "grantee_id",
            name="uq_resource_share",
        ),
        CheckConstraint("grantee_type IN ('user', 'group')", name="ck_grantee_type"),
        CheckConstraint("permission IN ('view', 'edit')", name="ck_share_permission"),
        Index("ix_resource_shares_grantee", "grantee_type", "grantee_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    resource_permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resource_permissions.id", ondelete="CASCADE")
    )
    grantee_type: Mapped[str] = mapped_column(Text, nullable=False)
    grantee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    permission: Mapped[str] = mapped_column(Text, nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    resource_permission: Mapped["ResourcePermission"] = relationship(
        back_populates="shares"
    )
