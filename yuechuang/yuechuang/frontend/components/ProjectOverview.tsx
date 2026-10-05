'use client';
import Link from 'next/link';
import Image from 'next/image';
import {useCallback,useEffect,useRef,useState} from 'react';
import {ArrowLeft,ArrowRight,Clapperboard,Images,Loader2,MapPin,Plus,Trash2,Users,X} from 'lucide-react';
import IPSourceNotice from './IPSourceNotice';
import {ProjectSelect} from './ProjectCreateDialog';
import {STYLES,VIDEO_RESOLUTIONS} from '@/config/models';
import {createEpisode,deleteSession,fetchProjectDetail,type ProjectDetail} from '@/lib/workflowApi';
import {errorMessage} from '@/lib/workflowTypes';
import {projectPresentation,projectCover} from '@/lib/projectPresentation';
import ProjectCover from './ProjectCover';
import StatusTag from './ui/StatusTag';
import Breadcrumbs from './ui/Breadcrumbs';
import {toast} from '@/lib/uiFeedback';
import {assetUrl} from './stages/utils';
const ASSET_NAMES={characters:'角色',settings:'场景',props:'道具'};
type ProjectAsset=ProjectDetail['assets'][number];

export default function ProjectOverview({id}:{id:string}) {
  const [data,setData]=useState<ProjectDetail|null>(null),[error,setError]=useState('');
  const [tab,setTab]=useState('episodes'),[kind,setKind]=useState('all');
  const [creating,setCreating]=useState(false),[busy,setBusy]=useState(false),[title,setTitle]=useState(''),[resolution,setResolution]=useState('720P');
  const [deleting,setDeleting]=useState<{id:string;name:string}|null>(null),[preview,setPreview]=useState<ProjectAsset|null>(null);
  const dialog=useRef<HTMLDialogElement>(null),deleteDialog=useRef<HTMLDialogElement>(null),previewDialog=useRef<HTMLDialogElement>(null);
  const attempt=useRef<{payload:string;key:string}|null>(null),lock=useRef(false);
  const returnFocus=useRef<string|null>(null);
  const load=useCallback(async()=>{try{setData(await fetchProjectDetail(id));setError('');}catch(e){setError(errorMessage(e,'项目读取失败'));}},[id]);
  useEffect(()=>{let active=true;fetchProjectDetail(id).then(value=>{if(active){setData(value);setError('');}}).catch(e=>{if(active)setError(errorMessage(e,'项目读取失败'));});return()=>{active=false;};},[id]);
  useEffect(()=>{if(creating)dialog.current?.showModal();else dialog.current?.close();},[creating]);
  useEffect(()=>{if(deleting)deleteDialog.current?.showModal();else deleteDialog.current?.close();},[deleting]);
  useEffect(()=>{if(preview)previewDialog.current?.showModal();else previewDialog.current?.close();},[preview]);
  useEffect(()=>{
    if(busy||creating||deleting||!returnFocus.current)return;
    document.getElementById(returnFocus.current)?.focus();returnFocus.current=null;
  },[busy,creating,deleting]);
  async function add(){
    if(lock.current)return;
    lock.current=true;setBusy(true);setError('');
    const values={title:title.trim(),video_resolution:resolution},payload=JSON.stringify(values);
    if(attempt.current?.payload!==payload)attempt.current={payload,key:crypto.randomUUID()};
    try{await createEpisode(id,values,attempt.current.key);attempt.current=null;returnFocus.current='episode-add';setCreating(false);toast.success('剧集已创建，尚未调用模型');await load();}
    catch(e){const message=errorMessage(e,'添加失败');setError(message);toast.error(message);}finally{lock.current=false;setBusy(false);}
  }
  async function remove(){
    if(!deleting||lock.current)return;
    lock.current=true;setBusy(true);setError('');
    try{await deleteSession(deleting.id);returnFocus.current='project-episodes-tab';setDeleting(null);toast.success('剧集已删除');await load();}
    catch(e){const message=errorMessage(e,'删除失败');setError(message);toast.error(message);}finally{lock.current=false;setBusy(false);}
  }
  function open(){setError('');setTitle('');setResolution(data?.project.meta?.video_resolution||'720P');setCreating(true);}
  if(!data)return <main className="hb-project-page yc-projects-experience"><Link className="hb-project-button" href="/projects"><ArrowLeft size={14}/>返回项目</Link>{error?<div role="alert" className="hb-project-error">{error}<button onClick={()=>void load()}>重试</button></div>:<div className="hb-project-empty" role="status"><Loader2 className="animate-spin"/><p>正在加载项目…</p></div>}</main>;
  const meta=data.project.meta||{},counts={characters:0,settings:0,props:0};data.assets.forEach(asset=>counts[asset.kind]++);
  const style=STYLES.find(item=>item.id===meta.style)?.label.split(' / ').at(-1)||meta.style||'默认风格';
  const nextEpisode=data.episodes.length?Math.max(...data.episodes.map(episode=>episode.meta?.episode_number||1))+1:1;
  return <main className="hb-project-page hb-project-detail yc-projects-experience">
    <Breadcrumbs items={[{label:'首页',href:'/'},{label:'项目',href:'/projects'},{label:meta.title||'历史项目'}]}/>
    <header className="yc-project-detail-heading"><div className="yc-project-detail-cover"><ProjectCover title={meta.title||'历史项目'} src={projectCover(data.assets)} ratio={meta.video_ratio} style={meta.style}/></div><div className="yc-project-detail-info"><div className="yc-project-detail-title"><h1>{meta.title||'历史项目'}</h1><StatusTag {...projectPresentation(data.project,data.episodes)}/></div><div className="yc-project-type"><span>{meta.ip_binding?'IP 改编':'原创项目'}</span><span>{style}</span></div><div className="yc-project-detail-stats"><span><Users size={14}/><b>{counts.characters}</b> 角色</span><span><MapPin size={14}/><b>{counts.settings}</b> 场景</span><span><Clapperboard size={14}/><b>{data.episodes.length}</b> 集</span><span>{meta.video_ratio||'9:16'}</span></div></div>{meta.ip_binding?<Link className="hb-project-button is-primary" href={`/projects/${id}/adaptation`}>进入改编<ArrowRight size={15}/></Link>:<button id="episode-add" className="hb-project-button is-primary" onClick={open} disabled={busy}><Plus size={15}/>添加剧集</button>}</header>
    {meta.ip_binding&&<IPSourceNotice binding={meta.ip_binding} projectId={id}/>}
    <nav className="hb-project-tabs" aria-label="项目内容"><button id="project-episodes-tab" aria-pressed={tab==='episodes'} onClick={()=>setTab('episodes')}><Clapperboard size={15}/>剧集列表<span>{data.episodes.length}</span></button><button aria-pressed={tab==='assets'} onClick={()=>setTab('assets')}><Images size={15}/>素材库<span>{data.assets.length}</span></button></nav>
    {error&&!creating&&!deleting&&<div role="alert" className="hb-project-error">{error}<button onClick={()=>void load()}>重试</button></div>}
    {tab==='episodes'?<div className="yc-episode-grid">{data.episodes.map((episode,index)=>{
      const presentation=projectPresentation(episode),number=episode.meta?.episode_number||index+1,href=`/studio/${episode.id}`;
      const name=(episode.meta?.parent_project_id?episode.title:`第 ${number} 集${(episode.meta?.episodes||1)>1?' · 历史多集工程':''}`)||`第 ${number} 集`;
      return <article key={episode.id} className="yc-episode-card"><Link href={href} aria-label={`进入 ${name} 制作`}><ProjectCover title={name} src={projectCover(data.assets,episode.id)} ratio={episode.meta?.video_ratio||meta.video_ratio} style={episode.meta?.style||meta.style} episode={number}/></Link><div className="yc-episode-body"><div className="yc-project-card-title"><h2 title={name}>{name}</h2><StatusTag {...presentation}/></div><p>{episode.meta?.target_duration||60} 秒 · {episode.meta?.video_resolution||'720P'} · {episode.confirmed_stages?.includes('script_generation')?'剧本已确认':'剧本待确认'}</p></div><footer><span>{presentation.progress.completed}/{presentation.progress.total} 步已确认</span><div>{episode.id!==id&&<button className="yc-episode-delete" aria-label={`删除 ${name}`} title={`删除 ${name}`} disabled={busy} onClick={()=>{setError('');setDeleting({id:episode.id,name});}}><Trash2 size={15}/></button>}<Link className="hb-project-button" href={href}>进入制作<ArrowRight size={14}/></Link></div></footer></article>;
    })}{meta.ip_binding?<Link className="hb-add-episode" href={`/projects/${id}/adaptation`}><span><Plus size={18}/></span>从分集大纲编写一集</Link>:<button className="hb-add-episode" onClick={open} disabled={busy}><span><Plus size={18}/></span>{data.episodes.length?`添加第 ${nextEpisode} 集`:'添加第一集'}</button>}</div>:<section>
      <div className="hb-filter-chips hb-asset-filter" aria-label="素材类型">{[['all','全部'],...Object.entries(ASSET_NAMES)].map(([value,label])=><button key={value} aria-pressed={kind===value} onClick={()=>setKind(value)}>{label} {value==='all'?data.assets.length:counts[value as keyof typeof counts]}</button>)}</div>
      <div className="hb-asset-grid">{data.assets.filter(asset=>kind==='all'||asset.kind===kind).map(asset=><button key={`${asset.session_id}-${asset.kind}-${asset.id}`} className="hb-asset-card" onClick={()=>setPreview(asset)}><div className={`hb-asset-cover ${asset.kind==='settings'?'is-wide':''}`}>{asset.selected?<Image unoptimized width={640} height={640} src={assetUrl(asset.selected)} alt={asset.name}/>:<Images size={28} strokeWidth={1.4}/>}<span className="hb-asset-badge">{asset.selected?'已就绪':'待制作'}</span></div><div className="hb-asset-body"><h3>{asset.name}</h3><p>{asset.description||'待完善描述'}</p></div><footer><span>第 {asset.episode_number} 集 · {ASSET_NAMES[asset.kind]}</span><ArrowRight size={13}/></footer></button>)}</div>
      {!data.assets.some(asset=>kind==='all'||asset.kind===kind)&&<div className="hb-project-empty"><span><Images size={28}/></span><h2>暂无{kind==='all'?'素材':ASSET_NAMES[kind as keyof typeof ASSET_NAMES]}</h2><p>进入剧集的资产制作，提取或添加素材后会汇总到这里。</p></div>}
    </section>}
    <dialog ref={dialog} className="hb-project-dialog hb-episode-dialog" aria-labelledby="episode-create-title" onCancel={event=>{if(busy)event.preventDefault();else setCreating(false);}}><form onSubmit={event=>{event.preventDefault();void add();}}><header><div className="hb-dialog-icon"><Clapperboard size={18}/></div><div><h2 id="episode-create-title">添加第 {nextEpisode} 集</h2><p>为这个项目创建一个新的剧集</p></div><button className="hb-dialog-close" aria-label="关闭添加剧集" type="button" disabled={busy} onClick={()=>setCreating(false)}><X size={18}/></button></header><fieldset className="hb-dialog-body" disabled={busy}><label>剧集名称<input autoFocus maxLength={120} value={title} onChange={event=>setTitle(event.target.value)} placeholder={`留空自动命名为第 ${nextEpisode} 集`}/></label><div className="hb-dialog-field"><span>视频分辨率</span><ProjectSelect label="视频分辨率" value={resolution} options={VIDEO_RESOLUTIONS} onChange={setResolution} disabled={busy}/><small>实际生成前仍校验所选模型是否支持。</small></div></fieldset>{error&&<p role="alert" className="hb-project-error">{error}</p>}<footer><small>创建剧集不会调用模型</small><button type="button" className="hb-project-button" onClick={()=>setCreating(false)} disabled={busy}>取消</button><button className="hb-project-button is-primary" disabled={busy}>{busy?<Loader2 size={14} className="animate-spin"/>:<Plus size={14}/>} {busy?'创建中…':'添加集'}</button></footer></form></dialog>
    <dialog ref={deleteDialog} className="hb-project-dialog hb-delete-dialog" aria-labelledby="episode-delete-title" onCancel={event=>{if(busy)event.preventDefault();else setDeleting(null);}}><form onSubmit={event=>{event.preventDefault();void remove();}}><div className="hb-delete-icon"><Trash2 size={20}/></div><h2 id="episode-delete-title">删除剧集？</h2><p className="hb-delete-message">将删除「{deleting?.name}」及这一集的产物，此操作无法恢复。</p>{error&&<p role="alert" className="hb-project-error">{error}</p>}<footer><button className="hb-project-button" type="button" disabled={busy} onClick={()=>setDeleting(null)}>取消</button><button className="hb-project-button is-danger-solid" disabled={busy}>{busy?<Loader2 size={14} className="animate-spin"/>:<Trash2 size={14}/>} {busy?'删除中…':'确认删除'}</button></footer></form></dialog>
    <dialog ref={previewDialog} className="hb-project-dialog hb-asset-preview" aria-labelledby="asset-preview-title" onCancel={()=>setPreview(null)}>{preview&&<><header><div><h2 id="asset-preview-title">{preview.name}</h2><p>第 {preview.episode_number} 集 · {ASSET_NAMES[preview.kind]}</p></div><button className="hb-dialog-close" aria-label="关闭素材预览" onClick={()=>setPreview(null)}><X size={18}/></button></header><div className="hb-asset-preview-body"><div className="hb-asset-preview-image">{preview.selected?<Image unoptimized width={900} height={900} src={assetUrl(preview.selected)} alt={preview.name}/>:<Images size={40}/>}</div><div><span className="hb-tag">{preview.selected?'图片已就绪':'待制作'}</span><h3>素材描述</h3><p>{preview.description||'暂无描述，进入制作工作台可继续完善。'}</p></div></div><footer><button className="hb-project-button" onClick={()=>setPreview(null)}>关闭</button><Link className="hb-project-button is-primary" href={`/studio/${preview.session_id}?step=assets`}>编辑与制作<ArrowRight size={14}/></Link></footer></>}</dialog>
  </main>;
}
