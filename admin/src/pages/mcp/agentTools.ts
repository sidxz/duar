import type { ServiceAction, ServiceApp } from "../../types/api";

/**
 * Agent-gated MCP tools are Duar actions named `mcp:<service_name>.<tool>`. The app
 * name is part of the action, so an app's tools are found by prefix in any
 * namespace — including a realm slug when the app is a realm member.
 */
export const agentPrefix = (serviceName: string) => `mcp:${serviceName}.`;

export const agentTools = (actions: ServiceAction[], serviceName: string) =>
  actions.filter((a) => a.action.startsWith(agentPrefix(serviceName)));

export const toolName = (action: string, serviceName: string) =>
  action.slice(agentPrefix(serviceName).length);

/** Apps that serve MCP with at least one agent-gated tool registered. */
export const mcpApps = (apps: ServiceApp[], actions: ServiceAction[]) =>
  apps.filter((app) => agentTools(actions, app.service_name).length > 0);
