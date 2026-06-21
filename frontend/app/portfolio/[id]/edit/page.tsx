"use client";
import { use } from "react";
import { useRouter } from "next/navigation";
import { usePortfolio } from "@/lib/queries";
import { PortfolioForm } from "@/components/form/portfolio-form";
import { GlassPanel } from "@/components/ui/glass";

export default function EditPortfolio({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const { data: portfolio } = usePortfolio(id);
  if (!portfolio) return <GlassPanel><span style={{ color: "var(--color-muted)" }}>Loading…</span></GlassPanel>;
  return <PortfolioForm initial={portfolio} onSaved={(pid) => router.push(`/portfolio/${pid}`)} />;
}
