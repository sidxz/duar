import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from src.database import Base


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        # At most one public org — enforced by a partial unique index.
        Index(
            "uq_one_public_org",
            "is_public",
            unique=True,
            postgresql_where=text("is_public"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # For the public org, `enabled` is the public-sign-in switch. For a real org,
    # `enabled=False` is a kill-switch that blocks all of its users at next login.
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    domains: Mapped[list["OrganizationDomain"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class OrganizationDomain(Base):
    __tablename__ = "organization_domains"
    __table_args__ = (
        UniqueConstraint("domain", name="uq_org_domain"),
        Index("ix_organization_domains_org_id", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE")
    )
    domain: Mapped[str] = mapped_column(Text, nullable=False)
    include_subdomains: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    organization: Mapped["Organization"] = relationship(back_populates="domains")


class WorkspaceAllowedOrganization(Base):
    __tablename__ = "workspace_allowed_organizations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "organization_id", name="uq_workspace_allowed_org"
        ),
        Index("ix_workspace_allowed_orgs_workspace_id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    # RESTRICT (not CASCADE): an org wired into a workspace's allow-list must not be
    # deletable out from under it. A cascade could empty the list, and an empty
    # allow-list means 'open to all' — silently flipping a locked-down workspace
    # open. delete_organization refuses the delete with a clear message first; this
    # FK is the race-proof DB backstop.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
