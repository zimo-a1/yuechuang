'use client';
import {useParams} from 'next/navigation';
import ProjectOverview from '@/components/ProjectOverview';
export default function ProjectPage(){const params=useParams<{projectId:string}>();return <ProjectOverview key={params.projectId} id={params.projectId}/>;}
