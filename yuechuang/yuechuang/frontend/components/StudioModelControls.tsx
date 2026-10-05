'use client';
import {useEffect,useRef,useState} from 'react';
import {Settings2,X} from 'lucide-react';
import {fetchModelGroupsByType,fetchVideoModelGroupsByAbility} from '@/lib/modelRegistry';
import {VIDEO_GENERATION_MODES,VIDEO_RESOLUTIONS,type ProviderGroup,type VideoGenerationMode} from '@/config/models';
import type {ModelConfig} from './TopBar';
import {fetchApiModels,type ApiModelOption} from '@/lib/workflowApi';
import {compatibleVideoFormat} from '@/lib/modelSelection';
import {errorMessage} from '@/lib/workflowTypes';
import ModelSelect from '@/components/ui/ModelSelect';
import {modelDisplayName} from '@/lib/modelLabel';
import {toast} from '@/lib/uiFeedback';
import {useStudioConfirm} from './StudioConfirm';
export default function StudioModelControls({config,disabled,onChange}:{config:ModelConfig;disabled:boolean;onChange:(value:ModelConfig)=>Promise<void>}){
  const {confirm,confirmation}=useStudioConfirm();
  const [capabilityModels,setCapabilityModels]=useState<ApiModelOption[]>([]);
  const [groups,setGroups]=useState<Record<string,ProviderGroup[]>>({});const [error,setError]=useState('');const [saving,setSaving]=useState(false);const [advanced,setAdvanced]=useState(false);const lock=useRef(false);const dialog=useRef<HTMLDialogElement>(null);
  useEffect(()=>{if(advanced)dialog.current?.showModal();},[advanced]);
  const mode=config.video_generation_mode||'first_frame';
  useEffect(()=>{let active=true;Promise.all([fetchApiModels({mediaType:'video'}),fetchModelGroupsByType('llm'),fetchModelGroupsByType('t2i'),fetchModelGroupsByType('i2i'),fetchModelGroupsByType('vlm'),fetchVideoModelGroupsByAbility(({first_frame:'first_frame_i2v',start_end_frame:'start_end_frame_i2v',reference:'reference_to_video',text:'text_to_video'} as const)[mode])]).then(([models,text,image,i2i,vlm,video])=>{if(active){setCapabilityModels(models);setGroups({text,image,i2i,vlm,video});}}).catch(e=>{if(active)setError(errorMessage(e,'模型清单读取失败'));});return()=>{active=false;};},[mode]);
  async function update(patch:Partial<ModelConfig>){
    if(lock.current||disabled)return;
    lock.current=true;
    const changed=Object.entries(patch).find(([key,value])=>key.endsWith('_model')&&typeof value==='string'&&value!==config[key as keyof ModelConfig]);
    try{
      if(changed){const before=String(config[changed[0] as keyof ModelConfig]||'');if(!await confirm(`本集当前使用 ${modelDisplayName(before)}。切换为 ${modelDisplayName(String(changed[1]))} 后，后续生成使用新模型，已有素材保留；不会立即发起生成。继续切换？`))return;}
      setSaving(true);setError('');await onChange({...config,...patch});toast.success('生成配置已保存，后续任务使用新配置');
    }catch(e){const message=errorMessage(e,'配置保存失败');setError(message);toast.error(message);}
    finally{lock.current=false;setSaving(false);}
  }
  const field=({first_frame:'video_first_frame_model',start_end_frame:'video_start_end_model',reference:'video_reference_model',text:'video_text_model'} as const)[mode];
  const caps=capabilityModels.find(m=>m.id===(config[field]||config.video_model))?.capabilities;
  const resolutions=Array.isArray(caps?.resolutions)?caps.resolutions as string[]:VIDEO_RESOLUTIONS.map(r=>r.id);
  const ratios=Array.isArray(caps?.ratios)?caps.ratios as string[]:null;
  const videoPatch=(id:string)=>({...compatibleVideoFormat(config,capabilityModels.find(m=>m.id===id)?.capabilities),video_model:id});
  const imageModel=config.image_t2i_model.split('/').at(-1);
  const optionsFor=(category:string)=>{
    const options=(groups[category]||[]).flatMap(group=>group.models.map(model=>({id:model.id,label:model.label})));
    return options;
  };
  return <div className="yc-model-controls"><label className="experience-model-field"><span>文本模型</span><ModelSelect label="文本模型" group="文本" value={config.llm_model} options={optionsFor('text')} disabled={disabled||saving} onChange={v=>void update({llm_model:v})}/></label><label className="experience-model-field"><span>图片模型</span><ModelSelect label="图片模型" group="图片" value={config.image_t2i_model} options={optionsFor('image')} disabled={disabled||saving} onChange={v=>void update({image_t2i_model:v,...(groups.i2i?.some(g=>g.models.some(m=>m.id===v))?{image_it2i_model:v}:{})})}/></label><label className="experience-model-field"><span>视频模型</span><ModelSelect label="视频模型" group="视频" value={config[field]||config.video_model} options={optionsFor('video')} disabled={disabled||saving} onChange={v=>void update({[field]:v,...videoPatch(v)})}/></label><label><span>分辨率</span><select aria-label="视频分辨率" disabled={disabled||saving} value={config.video_resolution} onChange={e=>void update({video_resolution:e.target.value})}>{Array.from(new Set([config.video_resolution,...resolutions])).map(r=><option key={r} value={r} disabled={!resolutions.includes(r)}>{r}{!resolutions.includes(r)?' · 当前模型不支持':''}</option>)}</select></label><button aria-label="更多生成配置" aria-expanded={advanced} onClick={()=>setAdvanced(v=>!v)}><Settings2 size={16}/></button>{saving&&<small role="status">保存中…</small>}{error&&!advanced&&<p role="alert">{error}<button onClick={()=>setError('')} aria-label="关闭配置提示"><X size={12}/></button></p>}{advanced&&<dialog ref={dialog} className="yc-model-advanced" aria-labelledby="studio-model-title" onCancel={()=>setAdvanced(false)}><header><h2 id="studio-model-title">生成配置</h2><button type="button" aria-label="关闭生成配置" onClick={()=>setAdvanced(false)}><X size={16}/></button></header><p>配置即时保存，仅作用于后续生成任务。</p>{error&&<p role="alert" className="hb-model-error">{error}<button type="button" aria-label="关闭配置错误" onClick={()=>setError('')}><X size={12}/></button></p>}<label>视频生成方式<select value={mode} disabled={disabled||saving} onChange={e=>{const next=e.target.value as VideoGenerationMode;const nextField=({first_frame:'video_first_frame_model',start_end_frame:'video_start_end_model',reference:'video_reference_model',text:'video_text_model'} as const)[next];void update({video_generation_mode:next,...videoPatch(config[nextField]||'')});}}>{VIDEO_GENERATION_MODES.map(m=><option key={m.id} value={m.id}>{m.label}</option>)}</select></label><label className="experience-model-field"><span>图生图模型</span><ModelSelect label="图生图模型" group="图片" value={config.image_it2i_model} options={optionsFor('i2i')} disabled={disabled||saving} onChange={v=>void update({image_it2i_model:v})}/></label><label className="experience-model-field"><span>图片评估模型</span><ModelSelect label="图片评估模型" group="文本" value={config.vlm_model} options={optionsFor('vlm')} disabled={disabled||saving} onChange={v=>void update({vlm_model:v})}/></label><label>画面比例<select disabled={disabled||saving} value={config.video_ratio} onChange={e=>void update({video_ratio:e.target.value})}>{Array.from(new Set([config.video_ratio,...(ratios||['16:9','9:16','1:1','4:3','3:4','21:9'])])).map(r=><option key={r} disabled={!!ratios&&!ratios.includes(r)}>{r}</option>)}</select></label><label><input type="checkbox" disabled={disabled||saving} checked={config.enable_concurrency} onChange={e=>void update({enable_concurrency:e.target.checked})}/>并发生成</label>{imageModel==='nano_banana'&&<p>Nano Banana：1K，提示词最多 1000 字符</p>}</dialog>}{confirmation}</div>;
}
