'use client';
import Link from 'next/link';
import {useEffect,useState} from 'react';
import {fetchApiModels,updateProjectDraft,type ApiModelOption} from '@/lib/workflowApi';
import type {NovelDetail} from '@/lib/novelApi';
import {useUnsavedChanges} from '@/lib/unsavedChanges';
import {errorMessage} from '@/lib/workflowTypes';
import ModelSelect from '@/components/ui/ModelSelect';
import {modelDisplayName} from '@/lib/modelLabel';
import {toast} from '@/lib/uiFeedback';
export default function NovelSettings({data,disabled,onSaved}:{data:NovelDetail;disabled:boolean;onSaved:()=>Promise<unknown>}){
  const [model,setModel]=useState(data.model);const [count,setCount]=useState(data.episode_count);const [duration,setDuration]=useState(data.target_duration);const [models,setModels]=useState<ApiModelOption[]>([]);const [saving,setSaving]=useState(false);const [error,setError]=useState('');
  const dirty=model!==data.model||count!==data.episode_count||duration!==data.target_duration;useUnsavedChanges(dirty);
  useEffect(()=>{let active=true;fetchApiModels({modelType:'llm'}).then(items=>{if(active)setModels(items.filter(m=>['deepseek','openai','agnes'].includes(m.provider)));}).catch(()=>{if(active)setError('模型列表加载失败，请刷新或在设置中检查。');});return()=>{active=false;};},[]);
  async function save(){if(saving)return;if(data.variant?.bible&&!window.confirm('改变模型或集数/时长将建立新的分析版本，已生成剧集保留。继续保存吗？'))return;setSaving(true);setError('');try{await updateProjectDraft(data.project_id,{llm_model:model,episodes:count,target_duration:duration},data.project_revision);await onSaved();toast.success('改编设置已保存');}catch(e){const message=errorMessage(e,'保存失败，输入已保留');setError(message);toast.error(message);}finally{setSaving(false);}}
  return <details className="yc-novel-settings"><summary>改编设置 · {modelDisplayName(data.model,models.find(m=>m.id===data.model)?.label)} · {data.episode_count} 集 × {data.target_duration} 秒</summary><form onSubmit={e=>{e.preventDefault();void save();}}><fieldset disabled={disabled||saving}><label>文本模型<ModelSelect label="改编文本模型" group="文本" value={model} options={models.map(item=>({id:item.id,label:modelDisplayName(item.id,item.label)}))} disabled={disabled||saving} onChange={setModel}/></label><label>规划集数<input aria-label="规划集数" type="number" min={1} max={24} required value={count} onChange={e=>setCount(Number(e.target.value))}/></label><label>单集目标秒数<input aria-label="单集目标秒数" type="number" min={15} max={600} required value={duration} onChange={e=>setDuration(Number(e.target.value))}/></label></fieldset><div><Link href="/settings">管理 API 服务 ↗</Link><button className="yc-secondary-button" disabled={disabled||saving||!dirty}>{saving?'保存中…':'保存设置'}</button></div>{error&&<p className="yc-project-error" role="alert">{error}</p>}</form></details>;
}
