'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { ArrowLeft, Layers, Loader2 } from 'lucide-react';
import PortfolioForm from '@/components/portfolio/PortfolioForm';
import { Portfolio, portfolioApi } from '@/lib/portfolioApi';

export default function EditPortfolioPage() {
  const params = useParams<{ id: string }>();
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    portfolioApi
      .get(params.id)
      .then((d) => setPortfolio({ ...d.portfolio, id: params.id }))
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load portfolio'));
  }, [params.id]);

  return (
    <div className="min-h-screen p-6 max-w-5xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <Link href={`/analytics/portfolios/${params.id}`} className="text-gray-500 hover:text-gray-800">
          <ArrowLeft className="w-5 h-5" />
        </Link>
        <Layers className="w-6 h-6 text-indigo-600" />
        <h1 className="text-2xl font-bold text-gray-900">Edit portfolio</h1>
      </div>
      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
      )}
      {!portfolio && !error && (
        <div className="flex items-center justify-center py-24 text-gray-500">
          <Loader2 className="w-6 h-6 animate-spin mr-2" /> Loading…
        </div>
      )}
      {portfolio && <PortfolioForm initial={portfolio} />}
    </div>
  );
}
