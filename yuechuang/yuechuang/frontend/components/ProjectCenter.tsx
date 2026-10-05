'use client';

import Link from 'next/link';
import {useCallback, useEffect, useRef, useState} from 'react';
import {FolderOpen, Grid2X2, List, Loader2, MoreHorizontal, Pencil, Plus, Search, Trash2, X} from 'lucide-react';
import ProjectCreateDialog from './ProjectCreateDialog';
import {deleteSession, fetchSessions, updateProjectDraft} from '@/lib/workflowApi';
import {errorMessage, type SessionSummary} from '@/lib/workflowTypes';
import {SOURCE_NAMES} from '@/lib/projects';
import {PROJECT_FILTERS,projectPresentation,type ProjectFilter} from '@/lib/projectPresentation';
import ProjectCard from './ProjectCard';
import {useProjectCovers} from './ProjectCover';
import {toast} from '@/lib/uiFeedback';

export default function ProjectCenter() {
  const [createOpen, setCreateOpen] = useState(false);
  const [projects, setProjects] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<ProjectFilter>('all');
  const [view,setView]=useState<'grid'|'list'>('grid');
  const [source, setSource] = useState('all');
  const [sort, setSort] = useState('recent');
  const [menu, setMenu] = useState<string|null>(null);
  const [busy, setBusy] = useState(false);
  const [action, setAction] = useState<{kind:'rename'|'delete';project:SessionSummary}|null>(null);
  const [title, setTitle] = useState('');
  const dialog = useRef<HTMLDialogElement>(null);
  const actionLock = useRef(false);
  const returnFocus = useRef<string|null>(null);
  const load = useCallback(async () => {
    setLoading(true); setError('');
    try {setProjects(await fetchSessions());} catch (e) {setError(errorMessage(e,'项目列表加载失败'));}
    finally {setLoading(false);}
  }, []);
  useEffect(() => {
    let active=true;
    fetchSessions().then(items=>{if(active)setProjects(items);})
      .catch(e=>{if(active)setError(errorMessage(e,'项目列表加载失败'));})
      .finally(()=>{if(active)setLoading(false);});
    return()=>{active=false;};
  }, []);
  useEffect(()=>{if(action)dialog.current?.showModal();else dialog.current?.close();},[action]);
  useEffect(()=>{
    if(action||loading||busy||!returnFocus.current)return;
    const target=document.getElementById(returnFocus.current)||document.getElementById('project-create');
    target?.focus();returnFocus.current=null;
  },[action,loading,busy]);
  useEffect(()=>{
    if(!menu)return;
    const outside=(event:PointerEvent)=>{if(!(event.target as Element).closest('.hb-project-menu'))setMenu(null);};
    const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'){setMenu(null);document.getElementById(`project-menu-${menu}`)?.focus();}};
    document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape);
    return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape);};
  },[menu]);
  async function submitAction(){
    if(!action||actionLock.current||(action.kind==='rename'&&!title.trim()))return;
    actionLock.current=true;setBusy(true);setError('');
    try {
      if(action.kind==='delete'){await deleteSession(action.project.id);returnFocus.current='project-create';}
      else await updateProjectDraft(action.project.id,{title:title.trim()},action.project.meta?.project_revision||0);
      toast.success(action.kind==='delete'?'项目已删除':'项目名称已保存');setAction(null);await load();
    } catch(e){const message=errorMessage(e,'操作失败，请重试');setError(message);toast.error(message);}
    finally {actionLock.current=false;setBusy(false);}
  }
  function edit(project:SessionSummary,kind:'rename'|'delete'){
    returnFocus.current=`project-menu-${project.id}`;
    setMenu(null);setError('');setTitle(project.title||'未命名项目');setAction({kind,project});
  }
  const children=(project:SessionSummary)=>projects.filter(ep=>ep.meta?.parent_project_id===project.id);
  const roots=projects.filter(p=>!p.meta?.parent_project_id);
  const visible=roots.filter(project=>{
    const presentation=projectPresentation(project,children(project));
    return (project.title||project.idea||project.id).toLowerCase().includes(query.trim().toLowerCase())
      &&(filter==='all'||presentation.filter===filter)&&(source==='all'||(project.source_type||'legacy')===source);
  }).sort((a,b)=>sort==='name'?(a.title||'').localeCompare(b.title||'','zh-CN'):sort==='oldest'?(a.date||0)-(b.date||0):(b.date||0)-(a.date||0));
  const covers=useProjectCovers(roots.map(project=>project.id));
  const clearFilters=()=>{setQuery('');setFilter('all');setSource('all');};
  return <main className="hb-project-page yc-projects-experience">
    <header className="yc-projects-heading"><div><span className="yc-page-eyebrow">创作工作空间</span><h1>项目</h1><p>让每一个故事，向成片再近一步。</p></div><button id="project-create" className="hb-project-button is-primary" onClick={()=>setCreateOpen(true)} disabled={busy}><Plus size={16}/>新建项目</button></header>
    <div className="yc-projects-toolbar">
      <label className="yc-projects-search"><Search size={16}/><input aria-label="搜索项目" value={query} onChange={e=>setQuery(e.target.value)} placeholder="搜索项目名称"/>{query&&<button type="button" onClick={()=>setQuery('')} aria-label="清空项目搜索"><X size={14}/></button>}</label>
      <div className="yc-project-filters" aria-label="按项目状态筛选">{PROJECT_FILTERS.map(({id: value,label})=><button key={value} type="button" aria-pressed={filter===value} onClick={()=>setFilter(value)}>{label}<span>{roots.filter(project=>value==='all'||projectPresentation(project,children(project)).filter===value).length}</span></button>)}</div>
      <div className="yc-projects-selects"><select aria-label="按来源筛选" value={source} onChange={event=>setSource(event.target.value)}><option value="all">全部来源</option>{Object.entries(SOURCE_NAMES).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select><select aria-label="项目排序" value={sort} onChange={event=>setSort(event.target.value)}><option value="recent">最近创建</option><option value="oldest">最早创建</option><option value="name">项目名称</option></select></div>
      <div className="yc-project-view" aria-label="展示方式"><button aria-label="网格视图" title="网格视图" aria-pressed={view==='grid'} onClick={()=>setView('grid')}><Grid2X2 size={16}/></button><button aria-label="列表视图" title="列表视图" aria-pressed={view==='list'} onClick={()=>setView('list')}><List size={17}/></button></div>
    </div>
    {error&&!action&&<div role="alert" className="hb-project-error">{error}<button onClick={()=>void load()}>重新加载</button></div>}
    {loading?<div className="yc-project-card-grid" aria-label="正在读取项目" aria-busy="true">{Array.from({length:6},(_,i)=><div className="yc-project-skeleton" key={i}><div/><p/><span/></div>)}</div>:visible.length?<><p className="yc-projects-result">{visible.length} 个项目{filter==='running'?' · 包含制作中、待确认和需要处理的项目':''}</p><div className={`yc-project-card-grid${view==='list'?' is-list':''}`}>{visible.map(project=>{
      const name=project.title||project.idea||'未命名项目';
      return <ProjectCard key={project.id} project={project} episodes={children(project)} cover={covers[project.id]} actions={<div className="hb-project-menu"><button id={`project-menu-${project.id}`} className="yc-project-more" aria-label={`更多操作 ${name}`} title="更多操作" aria-expanded={menu===project.id} onClick={()=>setMenu(menu===project.id?null:project.id)}><MoreHorizontal size={18}/></button>{menu===project.id&&<div className="hb-project-menu-panel"><Link href={`/projects/${project.id}`}>打开项目</Link><button onClick={()=>edit(project,'rename')}><Pencil size={14}/>重命名</button><button className="is-danger" onClick={()=>edit(project,'delete')}><Trash2 size={14}/>删除项目</button></div>}</div>}/>;
    })}</div></>:!error&&<div className="yc-project-empty"><span><FolderOpen size={36} strokeWidth={1.2}/></span><h2>{roots.length?'还没有匹配的故事':'给第一个故事一个开始'}</h2><p>{roots.length?'试试其他关键词，或者清空筛选条件。':'先创建项目，保存想法。剧本、素材与视频由你逐步确认。'}</p><div><button className="hb-project-button is-primary" onClick={()=>setCreateOpen(true)}><Plus size={16}/>新建项目</button>{roots.length>0&&<button className="hb-project-button" onClick={clearFilters}>清空筛选</button>}</div></div>}
    {createOpen&&<ProjectCreateDialog onClose={()=>setCreateOpen(false)}/>}
    <dialog ref={dialog} className={`hb-project-dialog ${action?.kind==='delete'?'hb-delete-dialog':''}`} aria-labelledby="project-dialog-title" onCancel={e=>{if(busy)e.preventDefault();else setAction(null);}}>
      <form onSubmit={e=>{e.preventDefault();void submitAction();}}>
        {action?.kind==='delete'?<><div className="hb-delete-icon"><Trash2 size={20}/></div><h2 id="project-dialog-title">删除项目？</h2><p className="hb-delete-message">将删除「{action.project.title||'未命名项目'}」、全部剧集及关联产物，此操作无法恢复。</p></>:<><header><div className="hb-dialog-icon"><Pencil size={18}/></div><div><h2 id="project-dialog-title">重命名项目</h2><p>更新项目名称，不影响已有剧集。</p></div><button className="hb-dialog-close" aria-label="关闭重命名" type="button" disabled={busy} onClick={()=>setAction(null)}><X size={18}/></button></header><div className="hb-dialog-body"><label>项目名称<input autoFocus aria-label="项目新名称" value={title} onChange={e=>setTitle(e.target.value)} required maxLength={120} disabled={busy}/></label></div></>}
        {error&&<p role="alert" className="hb-project-error">{error}</p>}
        <footer><button type="button" className="hb-project-button" onClick={()=>setAction(null)} disabled={busy}>取消</button><button className={`hb-project-button ${action?.kind==='delete'?'is-danger-solid':'is-primary'}`} disabled={busy||(action?.kind==='rename'&&!title.trim())}>{busy&&<Loader2 size={14} className="animate-spin"/>}{busy?'处理中…':action?.kind==='delete'?'确认删除':'保存名称'}</button></footer>
      </form>
    </dialog>
  </main>;
}
