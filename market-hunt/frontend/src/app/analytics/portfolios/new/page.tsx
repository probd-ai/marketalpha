'use client';

import React from 'react';
import Link from 'next/link';
import { ArrowLeft, Layers } from 'lucide-react';
import PortfolioForm from '@/components/portfolio/PortfolioForm';

export default function NewPortfolioPage() {
  return (
    <div className="min-h-screen p-6 max-w-5xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/analytics/portfolios" className="text-gray-500 hover:text-gray-800">
          <ArrowLeft className="w-5 h-5" />
        </Link>
        <Layers className="w-6 h-6 text-indigo-600" />
        <div>
          <h1 className="text-2xl font-bold text-gray-900">New portfolio</h1>
          <p className="text-sm text-gray-500">
            Prefilled with the validated Alpha two-tranche defaults (mid + first, 50/50) — edit anything.
          </p>
        </div>
      </div>
      <PortfolioForm />
    </div>
  );
}
