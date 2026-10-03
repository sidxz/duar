import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.organization import (
    Organization,
    OrganizationDomain,
    WorkspaceAllowedOrganization,
)
from src.models.user import User

# A clean LDH (letter-digit-hyphen) hostname of at least two labels, ≤253 chars.
# Applied to the IDNA-encoded (pure-ASCII) result so pasted junk like
# "https://x.y", "x.y:443", or "a b.com" — which Python's idna codec passes
# through unchanged for ASCII labels — is rejected.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


class OrgDisabled(ValueError):
    """The user's (real) organization is disabled — a kill-switch.

    Subclasses ValueError so existing token-route handlers that map ValueError
    to 403 keep working without a new except clause.
    """


def normalize_domain(value: str | None) -> str | None:
    """Extract and normalize the domain from an email or bare domain.

    Lowercases, strips, takes the part after a single '@' (if present),
    IDNA-encodes unicode labels, and returns None for anything malformed. This
    keys org lookups, so it must fail closed:

    - rejects empty, multiple '@', no dot, leading/trailing dot, null byte, or
      over the RFC 1035 253-char limit;
    - rejects inputs a Unicode label separator (U+3002/U+FF0E/U+FF61) would
      split into extra labels, so ``evil.com。tamu.edu`` cannot normalize into
      ``evil.com.tamu.edu`` and suffix-match a victim org's subdomain rule;
    - rejects results that are not a clean LDH hostname (Python's idna codec
      does not validate pure-ASCII labels, so URLs/ports/spaces slip through it).
    """
    if not value:
        return None
    candidate = value.strip().lower()
    if candidate.count("@") > 1:
        return None
    if "@" in candidate:
        candidate = candidate.split("@", 1)[1]
    if not candidate or "." not in candidate:
        return None
    if candidate.startswith(".") or candidate.endswith("."):
        return None
    if "\x00" in candidate or len(candidate) > 253:
        return None
    ascii_labels = candidate.count(".") + 1
    try:
        encoded = candidate.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return None
    # idna must not change the label structure. A Unicode full-stop variant maps
    # to '.' during encoding, adding a label boundary the structural checks above
    # (which only see the ASCII '.') never accounted for. Fail closed on any drift.
    if encoded.count(".") + 1 != ascii_labels:
        return None
    if not _HOSTNAME_RE.match(encoded):
        return None
    return encoded


def match_org_id(
    domain: str,
    rows: list[tuple[uuid.UUID, str, bool]],
) -> uuid.UUID | None:
    """Pure resolver over (org_id, rule_domain, include_subdomains) rows. Exact
    match is authoritative; otherwise the most specific (longest) subdomain rule
    whose pattern the domain is a sub-label of wins.

    ``domain`` is lowercased defensively so the function is correct even if a
    caller passes a non-normalized value (rules are lowercased below too).
    """
    domain = domain.lower()
    best_id: uuid.UUID | None = None
    best_len = -1
    for org_id, rule_domain, include_subdomains in rows:
        rule = rule_domain.lower()
        if domain == rule:
            return org_id
        if include_subdomains and domain.endswith("." + rule) and len(rule) > best_len:
            best_len = len(rule)
            best_id = org_id
    return best_id


async def resolve_organization(db: AsyncSession, email: str) -> Organization | None:
    """Resolve the organization a user's email belongs to, applying sign-in policy.

    1. Normalize the domain (fail-closed on malformed).
    2. Match the domain against *enabled* orgs' rules (exact, then longest
       subdomain). A disabled org never shadows an enabled org that also claims
       the same (sub)domain — the enabled org wins.
    3. If no enabled org claims it but a *disabled* org does, the domain belongs
       to a kill-switched tenant: return None — block sign-in, do NOT funnel its
       users into the public catch-all.
    4. An otherwise-unclaimed domain falls back to the enabled public org.
    5. None => sign-in not permitted.
    """
    domain = normalize_domain(email)
    if domain is None:
        return None

    # Only a rule that IS the domain or one of its parent suffixes can ever match
    # (exact, or subdomain-of). Querying just that small candidate set hits the
    # unique `domain` index instead of scanning every org's domains on each
    # sign-in. e.g. "a.b.tamu.edu" -> ["a.b.tamu.edu", "b.tamu.edu", "tamu.edu", "edu"].
    labels = domain.split(".")
    candidates = [".".join(labels[i:]) for i in range(len(labels))]

    rows = (
        await db.execute(
            select(
                OrganizationDomain.organization_id,
                OrganizationDomain.domain,
                OrganizationDomain.include_subdomains,
                Organization.enabled,
            )
            .join(Organization, Organization.id == OrganizationDomain.organization_id)
            .where(OrganizationDomain.domain.in_(candidates))
        )
    ).all()

    # Prefer an enabled org's claim so a disabled org's (possibly more specific)
    # rule can never shadow an enabled org that also covers the domain.
    enabled_rows = [(oid, dom, sub) for oid, dom, sub, enabled in rows if enabled]
    matched = match_org_id(domain, enabled_rows)
    if matched is not None:
        return await db.get(Organization, matched)

    # No enabled org claims it. If a disabled org does, this is a kill-switched
    # tenant: block (do NOT fall through to the public catch-all).
    disabled_rows = [(oid, dom, sub) for oid, dom, sub, enabled in rows if not enabled]
    if match_org_id(domain, disabled_rows) is not None:
        return None

    pub_stmt = select(Organization).where(
        Organization.is_public.is_(True), Organization.enabled.is_(True)
    )
    return (await db.execute(pub_stmt)).scalar_one_or_none()


