'use client';
import {useEffect,useRef,useState} from 'react';
import {useRouter} from 'next/navigation';
import {Check,ChevronDown,Loader2,Plus,Search,X} from 'lucide-react';
import {STYLES,VIDEO_RATIOS} from '@/config/models';
import {createProjectDraft} from '@/lib/workflowApi';
import {settingsRequest} from '@/lib/settingsApi';
import {errorMessage} from '@/lib/workflowTypes';
import {toast} from '@/lib/uiFeedback';

export function ProjectSelect({label,value,options,onChange,searchable=false,disabled=false}:{label:string;value:string;options:{id:string;label:string}[];onChange:(value:string)=>void;searchable?:boolean;disabled?:boolean}){
  const [open,setOpen]=useState(false),[query,setQuery]=useState('');
  const root=useRef<HTMLDivElement>(null),trigger=useRef<HTMLButtonElement>(null),search=useRef<HTMLInputElement>(null);
  const shown=options.filter(option=>option.label.toLowerCase().includes(query.toLowerCase()));
  useEffect(()=>{
    if(!open)return;
    const dismiss=(event:PointerEvent)=>{if(!root.current?.contains(event.target as Node)){setOpen(false);setQuery('');}};
    const position=()=>{
      const rect=trigger.current?.getBoundingClientRect(),popup=root.current?.querySelector<HTMLElement>('.hb-select-popup');
      if(!rect||!popup)return;
      const height=Math.min(popup.scrollHeight,240),below=window.innerHeight-rect.bottom-12;
      popup.style.left=`${Math.max(8,Math.min(rect.left,window.innerWidth-rect.width-8))}px`;
      popup.style.width=`${Math.min(rect.width,window.innerWidth-16)}px`;
      popup.style.top=`${below<height&&rect.top>height?rect.top-height-6:rect.bottom+6}px`;
    };
    position();window.addEventListener('resize',position);window.addEventListener('scroll',position,true);
    document.addEventListener('pointerdown',dismiss);if(searchable)search.current?.focus();else root.current?.querySelector<HTMLButtonElement>('[role="option"][aria-selected="true"]')?.focus();
    return()=>{document.removeEventListener('pointerdown',dismiss);window.removeEventListener('resize',position);window.removeEventListener('scroll',position,true);};
  },[open,searchable]);
  function close(){setOpen(false);setQuery('');trigger.current?.focus();}
  return <div className="hb-project-select" ref={root} onKeyDown={event=>{
    if(event.key==='Escape'&&open){event.preventDefault();event.stopPropagation();close();}
    if(event.key==='Enter'&&event.target===search.current){event.preventDefault();if(shown[0]){onChange(shown[0].id);close();}}
    if(['ArrowDown','ArrowUp'].includes(event.key)&&!open){event.preventDefault();setQuery('');setOpen(true);}
    if(['ArrowDown','ArrowUp','Home','End'].includes(event.key)&&open){
      if(event.target===search.current&&['Home','End'].includes(event.key))return;
      event.preventDefault();const items=Array.from(root.current?.querySelectorAll<HTMLButtonElement>('[role="option"]')||[]);const current=items.indexOf(document.activeElement as HTMLButtonElement);
      const next=event.key==='Home'?0:event.key==='End'?items.length-1:event.key==='ArrowDown'?(current+1)%items.length:(current<1?items.length-1:current-1);items[next]?.focus();
    }
  }}><button ref={trigger} type="button" className="hb-select-trigger" aria-label={label} aria-haspopup="listbox" aria-expanded={open} disabled={disabled} onClick={()=>{setQuery('');setOpen(!open);}}>{options.find(option=>option.id===value)?.label||value}<ChevronDown size={14}/></button>
    {open&&<div className="hb-select-popup">{searchable&&<label className="hb-select-search"><Search size={13}/><input ref={search} value={query} onChange={event=>setQuery(event.target.value)} aria-label={`搜索${label}`} placeholder="搜索…"/></label>}<div role="listbox" aria-label={label}>{shown.map(option=><button role="option" aria-selected={option.id===value} type="button" key={option.id} onClick={()=>{onChange(option.id);close();}}><span>{option.label}</span>{option.id===value&&<Check size={14}/>}</button>)}{!shown.length&&<p>没有匹配的选项</p>}</div></div>}
  </div>;
}
export default function ProjectCreateDialog({onClose}:{onClose:()=>void}) {
  const router=useRouter(),dialog=useRef<HTMLDialogElement>(null),nameInput=useRef<HTMLInputElement>(null),lock=useRef(false),attempt=useRef<{payload:string;key:string}|null>(null);
  const [defaultsLoading,setDefaultsLoading]=useState(true),[title,setTitle]=useState(''),[style,setStyle]=useState('realistic'),[ratio,setRatio]=useState('9:16'),[busy,setBusy]=useState(false),[error,setError]=useState('');
  useEffect(()=>{dialog.current?.showModal();},[]);
  useEffect(()=>{let active=true;settingsRequest<{style:string;video_ratio:string}>('/project-defaults').then(values=>{if(active){setStyle(values.style);setRatio(values.video_ratio);}}).catch(e=>{if(active)setError(errorMessage(e,'默认设置读取失败'));}).finally(()=>{if(active)setDefaultsLoading(false);});return()=>{active=false;};},[]);
  useEffect(()=>{if(!defaultsLoading)nameInput.current?.focus();},[defaultsLoading]);
  async function submit(){
    if(lock.current||defaultsLoading||!title.trim())return;
    lock.current=true;setBusy(true);setError('');
    const values={title:title.trim(),style,video_ratio:ratio,project_container:true},payload=JSON.stringify(values);
    if(attempt.current?.payload!==payload)attempt.current={payload,key:crypto.randomUUID()};
    try{const result=await createProjectDraft(values,attempt.current.key);toast.success('项目已创建，尚未调用模型');router.push(`/projects/${result.session_id}`);onClose();}
    catch(e){const message=errorMessage(e,'创建失败');setError(message);toast.error(message);}
    finally{lock.current=false;setBusy(false);}
  }
  return <dialog ref={dialog} className="hb-project-dialog hb-create-project-dialog" onCancel={e=>{if(busy)e.preventDefault();else onClose();}} aria-labelledby="create-project-heading">
    <form onSubmit={e=>{e.preventDefault();void submit();}}><header><div className="hb-dialog-icon"><Plus size={19}/></div><div><h2 id="create-project-heading">新建项目</h2><p>设置项目名称、视觉风格和画面比例</p></div><button className="hb-dialog-close" aria-label="关闭新建项目" type="button" onClick={onClose} disabled={busy}><X size={18}/></button></header>
      <fieldset className="hb-dialog-body" disabled={busy||defaultsLoading}><label><span>项目名称 <span className="hb-required">*</span></span><input ref={nameInput} required maxLength={120} value={title} onChange={e=>setTitle(e.target.value)} placeholder="例如：都市情感短剧《时光邮局》"/></label>
        <div className="hb-dialog-field"><span>视觉风格</span><ProjectSelect label="视觉风格" value={style} options={STYLES} onChange={setStyle} searchable disabled={busy||defaultsLoading}/></div>
        <div className="hb-dialog-field"><span>画面比例</span><ProjectSelect label="画面比例" value={ratio} options={VIDEO_RATIOS} onChange={setRatio} disabled={busy||defaultsLoading}/><small>各集沿用项目画幅，创建项目不会调用模型。</small></div>
      </fieldset>{error&&<p role="alert" className="hb-project-error">{error}</p>}
      <footer><small>仅创建项目 · 不产生模型费用</small><button type="button" className="hb-project-button" onClick={onClose} disabled={busy}>取消</button><button className="hb-project-button is-primary" disabled={busy||defaultsLoading||!title.trim()} title={!title.trim()?'请先填写项目名称':undefined}>{busy?<Loader2 size={14} className="animate-spin"/>:<Plus size={14}/>} {defaultsLoading?'读取默认设置…':busy?'创建中…':'创建项目'}</button></footer>
    </form>
  </dialog>;
}
