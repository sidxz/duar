import { useQuery } from "@tanstack/react-query";
import { getRealms } from "../../api/client";
import type { Realm, ServiceAction, ServiceApp } from "../../types/api";

/**
 * Agent-gated MCP tools are Duar actions named `mcp:<service_name>.<tool>`, registered
 * in the app's own action namespace — its realm's slug when it is a realm member. An
 * app's tools are those in that namespace with that prefix, so another app can't list
 * tools under it by naming an action after it.
 */
export const agentPrefix = (serviceName: string) => `mcp:${serviceName}.`;

/** The namespace an app's actions are registered under. */
export const actionScope = (app: ServiceApp, realms: Realm[]) =>
  realms.find((r) => r.id === app.realm_id)?.slug ?? app.service_name;

export const agentTools = (actions: ServiceAction[], app: ServiceApp, realms: Realm[]) => {
  const scope = actionScope(app, realms);
  const prefix = agentPrefix(app.service_name);
  return actions.filter((a) => a.service_name === scope && a.action.startsWith(prefix));
};

export const toolName = (action: string, serviceName: string) =>
  action.slice(agentPrefix(serviceName).length);

/** Apps that serve MCP with at least one agent-gated tool registered. */
export const mcpApps = (apps: ServiceApp[], actions: ServiceAction[], realms: Realm[]) =>
  apps.filter((app) => agentTools(actions, app, realms).length > 0);

export const useRealms = () => useQuery({ queryKey: ["realms"], queryFn: getRealms });
