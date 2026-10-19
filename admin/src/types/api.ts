export interface User {
  id: string;
  email: string;
  name: string;
  avatar_url: string | null;
  is_active: boolean;
  is_admin: boolean;
  created_at: string;
  workspace_count: number;
}

export interface UserDetail extends Omit<User, "workspace_count"> {
  updated_at: string;
  social_accounts: SocialAccount[];
  memberships: UserMembership[];
}

export interface SocialAccount {
  id: string;
  provider: string;
  provider_user_id: string;
}

export interface UserMembership {
  workspace_id: string;
  workspace_name: string;
  workspace_slug: string;
  role: string;
  joined_at: string;
}

export interface Workspace {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  created_by: string;
  created_at: string;
  member_count: number;
}

export interface WorkspaceDetail extends Workspace {
  group_count: number;
}

export interface WorkspaceMember {
  user_id: string;
  email: string;
  name: string;
  avatar_url: string | null;
  role: string;
  joined_at: string;
}

export interface Group {
  id: string;
  workspace_id: string;
  name: string;
  description: string | null;
  created_by: string;
  created_at: string;
}

export interface GroupMember {
  user_id: string;
  email: string;
  name: string;
  added_at: string;
}

export interface ResourcePermission {
  id: string;
  service_name: string;
  resource_type: string;
  resource_id: string;
  workspace_id: string;
  owner_id: string;
  visibility: string;
  created_at: string;
  shares: ResourceShare[];
}

export interface AdminResourcePermission {
  id: string;
  service_name: string;
  resource_type: string;
  resource_id: string;
  workspace_id: string;
  owner_id: string;
  owner_email: string | null;
  visibility: string;
  created_at: string;
  share_count: number;
  shares: ResourceShare[];
}

export interface ResourceShare {
  id: string;
  grantee_type: string;
  grantee_id: string;
  permission: string;
  granted_by: string;
  granted_at: string;
}

// ── RBAC ────────────────────────────────────────────────────────────

export interface ServiceAction {
  id: string;
  service_name: string;
  action: string;
  description: string | null;
  created_at: string;
}

export interface CustomRole {
  id: string;
  workspace_id: string;
  name: string;
  description: string | null;
  created_by: string | null;
  created_at: string;
  action_count: number;
  member_count: number;
  group_count: number;
}

export interface RoleMember {
  user_id: string;
  email: string;
  name: string;
  assigned_at: string;
  assigned_by: string | null;
}

