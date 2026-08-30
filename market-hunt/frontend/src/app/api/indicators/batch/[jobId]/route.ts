import { NextRequest, NextResponse } from 'next/server';

const BACKEND_URL = process.env.MH_API_URL || process.env.MH_API_URL || 'http://127.0.0.1:3001';

interface RouteParams {
  params: Promise<{
    jobId: string;
  }>;
}

export async function GET(request: NextRequest, { params }: RouteParams) {
  try {
    const { jobId } = await params;
    const response = await fetch(`${BACKEND_URL}/api/indicators/batch/${jobId}`, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      const errorData = await response.json();
      return NextResponse.json(errorData, { status: response.status });
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error('Error fetching job progress:', error);
    return NextResponse.json(
      { error: 'Failed to fetch job progress' },
      { status: 500 }
    );
  }
}

export async function DELETE(request: NextRequest, { params }: RouteParams) {
  try {
    const { jobId } = await params;
    const response = await fetch(`${BACKEND_URL}/api/indicators/batch/${jobId}`, {
      method: 'DELETE',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      const errorData = await response.json();
      return NextResponse.json(errorData, { status: response.status });
    }

    const data = await response.json();
    return NextResponse.json(data);
  } catch (error) {
    console.error('Error cancelling job:', error);
    return NextResponse.json(
      { error: 'Failed to cancel job' },
      { status: 500 }
    );
  }
}
