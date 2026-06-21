"use client";
import { useRouter } from "next/navigation";
import { PortfolioForm } from "@/components/form/portfolio-form";

export default function NewPortfolio() {
  const router = useRouter();
  return <PortfolioForm onSaved={(id) => router.push(`/portfolio/${id}`)} />;
}
