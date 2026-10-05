import Link from 'next/link';
import {BookOpen} from 'lucide-react';
import type {IPBinding} from '@/lib/ipApi';
export default function IPSourceNotice({binding,projectId}:{binding:IPBinding;projectId?:string}){return <section className="yc-ip-source-notice"><BookOpen size={21}/><div><b>改编来源：《{binding.title}》</b><p>版本 {binding.version_number} · {binding.chapters[0]?.title} — {binding.chapters.at(-1)?.title} · {binding.direction}</p><small>先分析原著并确认分集大纲，再编写选定一集，进入统一制作流程。</small></div>{projectId&&<Link href={`/projects/${projectId}/adaptation`}>小说改编 →</Link>}<Link href={`/ip/${binding.ip_id}?version=${binding.version_id}&chapter=ch-${binding.first_chapter}`}>查看原文 →</Link></section>;}