async def org_for_claims(
    db: AsyncSession, organization_id: uuid.UUID | None
) -> Organization | None:
    """Load the user's org for token claims, enforcing the real-org kill-switch.

    Raises ``OrgDisabled`` when the org is a disabled *real* org, so no
    token-minting path (issue/refresh) can keep a kill-switched tenant's sessions
    alive. The public org's ``enabled`` flag only gates new sign-ins (handled in
    ``resolve_organization``), not existing sessions, so it does not block here.
    """
    if organization_id is None:
        return None
    org = await db.get(Organization, organization_id)
    if org is not None and not org.enabled and not org.is_public:
        raise OrgDisabled("User's organization is disabled")
    return org


def org_claims(org: Organization | None) -> dict:
    """Build the oid/oslug/opub JWT claim kwargs from a resolved org (or None)."""
    return {
        "org_id": str(org.id) if org else None,
        "org_slug": org.slug if org else None,
        "org_is_public": org.is_public if org else False,
    }


def is_disabled_real_org(org: Organization | None) -> bool:
    """The kill-switch condition: a disabled *real* (non-public) org.

    Such an org's users must be blocked from new memberships and from minting
    tokens. The public org's ``enabled`` flag is a sign-in switch (enforced in
    ``resolve_organization``), not a live-session kill-switch, so a disabled
    public org is NOT 'disabled' for this purpose.
    """
    return org is not None and not org.enabled and not org.is_public


async def effective_org(db: AsyncSession, user: User) -> Organization | None:
    """The user's effective org for access decisions.

    The stored ``organization_id`` if present, else — for a pre-provisioned (never
    signed in) or SET-NULL-after-org-deletion account — the org resolved from their
    verified email domain. The single source of truth so the membership-creation
    gate and the login/authz workspace pickers agree on which org a user counts as.
    """
    if user.organization_id is not None:
        return await db.get(Organization, user.organization_id)
    return await resolve_organization(db, user.email)


async def workspace_allows_org(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    organization_id: uuid.UUID | None,
) -> bool:
    """True if the workspace permits members from this org.

    No allowed-org rows => open to all orgs (legacy behavior). A restricted
    workspace denies users whose org is None or not in the allowed set.
    """
    stmt = select(WorkspaceAllowedOrganization.organization_id).where(
        WorkspaceAllowedOrganization.workspace_id == workspace_id
    )
    allowed = set((await db.execute(stmt)).scalars().all())
    if not allowed:
        return True
    return organization_id in allowed


async def filter_workspaces_allowing_org(
    db: AsyncSession,
    workspace_ids: list[uuid.UUID],
    organization_id: uuid.UUID | None,
) -> set[uuid.UUID]:
    """Of ``workspace_ids``, the subset that permits this org.

    Computed in a single pair of queries instead of one ``workspace_allows_org``
    call per workspace — used by the login picker and the authz discovery list,
    which iterate a user's workspaces. A workspace with no allow-list rows is open
    to all orgs.
    """
    if not workspace_ids:
        return set()
    # Workspaces that have ANY allow-list rows (i.e. are restricted).
    restricted = set(
        (
            await db.execute(
                select(WorkspaceAllowedOrganization.workspace_id)
                .where(WorkspaceAllowedOrganization.workspace_id.in_(workspace_ids))
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    # Restricted workspaces that explicitly allow this org.
    explicitly_allowed: set[uuid.UUID] = set()
    if organization_id is not None:
        explicitly_allowed = set(
            (
                await db.execute(
                    select(WorkspaceAllowedOrganization.workspace_id).where(
                        WorkspaceAllowedOrganization.workspace_id.in_(workspace_ids),
                        WorkspaceAllowedOrganization.organization_id == organization_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    return {
        wid
        for wid in workspace_ids
        if wid not in restricted or wid in explicitly_allowed
    }


async def assert_user_allowed_in_workspace(
    db: AsyncSession, user: User, workspace_id: uuid.UUID
) -> None:
    """Raise ValueError if ``user`` may not be a member of this workspace.

    The single chokepoint for the allowed-orgs gate on every membership-creation
    path (invite, admin add-user, CSV import). Resolves the user's *effective* org
    (the stored ``organization_id``, or the org of their email domain for a
    pre-provisioned NULL-org account) and applies the same two checks token
    issuance does, so a membership is never created that the user could not redeem:

    - the disabled-real-org kill-switch (mirrors ``org_for_claims`` at mint time); and
    - the workspace's allowed-orgs list.
    """
    org = await effective_org(db, user)
    if is_disabled_real_org(org):
        raise ValueError("User's organization is disabled")
    org_id = org.id if org is not None else None
    if not await workspace_allows_org(db, workspace_id, org_id):
        raise ValueError("User's organization is not permitted in this workspace")


async def workspaces_allowing_org(
    db: AsyncSession, organization_id: uuid.UUID
) -> list[uuid.UUID]:
    """Workspace ids whose allow-list references this org.

    Deleting an org wired into any workspace's allow-list is refused: the FK is
    ``ON DELETE RESTRICT`` (so the DB rejects it as a race backstop), and the
    service uses this to fail fast with a clear message. Removing the org's rows
    via cascade could empty a list and silently flip a locked-down workspace open
    ('open to all'), so the admin must adjust those workspaces first.
    """
    stmt = (
        select(WorkspaceAllowedOrganization.workspace_id)
        .where(WorkspaceAllowedOrganization.organization_id == organization_id)
        .distinct()
    )
    return list((await db.execute(stmt)).scalars().all())


async def list_orgs_for_directory(
    db: AsyncSession, include_disabled: bool = False
) -> list[Organization]:
    """Lean org list for the internal directory endpoint (no counts)."""
    stmt = select(Organization).order_by(
        Organization.is_public.desc(), Organization.name
    )
    if not include_disabled:
        stmt = stmt.where(Organization.enabled.is_(True))
    return list((await db.execute(stmt)).scalars())
