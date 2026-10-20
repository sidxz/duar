import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** An activity-detail value for display: objects as JSON, capped at 60 chars. */
export function detailValue(v: unknown): string {
  const s = typeof v === "object" && v !== null ? JSON.stringify(v) : String(v);
  return s.length > 60 ? `${s.slice(0, 60)}…` : s;
}
