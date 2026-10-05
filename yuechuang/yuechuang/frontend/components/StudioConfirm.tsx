'use client';
import {useEffect,useRef,useState} from 'react';
import {CircleHelp} from 'lucide-react';
function ConfirmDialog({message,onAnswer}:{message:string;onAnswer:(answer:boolean)=>void}){
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(()=>{ref.current?.showModal();},[]);
  return <dialog ref={ref} className="hb-confirm-dialog" aria-labelledby="studio-confirm-title" onCancel={()=>onAnswer(false)} onClick={event=>{if(event.target===event.currentTarget) onAnswer(false);}}><div className="hb-confirm-icon"><CircleHelp size={22}/></div><h2 id="studio-confirm-title">确认操作</h2><p>{message}</p><footer><button autoFocus className="yc-secondary-button" onClick={()=>onAnswer(false)}>取消</button><button className="yc-primary-button" onClick={()=>onAnswer(true)}>确认继续</button></footer></dialog>;
}
export function useStudioConfirm(){
  const [message,setMessage]=useState<string|null>(null);const resolve=useRef<((answer:boolean)=>void)|null>(null);
  useEffect(()=>()=>{resolve.current?.(false);},[]);
  const confirm=(text:string)=>new Promise<boolean>(done=>{resolve.current?.(false);resolve.current=done;setMessage(text);});
  const answer=(value:boolean)=>{resolve.current?.(value);resolve.current=null;setMessage(null);};
  return {confirm,confirmation:message!==null?<ConfirmDialog message={message} onAnswer={answer}/>:null};
}
