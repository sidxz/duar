"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthz } from "@duar-auth/nextjs";

export default function HomePage() {
  const { isAuthenticated, isLoading } = useAuthz();
  const router = useRouter();

  useEffect(() => {
    if (isLoading) return;
    router.replace(isAuthenticated ? "/notes" : "/login");
  }, [isAuthenticated, isLoading, router]);

  return (
    <div className="flex h-screen items-center justify-center bg-zinc-950">
      <div className="h-6 w-6 animate-spin rounded-full border-2 border-zinc-600 border-t-zinc-300" />
    </div>
  );
}
