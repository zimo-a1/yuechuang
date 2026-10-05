'use client';
import Link from 'next/link';
import {useEffect,useRef,useState} from 'react';
import {useRouter,useSearchParams} from 'next/navigation';
import {BookOpen,Plus,Search,ChevronLeft,ChevronRight,ChevronDown,X,Loader2,ArrowRight} from 'lucide-react';
import {ipRequest,versionRestriction,type IPListData,type IPDetailData} from '@/lib/ipApi';
import {errorMessage} from '@/lib/workflowTypes';
import IPImportDialog from './IPImportDialog';
import IPCover from './IPCover';
import IPProjectDialog from './IPProjectDialog';
import IPAvailability,{IP_CONTENT_STATES} from './IPAvailability';
import {toast} from '@/lib/uiFeedback';

export default function IPLibrary(){
  const router=useRouter();
  const params=useSearchParams();
  const q=params.get('q')||'';
  const status=params.get('status')||'';
  const genre=params.get('genre')||'';
  const page=Math.max(1,parseInt(params.get('page')||'1',10)||1);
  const [result,setResult]=useState<{key:string;data:IPListData}|null>(null);
  const [failure,setFailure]=useState<{key:string;message:string}|null>(null);
  const [creating,setCreating]=useState(false);
  const [adapting,setAdapting]=useState<IPDetailData|null>(null);
  const [opening,setOpening]=useState('');
  const openingLock=useRef(false);
  const active=useRef(true);
  const returnFocus=useRef<HTMLElement|null>(null);
  useEffect(()=>{active.current=true;return()=>{active.current=false;};},[]);
  async function startAdaptation(id:string){
    if(openingLock.current)return;
    openingLock.current=true;setOpening(id);returnFocus.current=document.activeElement as HTMLElement;
    try{const detail=await ipRequest<IPDetailData>(`/${id}`);if(!active.current)return;const version=detail.versions.find(value=>value.id===detail.item.latest_version);const reason=versionRestriction(detail.item,version);if(reason){toast.info(reason+'，请先完善作品资料。');router.push(`/ip/${id}`);return;}setAdapting(detail);}
    catch(error){if(active.current)toast.error(errorMessage(error,'作品读取失败，请重试'));}
    finally{openingLock.current=false;if(active.current)setOpening('');}
  }
  function closeAdaptation(){setAdapting(null);requestAnimationFrame(()=>returnFocus.current?.isConnected&&returnFocus.current.focus());}
  const [retry,setRetry]=useState(0);
  const categories=useRef<HTMLDivElement>(null);
  const [scrollBounds,setScrollBounds]=useState({left:false,right:false});
  const queryString=new URLSearchParams({q,status,genre,page:String(page)}).toString();
  const requestKey=`${queryString}|${retry}`;
  const data=result?.key===requestKey?result.data:null;
  const error=failure?.key===requestKey?failure.message:'';
  const genres=result?.data.genres||[];
  const genreKey=genres.join('|');
  const filtered=!!(q||status||genre);

  useEffect(()=>{
    let active=true;
    ipRequest<IPListData>(`?${queryString}`).then(value=>{
      if(active){setResult({key:requestKey,data:value});setFailure(null);}
    }).catch(e=>{if(active)setFailure({key:requestKey,message:errorMessage(e,'IP 库读取失败')});});
    return()=>{active=false;};
  },[queryString,requestKey]);

  useEffect(()=>{
    const node=categories.current;
    if(!node)return;
    const measure=()=>setScrollBounds({left:node.scrollLeft>1,right:node.scrollLeft+node.clientWidth<node.scrollWidth-1});
    const observer=new ResizeObserver(measure);
    observer.observe(node);
    node.addEventListener('scroll',measure,{passive:true});
    const selected=node.querySelector<HTMLButtonElement>('[aria-pressed="true"]');
    if(selected)node.scrollTo({left:Math.max(0,selected.offsetLeft-(node.clientWidth-selected.offsetWidth)/2)});
    return()=>{observer.disconnect();node.removeEventListener('scroll',measure);};
  },[genre,genreKey]);

  function filter(next:{q?:string;status?:string;genre?:string;page?:number}){
    const values={q,status,genre,page:1,...next};
    const search=new URLSearchParams();
    if(values.q)search.set('q',values.q);
    if(values.genre)search.set('genre',values.genre);
    if(values.status)search.set('status',values.status);
    if(values.page>1)search.set('page',String(values.page));
    router.push(`/ip${search.size?`?${search}`:''}`,{scroll:false});
  }

  function turnPage(next:number){
    filter({page:next});
    document.querySelector('.yc-ip-library')?.scrollIntoView({block:'start'});
  }
  const totalPages=Math.ceil((data?.total||0)/24);
  const pageNumbers=Array.from({length:Math.min(totalPages,5)},(_,i)=>Math.max(1,Math.min(page-2,totalPages-4))+i);

  return <main className="yc-project-center yc-ip-library">
    <header className="yc-ip-catalog-heading">
      <h1>精品 IP 资源</h1>
      <div className="yc-ip-type-navigation">
        <button className="yc-ip-type-scroll" aria-label="向左查看更多小说类型" disabled={!scrollBounds.left} onClick={()=>categories.current?.scrollBy({left:-360,behavior:'smooth'})}><ChevronLeft size={17}/></button>
        <div ref={categories} className="yc-ip-types" role="group" aria-label="小说类型筛选">
          <button aria-pressed={!genre} onClick={()=>filter({genre:''})}>全部</button>
          {genres.map(type=><button key={type} aria-pressed={genre===type} onClick={()=>filter({genre:type})}>{type}</button>)}
          {genre&&!genres.includes(genre)&&<button aria-pressed="true" onClick={()=>filter({genre})}>{genre}</button>}
        </div>
        <button className="yc-ip-type-scroll" aria-label="向右查看更多小说类型" disabled={!scrollBounds.right} onClick={()=>categories.current?.scrollBy({left:360,behavior:'smooth'})}><ChevronRight size={17}/></button>
      </div>
      <button className="yc-ip-add-button" onClick={()=>setCreating(true)}><Plus size={17}/>添加 IP</button>
    </header>

    <div className="yc-ip-toolbar">
      <form role="search" onSubmit={e=>{e.preventDefault();filter({q:String(new FormData(e.currentTarget).get('q')||'').trim()});}}>
        <input aria-label="搜索 IP" name="q" key={q} defaultValue={q} placeholder="搜索 IP 名称 / 作者"/>
        {q&&<button type="button" aria-label="清空搜索" className="yc-ip-clear-search" onClick={()=>filter({q:''})}><X size={16}/></button>}
        <button type="submit" className="yc-ip-search-submit" aria-label="搜索 IP"><Search size={19}/></button>
      </form>
      <label className="yc-ip-status-select" data-active={!!status} title={status in IP_CONTENT_STATES?IP_CONTENT_STATES[status as keyof typeof IP_CONTENT_STATES].description:'按正文和资料准备情况筛选'}>
        <span className="sr-only">内容状态</span>
        <select aria-label="内容状态" value={status} onChange={e=>filter({status:e.target.value})}>
          <option value="">全部状态</option>
          {Object.entries(IP_CONTENT_STATES).map(([key,state])=><option key={key} value={key}>{state.label}</option>)}
        </select><ChevronDown size={16} aria-hidden="true"/>
      </label>
    </div>

    <section className="yc-ip-results" aria-label="作品列表" aria-busy={!data&&!error}>
      <div className="yc-ip-results-heading"><p className="yc-ip-count" role="status">{data?`${data.total} 部作品${genre?` · ${genre}`:''}`:error?'读取失败':'正在读取作品…'}</p>{filtered&&<button className="yc-ip-reset" onClick={()=>filter({q:'',status:'',genre:''})}><X size={14}/>清除筛选</button>}</div>
      {error?<div role="alert" className="yc-project-error">{error}<button onClick={()=>setRetry(v=>v+1)}>重试</button></div>:!data?<div className="yc-ip-grid" aria-hidden="true">{Array.from({length:8},(_,i)=><div className="yc-ip-card-skeleton" key={i}><div/><span/><p/></div>)}</div>:!data.items.length?<section className="yc-project-empty"><BookOpen size={40}/><h2>{filtered?'没有符合条件的作品':'你的故事书架，等待第一部作品'}</h2><p>{filtered?'换个小说类型、搜索词或内容状态试试。':'添加本地小说，或者先记录书名和故事简介。'}</p>{filtered?<button className="yc-secondary-button" onClick={()=>filter({q:'',status:'',genre:''})}>查看全部作品</button>:<button className="yc-primary-button" onClick={()=>setCreating(true)}>添加第一部 IP</button>}</section>:<div className="yc-ip-grid">
        {data.items.map((item,index)=>{const href={pathname:`/ip/${item.id}`,query:params.size?{library:params.toString()}:undefined};const ready=item.availability==='ready';return <article className="yc-ip-card ex-ip-card" key={item.id}>
          <Link className="yc-ip-card-art" href={href} aria-label={`查看《${item.title}》详情`}><IPCover item={item} tone={index} priority={index<4}/><span className="ex-ip-card-state"><IPAvailability item={item}/></span></Link>
          <div className="yc-ip-card-content"><div className="ex-ip-card-meta"><span>{item.genre||'未分类'}</span><span>{item.author||'作者待补充'}</span></div><h2 title={item.title}><Link href={href}>{item.title}</Link></h2><p>{item.synopsis||'还没有故事简介，进入详情后可补充。'}</p><div className="ex-ip-card-actions"><span title={ready?'选择章节和改编方向':item.restriction||IP_CONTENT_STATES[item.availability].description}><button className="yc-primary-button" disabled={!ready||!!opening} onClick={()=>void startAdaptation(item.id)}>{opening===item.id?<Loader2 size={15} className="animate-spin"/>:<ArrowRight size={15}/>}开始改编</button></span><Link className="ex-ip-detail-link" href={href}>查看详情</Link></div>{!ready&&<small className="ex-ip-card-next">{item.availability==='missing'?'进入详情，导入正文后开始改编':'进入详情，完善资料后开始改编'}</small>}</div>
        </article>;})}
      </div>}
      {data&&totalPages>1&&<nav className="yc-ip-pagination" aria-label="IP 分页"><button aria-label="上一页" disabled={page<=1} onClick={()=>turnPage(page-1)}><ChevronLeft size={17}/></button>{pageNumbers.map(number=><button key={number} aria-label={`第 ${number} 页`} aria-current={page===number?'page':undefined} onClick={()=>turnPage(number)}>{number}</button>)}<button aria-label="下一页" disabled={page>=totalPages} onClick={()=>turnPage(page+1)}><ChevronRight size={17}/></button><span>共 {totalPages} 页</span></nav>}
    </section>
    {adapting&&<IPProjectDialog data={adapting} version={adapting.versions.find(value=>value.id===adapting.item.latest_version)!} onClose={closeAdaptation}/>}
    {creating&&<IPImportDialog mode="create" onClose={()=>setCreating(false)} onSaved={value=>router.push(`/ip/${value.item.id}`)}/>}
  </main>;
}
