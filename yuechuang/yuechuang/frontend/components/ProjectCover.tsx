'use client';
import Image from 'next/image';
import {useEffect, useState} from 'react';
import {Clapperboard, Play} from 'lucide-react';
import {fetchProjectDetail} from '@/lib/workflowApi';
import {projectCover} from '@/lib/projectPresentation';
import {assetUrl} from './stages/utils';

/** Existing detail API is the only source of selected images; keep cover reads bounded. */
export function useProjectCovers(ids: string[]) {
  const [covers,setCovers]=useState<Record<string,string|undefined>>({});
  const key=JSON.stringify(ids);
  useEffect(()=>{
    let cancelled=false,index=0;
    const projectIds=JSON.parse(key) as string[];
    async function worker() {
      while (!cancelled&&index<projectIds.length) {
        const id=projectIds[index++];
        try {
          const detail=await fetchProjectDetail(id);
          if(!cancelled)setCovers(previous=>({...previous,[id]:projectCover(detail.assets)}));
        } catch { /* A missing cover never prevents opening the project. */ }
      }
    }
    void Promise.all(Array.from({length:Math.min(3,projectIds.length)},()=>worker()));
    return()=>{cancelled=true;};
  },[key]);
  return covers;
}

export default function ProjectCover({src,title,ratio='16:9',style='realistic',episode}:{src?:string;title:string;ratio?:string;style?:string;episode?:number}) {
  const [failed,setFailed]=useState<string>();
  return <div className="yc-project-cover" data-style={style} data-portrait={ratio==='9:16'||ratio==='3:4'}>
    {src&&failed!==src?<Image src={assetUrl(src)} alt={`${title} · 已选素材预览`} fill sizes="(max-width: 600px) 100vw, (max-width: 1000px) 50vw, 33vw" unoptimized onError={()=>setFailed(src)}/>:<div className="yc-project-cover-placeholder"><Clapperboard size={28} strokeWidth={1.2}/><span className="yc-cover-monogram" aria-hidden="true">{Array.from(title)[0]||'创'}</span><small>{src?'封面暂不可用':'暂无封面 · 制作后展示'}</small></div>}
    <span className="yc-cover-ratio">{ratio}</span>
    {episode!==undefined&&<span className="yc-cover-episode">第 {episode} 集</span>}
    {src&&failed!==src&&<span className="yc-cover-source">已选素材</span>}
    <span className="yc-cover-open" aria-hidden="true"><Play size={18}/></span>
  </div>;
}
