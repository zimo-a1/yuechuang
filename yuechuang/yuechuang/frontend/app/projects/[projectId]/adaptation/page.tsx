'use client';
import {useParams} from 'next/navigation';
import NovelStudio from '@/components/NovelStudio';
export default function AdaptationPage(){const params=useParams<{projectId:string}>();return <NovelStudio key={params.projectId} id={params.projectId}/>;}
