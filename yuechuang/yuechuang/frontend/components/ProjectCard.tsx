'use client';
import Link from 'next/link';
import type {ReactNode} from 'react';
import {STYLES} from '@/config/models';
import {SOURCE_NAMES} from '@/lib/projects';
import {projectPresentation,relativeProjectTime} from '@/lib/projectPresentation';
import type {SessionSummary} from '@/lib/workflowTypes';
import ProjectCover from './ProjectCover';
import StatusTag from './ui/StatusTag';

export default function ProjectCard({project,episodes=[],cover,actions,compact=false}:{project:SessionSummary;episodes?:SessionSummary[];cover?:string;actions?:ReactNode;compact?:boolean}) {
  const name=project.title||project.idea||'未命名项目';
  const presentation=projectPresentation(project,episodes);
  const count=project.meta?.project_container?episodes.length:(project.meta?.episodes||1)+episodes.length;
  const style=project.meta?.style||project.style;
  const styleLabel=STYLES.find(item=>item.id===style)?.label.split(' / ').at(-1)||style||'默认风格';
  const href=`/projects/${encodeURIComponent(project.id)}`;
  return <article className={`yc-project-card${compact?' is-compact':''}`}>
    <Link className="yc-project-cover-link" href={href} aria-label={`打开项目 ${name}`}><ProjectCover src={cover} title={name} ratio={project.meta?.video_ratio} style={style}/></Link>
    {actions}
    <Link className="yc-project-card-body" href={href}>
      <div className="yc-project-card-title"><h2 title={name}>{name}</h2><StatusTag {...presentation}/></div>
      <div className="yc-project-card-meta"><span title={`${styleLabel} · ${count} 集 · ${SOURCE_NAMES[project.source_type||'legacy']}`}>{styleLabel} · {count} 集 · {SOURCE_NAMES[project.source_type||'legacy']}</span><time title={project.date?new Date(project.date*1000).toLocaleString('zh-CN'):undefined} dateTime={project.date?new Date(project.date*1000).toISOString():undefined}>{relativeProjectTime(project.date)}</time></div>
      {!compact&&<div className="yc-project-card-progress"><span>{presentation.description}</span><span aria-hidden="true">→</span></div>}
    </Link>
  </article>;
}
