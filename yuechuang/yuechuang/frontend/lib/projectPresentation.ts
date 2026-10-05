import {projectProgress, STAGE_NAMES} from './projects.ts';
import type {SessionSummary} from './workflowTypes.ts';

export type StatusTone = 'draft' | 'todo' | 'running' | 'review' | 'done' | 'error' | 'stopped';
export type ProjectFilter = 'all' | 'todo' | 'running' | 'done';
export const PROJECT_FILTERS: {id: ProjectFilter; label: string}[] = [
  {id:'all',label:'全部'}, {id:'todo',label:'待开始'},
  {id:'running',label:'进行中'}, {id:'done',label:'已完成'},
];
type StatusProject = Pick<SessionSummary,'status'|'confirmed_stages'|'current_stage'|'meta'|'novel_task'>;

/** A presentation layer only: preserve failure/review states instead of rewriting backend state. */
export function projectPresentation(project: StatusProject, episodes: StatusProject[] = []) {
  let progress = projectProgress(project);
  if (project.meta?.project_container && episodes.length) {
    const states = episodes.map(projectProgress);
    const priority = ['running','attention','review','progress','draft','completed'];
    const lead = [...states].sort((a,b)=>priority.indexOf(a.state)-priority.indexOf(b.state))[0];
    progress = {...lead, completed:states.reduce((n,item)=>n+item.completed,0), total:states.reduce((n,item)=>n+item.total,0)};
  }
  const involved = project.meta?.project_container && episodes.length ? episodes : [project];
  let tone: StatusTone = 'todo';
  let label = '待开始';
  let filter: Exclude<ProjectFilter,'all'> = 'todo';
  if (progress.state === 'completed') {tone='done'; label='已完成'; filter='done';}
  else if (progress.state === 'attention') {
    tone=involved.some(item=>Object.values(item.status).includes('error'))?'error':'stopped';
    label=tone==='error'?'需要处理':'已停止'; filter='running';
  } else if (progress.state === 'review') {tone='review'; label='待确认'; filter='running';}
  else if (progress.state === 'running' || progress.state === 'progress') {tone='running'; label=progress.state==='running'?'生成中':'制作中'; filter='running';}
  const novelStatus=project.novel_task?.status;
  if (novelStatus && ['queued','running','stopping','error','stopped','interrupted'].includes(novelStatus)) {
    filter='running';
    if (novelStatus==='error') {tone='error';label='改编需要处理';}
    else if (novelStatus==='stopped'||novelStatus==='interrupted') {tone='stopped';label=novelStatus==='stopped'?'改编已停止':'改编已中断';}
    else {tone='running';label=novelStatus==='queued'?'改编排队中':novelStatus==='stopping'?'正在停止':'改编中';}
  }
  const description=filter==='done'?'全部制作步骤已确认；成片质量仍需人工验收':`已确认 ${progress.completed}/${progress.total} 步 · 当前${STAGE_NAMES[progress.stage]||'制作'}`;
  return {filter,tone,label,description,progress};
}

export function relativeProjectTime(timestamp?: number, now = Date.now()): string {
  if (!timestamp || !Number.isFinite(timestamp)) return '历史项目';
  const date = new Date(timestamp*1000),today=new Date(now);
  if (now-date.getTime()<60000 && now>=date.getTime()) return '刚刚';
  if (date.toDateString()===today.toDateString()) return '今天';
  const yesterday=new Date(today);yesterday.setDate(today.getDate()-1);
  if (date.toDateString()===yesterday.toDateString()) return '昨天';
  return `${date.getFullYear()===today.getFullYear()?'':`${date.getFullYear()}-`}${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
}

type CoverAsset = {selected?: string; kind?: string; session_id?: string};
/** Only use selected production assets. Never substitute a style example or another episode's frame. */
export function projectCover(assets: CoverAsset[], episodeId?: string): string | undefined {
  const own=assets.filter(asset=>(!episodeId||asset.session_id===episodeId)&&asset.selected);
  return (own.find(asset=>asset.kind==='settings')||own.find(asset=>asset.kind==='characters')||own[0])?.selected;
}
