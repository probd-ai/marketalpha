"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type { Portfolio } from "./types";

export const usePortfolios = () => useQuery({ queryKey: ["portfolios"], queryFn: api.listPortfolios });
export const usePortfolio = (id: string) =>
  useQuery({ queryKey: ["portfolio", id], queryFn: () => api.getPortfolio(id), enabled: !!id });
export const useRunPortfolio = (id: string, endDate?: string, startDate?: string) =>
  useQuery({ queryKey: ["run", id, endDate ?? "today", startDate ?? "default"], queryFn: () => api.runPortfolio(id, endDate, startDate), enabled: !!id });
export const useStrategies = () => useQuery({ queryKey: ["strategies"], queryFn: api.listStrategies });
export const useUniverses = () => useQuery({ queryKey: ["universes"], queryFn: api.listUniverses });

export function useCreatePortfolio() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (b: Portfolio) => api.createPortfolio(b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolios"] }) });
}
export function useUpdatePortfolio(id: string) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (b: Portfolio) => api.updatePortfolio(id, b),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["portfolios"] }); qc.invalidateQueries({ queryKey: ["portfolio", id] }); } });
}
export function useDeletePortfolio() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: string) => api.deletePortfolio(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["portfolios"] }) });
}
export function useCreateStrategy() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: api.createStrategy,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["strategies"] }) });
}
