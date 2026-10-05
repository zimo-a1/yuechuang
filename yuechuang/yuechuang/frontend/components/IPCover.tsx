'use client';
import Image from 'next/image';
import {useState} from 'react';
import {BookOpen} from 'lucide-react';
import type {IPItem} from '@/lib/ipApi';

export default function IPCover({item,tone=0,priority=false}:{item:IPItem;tone?:number;priority?:boolean}){
  const [failed,setFailed]=useState('');
  const path=item.cover_path||item.catalog?.cover_path;
  const showImage=!!path&&failed!==path;
  return <div className="yc-ip-cover" data-tone={tone%4} data-image={showImage}>
    {showImage?<Image src={path} alt={`${item.title}封面`} fill sizes="(max-width:600px) 45vw, 280px" priority={priority} unoptimized onError={()=>setFailed(path)}/>:<><BookOpen size={30}/><small>{item.genre||'故事档案'}</small><h2>{item.title}</h2><span>{item.author||'作者待补充'}</span></>}
  </div>;
}