export interface RoleGroup {
  group_id: string;
  name: string;
  description: string | null;
  member_count: number;
  assigned_at: string;
  assigned_by: string | null;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface TopWorkspace {
  id: string;
  name: string;
  slug: string;
  member_count: number;
}

export interface WorkspaceOption {
  id: string;
  name: string;
  slug: string;
}

export interface Stats {
  total_users: number;
  total_workspaces: number;
  total_groups: number;
  total_resources: number;
  active_users: number;
  inactive_users: number;
  recent_users: User[];
  top_workspaces: TopWorkspace[];
}

export interface ActivityDailyCount {
  day: string; // ISO date, UTC bucket
  action: string;
  count: number;
}

export interface NamedCount {
  name: string;
  count: number;
}

export interface CountryCount extends NamedCount {
  code: string; // ISO 3166-1 alpha-2
}

export interface SignInInsights {
  days: number;
  total: number;
  browsers: NamedCount[];
  os: NamedCount[];
  countries: CountryCount[];
  unresolved: number; // sign-ins from private/unresolvable addresses
}

export interface ActionCount {
  service_name: string;
  action: string;
  count: number;
}

export interface ActionUserCount {
  user_id: string;
  email: string;
  name: string;
  count: number;
}

export interface ActionTrendPoint {
  day: string; // YYYY-MM-DD
  allowed: number;
  denied: number;
}

export interface DormantGrant {
  user_id: string;
  email: string;
  name: string;
  service_name: string;
  action: string;
  role_name: string;
  workspace_id: string;
  workspace_name: string;
}

export interface UnusedRole {
  id: string;
  name: string;
  workspace_id: string;
  workspace_name: string;
  assignees: number;
  no_assignees: boolean;
}

export interface ActionsInsights {
  days: number;
  since: string;
  data_since: string | null;
  top_actions: ActionCount[];
  by_service: { service_name: string; count: number }[];
  top_users: ActionUserCount[];
  trend: ActionTrendPoint[];
  dormant_grants: { total: number; items: DormantGrant[] };
  unused_roles: UnusedRole[];
}

export interface ActivityLog {
  id: string;
  action: string;
  actor_id: string | null;
  actor_name: string | null;
  actor_email: string | null;
  target_type: string;
  target_id: string;
  target_label: string | null;
  workspace_id: string | null;
  detail: Record<string, unknown> | null;
  created_at: string;
}

export interface CsvImportRow {
  email: string;
  name: string;
  workspace_slug: string;
  role: string;
  error: string | null;
}

export interface CsvImportPreview {
  rows: CsvImportRow[];
  valid_count: number;
  error_count: number;
}

export interface CsvImportResult {
  users_created: number;
  memberships_added: number;
  errors: string[];
}

// ── Client Apps ─────────────────────────────────────────────────────

export interface ClientApp {
  id: string;
  name: string;
  redirect_uris: string[];
  is_active: boolean;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

// ── Service Apps ────────────────────────────────────────────────────

export interface ServiceApp {
  id: string;
  name: string;
  service_name: string;
  key_prefix: string;
  is_active: boolean;
  allowed_origins: string[];
  realm_id: string | null;
  last_used_at: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface ServiceAppCreateResponse extends ServiceApp {
  api_key: string;
}

// ── System Health ───────────────────────────────────────────────────

export interface HealthCheckDetail {
  status: string;
  latency_ms: number;
  error: string | null;
}

export interface SystemHealth {
  status: string;
  checks: Record<string, HealthCheckDetail>;
  uptime_seconds: number;
  version: string;
}

// ── Organizations ────────────────────────────────────────────────────

export interface Organization {
  id: string;
  name: string;
  slug: string;
  is_public: boolean;
  enabled: boolean;
  domain_count: number;
  user_count: number;
}

export interface OrgDomain {
  id: string;
  domain: string;
  include_subdomains: boolean;
}

export interface OrganizationDetail {
  id: string;
  name: string;
  slug: string;
  is_public: boolean;
  enabled: boolean;
  user_count: number;
  domains: OrgDomain[];
}

export interface OrgUser {
  id: string;
  email: string;
  name: string;
  avatar_url: string | null;
  is_active: boolean;
}

// ── Settings ────────────────────────────────────────────────────────

export interface OAuthProviderInfo {
  name: string;
  configured: boolean;
}

export interface JwtInfo {
  algorithm: string;
  access_token_expire_minutes: number;
  refresh_token_expire_days: number;
  public_key_preview: string;
  denylist_count: number;
}

export interface SecurityInfo {
  cookie_secure: boolean;
  allowed_hosts: string[];
  cors_origins: string[];
  session_secret_configured: boolean;
  admin_emails: string[];
}

export interface RateLimitInfo {
  endpoint: string;
  limit: string;
}

export interface ServiceKeyInfo {
  name: string;
  preview: string;
}

export interface ServiceInfoType {
  base_url: string;
  frontend_url: string;
  admin_url: string;
}

export interface SelfServeInfo {
  enabled: boolean;
  max_workspaces_per_user: number;
  max_creates_per_hour: number;
}

export interface SystemSettings {
  oauth_providers: OAuthProviderInfo[];
  jwt: JwtInfo;
  security: SecurityInfo;
  rate_limits: RateLimitInfo[];
  service_keys: ServiceKeyInfo[];
  service: ServiceInfoType;
  self_serve: SelfServeInfo;
}

// ── Realms (trusted app groups) ──────────────────────────────────────

export interface Realm {
  id: string;
  slug: string;
  name: string;
  m2m_ttl_s: number;
  is_active: boolean;
  created_at: string;
}

export interface RealmMember {
  // `id` is the service-app id — pass it to add/removeRealmMember.
  id: string;
  name: string;
  service_name: string;
  has_grants: boolean;
}
