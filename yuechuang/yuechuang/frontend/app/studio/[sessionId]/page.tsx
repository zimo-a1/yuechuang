'use client';
import {Suspense} from 'react';
import {useParams} from 'next/navigation';
import WorkflowPanel from '@/components/WorkflowPanel';
export default function StudioPage(){const params=useParams<{sessionId:string}>();return <Suspense fallback={<p>正在加载制作工作台…</p>}><WorkflowPanel key={params.sessionId} sessionOverride={params.sessionId}/></Suspense>;}
