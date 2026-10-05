import {Suspense} from 'react';
import IPLibrary from '@/components/IPLibrary';
export default function Page(){return <Suspense fallback={<p>正在加载 IP 库…</p>}><IPLibrary/></Suspense>;}
