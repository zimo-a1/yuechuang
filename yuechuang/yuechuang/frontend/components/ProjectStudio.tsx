'use client';
import IPSourceNotice from './IPSourceNotice';
import Link from 'next/link';
import {useEffect,useRef,useState,type ReactNode} from 'react';
import {ArrowLeft,BookOpen,Check,ListTodo,RefreshCw,X,Loader2} from 'lucide-react';
import {fetchProjectTasks,retryProjectTask,stopProject} from '@/lib/workflowApi';
import {errorMessage,type ProjectMeta} from '@/lib/workflowTypes';
import {STUDIO_STEPS,stepComplete,type StudioStep} from '@/lib/studio';
import {useStudioConfirm} from './StudioConfirm';
import {canLeaveEditor} from '@/lib/unsavedChanges';
import {stepForStage} from '@/lib/studio';
import {STAGE_NAMES} from '@/lib/projects';
import StatusTag from '@/components/ui/StatusTag';
import {toast} from '@/lib/uiFeedback';
function Tasks({id,onClose,onStep,onRefresh}:{id:string;onClose:()=>void;onStep:(step:StudioStep)=>void;onRefresh:()=>void}){
  const dialog=useRef<HTMLDialogElement>(null);
  useEffect(()=>{dialog.current?.showModal();},[]);
  const [tasks,setTasks]=useState<Awaited<ReturnType<typeof fetchProjectTasks>>>([]);const [error,setError]=useState('');const [loaded,setLoaded]=useState(false);const [filter,setFilter]=useState('all');const [working,setWorking]=useState(false);const lock=useRef(false);const {confirm,confirmation}=useStudioConfirm();
  useEffect(()=>{let active=true;const load=async()=>{try{const value=await fetchProjectTasks(id);if(active){setTasks(value);setError('');}}catch(e){if(active)setError(errorMessage(e,'任务读取失败'));}finally{if(active)setLoaded(true);}};void load();const timer=setInterval(load,2500);return()=>{active=false;clearInterval(timer);};},[id]);
  async function action(taskId?:string,stage?:string){if(lock.current)return;if(taskId&&!await confirm(stage==='post_production'?'重新执行本地视频拼接，不调用模型。继续？':'按原任务的范围重试，模型请求会再次计费。已有产物保留，不会自动重试其他任务。继续？'))return;lock.current=true;setWorking(true);try{if(taskId)await retryProjectTask(id,taskId);else await stopProject(id);setTasks(await fetchProjectTasks(id));onRefresh();toast.success(taskId?'已提交重试任务':'已请求停止任务');}catch(e){const message=errorMessage(e,'操作失败');setError(message);toast.error(message);}finally{lock.current=false;setWorking(false);}}
  const active=tasks.some(t=>['queued','running','stopping'].includes(t.status));
  const shown=tasks.filter(t=>filter==='all'||(filter==='active'?['queued','running','stopping'].includes(t.status):filter==='failed'?['error','stopped','interrupted'].includes(t.status):t.status==='completed'));
  return <dialog ref={dialog} className="yc-task-native" aria-label="本集任务" onCancel={onClose}><section className="yc-task-drawer"><header><h2>本集任务</h2><button onClick={onClose} aria-label="关闭任务"><X size={20}/></button></header><nav className="yc-task-filters">{[['all','全部'],['active','进行中'],['failed','失败 / 停止'],['done','已完成']].map(([key,label])=><button key={key} aria-pressed={filter===key} onClick={()=>setFilter(key)}>{label}</button>)}</nav>{error&&<p role="alert" className="yc-project-error">{error}</p>}{active&&<button className="yc-secondary-button" disabled={working} onClick={()=>void action()}>停止当前任务</button>}{!loaded?<p>正在读取任务…</p>:!shown.length?<div className="yc-project-empty"><ListTodo size={32}/><p>当前分类没有任务</p></div>:shown.map(t=>{const event=t.events?.at(-1);return <article key={t.task_id}><div className="yc-section-row"><b>{STAGE_NAMES[t.stage]||t.stage}</b><StatusTag tone={t.status==='completed'?'review':['error','interrupted'].includes(t.status)?'error':t.status==='stopped'?'stopped':['running','stopping'].includes(t.status)?'running':'todo'} label={({queued:'排队中',running:'执行中',stopping:'正在停止',completed:'已返回结果',error:'失败',stopped:'已停止',interrupted:'重启中断'} as Record<string,string>)[t.status]||'状态待更新'}/></div>{['running','queued'].includes(t.status)&&<progress max={100} value={event?.percent||0}/>}<p>{event?.step_desc||event?.message}</p>{t.error&&<p className="yc-project-error">{t.error.message}</p>}<small>{new Date(t.updated_at*1000).toLocaleString('zh-CN')}</small><div className="yc-task-actions"><button className="yc-secondary-button" onClick={()=>{if(canLeaveEditor()){onClose();onStep(stepForStage(t.stage));}}}>查看对应步骤</button>{['error','stopped','interrupted'].includes(t.status)&&<button className="yc-primary-button" disabled={working||active} onClick={()=>void action(t.task_id,t.stage)}>重试此任务</button>}</div></article>;})}</section>{confirmation}</dialog>;
}
function nextHintFor(step:StudioStep,confirmed:string[]){
  if(step==='source')return '保存原始内容，进入 AI 改写';
  if(step==='rewrite')return '保存并确认剧本，进入资产制作';
  if(step==='assets')return '补齐图片并确认资产，进入视频制作';
  if(step==='videos')return !confirmed.includes('character_design')?'先回到资产制作，选择并确认图片':!confirmed.includes('storyboard')?'编辑并保存分镜，检查后点「确认分镜」':!confirmed.includes('reference_generation')?'补齐每段参考图，检查后点「确认参考图」':!confirmed.includes('video_generation')?'生成视频并逐段检查，满意后点「确认视频」':'视频已全部确认，点「拼接导出」继续';
  return confirmed.includes('post_production')?'预览并下载成片':'拼接视频并检查成片';
}
function SourceChip({meta}:{meta:ProjectMeta}){
  const ref=useRef<HTMLDetailsElement>(null);
  useEffect(()=>{const close=(event:PointerEvent)=>{const node=ref.current;if(node?.open&&!node.contains(event.target as Node))node.open=false;};document.addEventListener('pointerdown',close);return()=>document.removeEventListener('pointerdown',close);},[]);
  if(!meta.ip_binding)return null;
  return <details ref={ref} className="yc-studio-source"><summary title="查看改编来源与原文"><BookOpen size={13}/><span>改编自《{meta.ip_binding.title}》</span><small>版本 {meta.ip_binding.version_number}</small></summary><div className="yc-studio-source-panel"><IPSourceNotice binding={meta.ip_binding} projectId={meta.parent_project_id}/></div></details>;
}
export default function ProjectStudio({id,meta,step,confirmed,busy,onStep,onRefresh,controls,children}:{id:string;meta:ProjectMeta;step:StudioStep;stage:string;confirmed:string[];busy:boolean;onStep:(step:StudioStep)=>void;onStage:(stage:string)=>void;onRefresh:()=>void;controls:ReactNode;children:ReactNode}){
  const [tasksOpen,setTasksOpen]=useState(false);
  const stepsRef=useRef<HTMLElement>(null);
  useEffect(()=>{const nav=stepsRef.current;const current=nav?.querySelector<HTMLElement>('[aria-current=step]');if(nav&&current&&nav.scrollWidth>nav.clientWidth)nav.scrollTo({left:Math.max(0,current.offsetLeft-nav.offsetLeft-(nav.clientWidth-current.offsetWidth)/2)});},[step]);
  const activeIndex=STUDIO_STEPS.findIndex(item=>item.id===step);
  const title=meta.project_title||meta.title||'历史项目';
  const projectHref=`/projects/${meta.parent_project_id||id}`;
  return <div className="yc-project-studio hb-studio experience-studio yc-studio-compact">
    <header className="yc-studio-bar">
      <div className="yc-studio-bar-identity"><Link className="yc-studio-back-link" href={projectHref} aria-label={`返回项目 ${title}`} title="返回项目" onClick={event=>{if(!canLeaveEditor())event.preventDefault();}}><ArrowLeft size={16}/></Link><div><h1 title={title}>{title}</h1><span>第 {meta.episode_number||1} 集</span></div></div>
      <nav ref={stepsRef} className="yc-studio-steps" aria-label="项目制作流程">{STUDIO_STEPS.map((item,index)=>{const done=stepComplete(item.id,meta,confirmed),current=step===item.id;return <button key={item.id} aria-current={current?'step':undefined} data-done={done} onClick={()=>onStep(item.id)} title={`${index+1}. ${item.label} · ${done?'已完成':current?'当前步骤':'待处理'}`}><span className="yc-studio-step-mark">{done?<Check size={12}/>:index+1}</span><span>{item.label}</span></button>;})}</nav>
      <div className="yc-studio-bar-actions">{controls}<button className="yc-studio-icon-button" onClick={onRefresh} aria-label="刷新本集" title="刷新本集"><RefreshCw size={14}/></button><button className="yc-studio-task-button" onClick={()=>setTasksOpen(true)} title="查看本集任务"><ListTodo size={14}/><span>任务</span>{busy&&<Loader2 size={12} className="animate-spin"/>}</button></div>
    </header>
    <div className="yc-studio-guide"><span className="yc-studio-guide-step">第 {activeIndex+1}/5 步 · {STUDIO_STEPS[activeIndex]?.label}</span><p><b>下一步</b>{nextHintFor(step,confirmed)}</p><SourceChip meta={meta}/><span className="yc-studio-guide-count" title="六个制作环节需要逐一确认">已确认 {confirmed.length}/6</span></div>
    <div className="yc-studio-body"><main className="yc-studio-content">{children}</main></div>
    {tasksOpen&&<Tasks id={id} onClose={()=>setTasksOpen(false)} onStep={onStep} onRefresh={onRefresh}/>}
  </div>;
}
