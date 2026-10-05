'use client';

import { Suspense } from 'react';
import {useSearchParams} from 'next/navigation';
import WorkflowPanel from '@/components/WorkflowPanel';

function Loading() {
  return (
    <div className="flex items-center justify-center h-screen bg-gray-50">
      <div className="text-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto mb-4"></div>
        <p className="text-gray-500">加载中...</p>
      </div>
    </div>
  );
}

function Workspace() {
  const params = useSearchParams();
  return <WorkflowPanel key={params.get('session') || 'home'} />;
}

export default function Home() {
  return (
    <Suspense fallback={<Loading />}>
      <Workspace />
    </Suspense>
  );
}

