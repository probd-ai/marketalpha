import { NextRequest, NextResponse } from 'next/server';

const API_BASE_URL = process.env.MH_API_URL || process.env.MH_API_URL || 'http://127.0.0.1:3001';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ index_name: string }> }
) {
  try {
  const awaitedParams = await params;
    const { index_name } = awaitedParams;
    
    if (!index_name) {
      return NextResponse.json(
        { error: 'Index name is required' },
        { status: 400 }
      );
    }

    const response = await fetch(`${API_BASE_URL}/api/data/index/${encodeURIComponent(index_name)}/industries`);
    
    if (!response.ok) {
      throw new Error(`Backend responded with status: ${response.status}`);
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error('API Error:', error);
    return NextResponse.json(
      { error: `Failed to fetch industries for index: ${error instanceof Error ? error.message : 'Unknown error'}` },
      { status: 500 }
    );
  }
}
