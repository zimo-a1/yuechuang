import {Suspense} from 'react';
import IPDetail from '@/components/IPDetail';
export default async function Page({params}:{params:Promise<{ipId:string}>}){const {ipId}=await params;return <Suspense fallback={<p>正在加载作品…</p>}><IPDetail id={ipId}/></Suspense>;}
