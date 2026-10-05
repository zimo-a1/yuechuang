'use client';
import {modelDisplayName} from '@/lib/modelLabel';
import {toast} from '@/lib/uiFeedback';
import StatusTag from '@/components/ui/StatusTag';
import Image from 'next/image';
import {useSearchParams} from 'next/navigation';
import {useRef,useState} from 'react';
import {Check,Download,Film,ImagePlus,Plus,RefreshCw,Save,Upload,X} from 'lucide-react';
import {continueWorkflow,saveSelections,saveStudioSegment,uploadArtifactImage} from '@/lib/workflowApi';
import {errorMessage,type AssetItem,type ProjectMeta,type WorkflowSegment} from '@/lib/workflowTypes';
import {canLeaveEditor,hasUnsavedChanges,useUnsavedChanges} from '@/lib/unsavedChanges';
import {useEditorRecovery} from '@/lib/editorRecovery';
import {boundAssets,segmentDuration,segmentSummary,videoItemStatus} from '@/lib/studioVideo';
import {readStoryboard,storyboardIssues} from '@/lib/storyboard';
import type {StageState} from './stages/types';
import {assetUrl} from './stages/utils';
import VideoPreview from './stages/VideoPreview';
import StudioColumns from './StudioColumns';
import DialogueTimeline from './stages/DialogueTimeline';
import {useStudioConfirm} from './StudioConfirm';

type Props={id:string;meta:ProjectMeta;states:Record<string,StageState>;confirmed:string[];busy:boolean;onRefresh:()=>Promise<void>;onGenerate:(stage:string,mods:Record<string,unknown>)=>Promise<void>;onNext:()=>void;onAssets:()=>void};
const statusNames={done:'已完成',failed:'失败',running:'生成中',pending:'待生成'};
export default function StudioVideoWorkbench(props:Props){
  const {id,meta,states,confirmed,busy,onRefresh,onGenerate,onNext,onAssets}=props;
  const storyboard=states.storyboard.artifact;const segments=(storyboard?.episodes||[]).flatMap(e=>e.segments||[]);
  const params=useSearchParams();const [active,setActive]=useState(params.get('shot')||'');const [filter,setFilter]=useState('all');const [search,setSearch]=useState('');const [selecting,setSelecting]=useState(false);const [selected,setSelected]=useState<string[]>([]);
  const [working,setWorking]=useState(false);const [failure,setFailure]=useState('');const lock=useRef(false);const {confirm,confirmation}=useStudioConfirm();
  const clips=states.video_generation.artifact?.clips||[];const refs=states.reference_generation.artifact?.scenes||[];
  const current=segments.find(s=>s.segment_id===active)||segments[0];const disabled=busy||working;
  const visible=segments.filter(s=>(filter==='all'||videoItemStatus(clips.find(c=>c.id===s.segment_id))===filter)&&`${segmentSummary(s)} ${s.location}`.includes(search));
  async function act(fn:()=>Promise<unknown>){if(lock.current)return;lock.current=true;setWorking(true);setFailure('');try{await fn();await onRefresh();}catch(e){const message=errorMessage(e,'操作失败，请重试');setFailure(message);toast.error(message);}finally{lock.current=false;setWorking(false);}}
  async function generate(stage:string,ids:string[]){if(hasUnsavedChanges()){setFailure('请先保存镜头编辑，再开始生成');return;}const video=stage==='video_generation';const seconds=segments.filter(s=>ids.includes(s.segment_id)).reduce((n,s)=>n+segmentDuration(s),0);const message=stage==='storyboard'?`使用 ${modelDisplayName(meta.llm_model||'文本模型')} 重新拆分剧本，会产生文本调用费用。已有媒体保留，生成后需重新核对分镜及对应关系。`:video?`使用 ${modelDisplayName(meta.video_model||'视频模型')} 生成 ${ids.length} 段，共 ${seconds} 秒。只处理选中的片段，按供应商实际用量计费。`:`生成 ${ids.length} 张参考图，包含文本提示词与图片评估，每张最多 3 次图片生成，会消耗 API 额度。`;
    if(stage!=='storyboard'&&!ids.length)return;if(!await confirm(message))return;
    await act(()=>onGenerate(stage,stage==='storyboard'?{}:{[video?'regenerate_clips':'regenerate_scenes']:ids}));
  }
  const error=failure||states.storyboard.error||states.reference_generation.error||states.video_generation.error;
  const running=['storyboard','reference_generation','video_generation'].map(key=>states[key]).find(state=>state.status==='running');
  const allIds=segments.map(s=>s.segment_id);const batchIds=selecting?selected:allIds;
  return <section className="yc-video-workbench"><header className="yc-video-toolbar"><div><h2>视频制作</h2><span>{segments.length} 段落 · {segments.reduce((n,s)=>n+segmentDuration(s),0)}s · {meta.video_ratio||'16:9'}</span></div><div><button className="yc-secondary-button" disabled={disabled||!confirmed.includes('character_design')} onClick={()=>void generate('storyboard',[])}><RefreshCw size={14}/>{segments.length?'重新拆分':'拆分分镜'}</button><button className="yc-secondary-button" aria-pressed={selecting} onClick={()=>{setSelecting(v=>!v);setSelected([]);}} disabled={!segments.length}>选择</button><button className="yc-secondary-button" disabled={disabled||!confirmed.includes('storyboard')||!batchIds.length} onClick={()=>void generate('reference_generation',batchIds.filter(key=>selecting||!refs.find(r=>r.id===key)?.selected))}>补齐参考图</button><button className="yc-primary-button" disabled={disabled||!confirmed.includes('reference_generation')||!batchIds.length} onClick={()=>void generate('video_generation',batchIds)}>批量视频{selecting?` (${selected.length})`:''}</button></div></header>
    {error&&<div role="alert" className="yc-project-error">{error}<button onClick={()=>void act(onRefresh)}>重新读取状态</button></div>}
    {running&&<div className="yc-workbench-progress" role="status"><progress max={100} value={running.progress}/><span>{running.progressMessage||'任务正在后台执行，可从右上角查看或停止'}</span></div>}
    <div className="yc-video-gates experience-video-gates" role="group" aria-label="制作检查"><span>制作检查</span>{[['character_design','资产',true],['storyboard','分镜',segments.length>0],['reference_generation','参考图',segments.length>0&&segments.every(s=>refs.some(r=>r.id===s.segment_id&&r.selected))],['video_generation','视频',segments.length>0&&segments.every(s=>clips.some(c=>c.id===s.segment_id&&c.selected))]].map(([key,label,ready],index,array)=>{
      const stage=String(key),done=confirmed.includes(stage),previous=index===0||confirmed.includes(String(array[index-1][0]));
      const unavailable=disabled||done||(index>0&&(!ready||!previous));
      const reason=disabled?'当前任务处理中，请等待完成':done?`${label}已确认`:!previous?`请先确认${array[index-1][1]}`:!ready?(stage==='storyboard'?'请先拆分并保存分镜':`请先补齐每段${label}`):`检查后确认${label}`;
      return <span className="experience-gate" key={stage} data-state={done?'done':previous?'current':'pending'} title={reason} tabIndex={unavailable?0:undefined} aria-label={unavailable?reason:undefined}><button disabled={unavailable} aria-label={reason} onClick={()=>{if(index===0)onAssets();else if(hasUnsavedChanges())setFailure('请先保存镜头编辑，再确认');else void act(()=>continueWorkflow(id,stage));}}><i>{done?<Check size={13}/>:index+1}</i><span>{done?`${label}已确认`:`确认${label}`}</span></button></span>;
    })}<small>{!confirmed.includes('character_design')?'先选择并确认资产图片':!confirmed.includes('storyboard')?'编辑分镜并保存，检查后确认分镜':!confirmed.includes('reference_generation')?'补齐每段参考图，检查后确认':!confirmed.includes('video_generation')?'生成视频并逐段检查，满意后确认':'全部视频已确认，可以拼接导出'}</small></div>
    {!segments.length?<div className="yc-project-empty"><Film size={38}/><h3>把剧本拆成可制作的镜头</h3><p>{confirmed.includes('character_design')?'点击右上方“拆分分镜”，生成后在此编辑内容、绑定参考图和制作视频。':'请先到资产制作，完成图片选择并确认。'}</p>{!confirmed.includes('character_design')&&<button className="yc-primary-button" onClick={onAssets}>前往资产制作</button>}</div>:<StudioColumns><aside className="yc-shot-list"><header><h3>视频任务列表</h3><span>{segments.length} 个任务</span></header><input aria-label="搜索镜头" placeholder="搜索镜头内容、场景" value={search} onChange={e=>setSearch(e.target.value)}/><nav aria-label="镜头状态筛选">{[['all','全部'],['pending','待生成'],['running','生成中'],['done','完成'],['failed','失败']].map(([value,label])=><button key={value} aria-pressed={filter===value} onClick={()=>setFilter(value)}>{label} {value==='all'?segments.length:segments.filter(s=>videoItemStatus(clips.find(c=>c.id===s.segment_id))===value).length}</button>)}</nav>{selecting&&<div className="yc-selection-tools"><button onClick={()=>setSelected(visible.map(s=>s.segment_id))}>全选当前列表</button><button onClick={()=>setSelected([])}>清空</button></div>}
      <div className="yc-shot-list-items">{visible.map((seg)=>{const index=segments.indexOf(seg);const clip=clips.find(c=>c.id===seg.segment_id);const ref=refs.find(r=>r.id===seg.segment_id);const status=videoItemStatus(clip);return <div key={seg.segment_id} className="yc-shot-row" data-active={current?.segment_id===seg.segment_id}>{selecting&&<input type="checkbox" aria-label={`选择片段 ${index+1}`} checked={selected.includes(seg.segment_id)} onChange={e=>setSelected(e.target.checked?[...selected,seg.segment_id]:selected.filter(key=>key!==seg.segment_id))}/>}<button aria-label={`打开片段 ${index+1}`} aria-pressed={current?.segment_id===seg.segment_id} onClick={()=>{if(canLeaveEditor()){setActive(seg.segment_id);const url=new URL(window.location.href);url.searchParams.set('shot',seg.segment_id);window.history.replaceState(null,'',url);}}}><div className="yc-shot-thumbnail">{ref?.selected?<Image unoptimized width={96} height={72} src={assetUrl(ref.selected)} alt=""/>:<Film size={20}/>}<span>#{String(index+1).padStart(2,'0')}</span></div><div><p>{segmentSummary(seg)}</p><small data-status={status}>{statusNames[status]} · {segmentDuration(seg)}s</small><small>{seg.location||'未设置场景'}</small></div></button></div>;})}{!visible.length&&<p className="yc-empty-inline">没有符合条件的镜头</p>}</div>{clips.some(c=>videoItemStatus(c)==='failed')&&<button className="yc-secondary-button" disabled={disabled||!confirmed.includes('reference_generation')} onClick={()=>void generate('video_generation',clips.filter(c=>videoItemStatus(c)==='failed').map(c=>c.id))}>重试失败视频</button>}</aside>
      {current&&<SegmentEditor key={current.segment_id} {...props} segment={current} number={segments.indexOf(current)+1} refItem={refs.find(r=>r.id===current.segment_id)} clip={clips.find(c=>c.id===current.segment_id)} disabled={disabled} onAct={act} onGenerate={generate}/>}
    </StudioColumns>}
    <footer className="yc-workbench-footer experience-sticky-actions"><span>编辑 → 保存 → 确认分镜 → 参考图 → 视频</span><button className="yc-primary-button" disabled={disabled||!confirmed.includes('video_generation')} onClick={()=>{if(canLeaveEditor())onNext();}}>进入拼接导出 →</button></footer>{confirmation}
  </section>;
}

function SegmentEditor({id,states,meta,segment,number,refItem,clip,disabled,confirmed,onAct,onGenerate}:Omit<Props,'onGenerate'>&{segment:WorkflowSegment;number:number;refItem?:AssetItem;clip?:AssetItem;disabled:boolean;onAct:(fn:()=>Promise<unknown>)=>Promise<void>;onGenerate:(stage:string,ids:string[])=>Promise<void>}){
  const [base,setBase]=useState(segment);const [draft,setDraft]=useState(segment);const [message,setMessage]=useState('');const [tab,setTab]=useState<'video'|'image'>('video');const [failure,setFailure]=useState('');const [saving,setSaving]=useState(false);
  const dirty=JSON.stringify(draft)!==JSON.stringify(base);const changed=JSON.stringify(segment)!==JSON.stringify(base);
  if(changed&&!dirty&&!saving){setBase(segment);setDraft(segment);}
  useUnsavedChanges(dirty);const {recovery,clearRecovery}=useEditorRecovery(`yc-segment-${id}-${segment.segment_id}`,JSON.stringify(draft),dirty);
  const art=states.character_design.artifact||{};const bound=boundAssets(draft,art);const assetGroups=[{key:'characters',label:'角色'},{key:'settings',label:'场景'},{key:'props',label:'道具'}] as const;const assetCount=assetGroups.reduce((n,g)=>n+(art[g.key]?.length||0),0);
  let timeline;try{timeline=readStoryboard([{episode_number:1,segments:[{...draft,segment_number:draft.segment_number||number,location:draft.location||'',characters:draft.characters||[],total_duration:segmentDuration(draft),shots:draft.shots.map((s,i)=>({...s,shot_number:s.shot_number||i+1,shot_type:s.shot_type||'中景',content:s.content||s.plot||''}))}]}])[0].segments[0];}catch{timeline=undefined;}
  const issues=timeline?storyboardIssues([{episode_number:1,segments:[timeline]}]):[];
  function patch(values:Partial<WorkflowSegment>){setDraft({...draft,...values});setMessage('');}
  async function save(){if(saving)return;setSaving(true);setFailure('');try{const result=await saveStudioSegment(id,segment.segment_id,base,draft);const value=result.artifact.episodes?.flatMap(e=>e.segments||[]).find(s=>s.segment_id===segment.segment_id)||draft;setBase(value);setDraft(value);clearRecovery();setMessage('已保存，后续步骤需重新确认');toast.success('镜头已保存，后续步骤需重新确认');await onAct(async()=>{});}catch(e){setFailure(errorMessage(e,'保存失败，输入已保留'));}finally{setSaving(false);}}
  function shot(index:number,values:Partial<WorkflowSegment['shots'][number]>){const shots=draft.shots.map((s,i)=>i===index?{...s,...values}:s);patch({shots,total_duration:shots.reduce((n,s)=>n+(s.duration||0),0)});}
  const item=tab==='video'?clip:refItem;const versions=item?.versions||[];
  async function selectVersion(path:string){if(dirty){setFailure('请先保存分镜修改，再选择媒体版本');return;}await onAct(()=>saveSelections(id,tab==='video'?'video_generation':'reference_generation',{[tab==='video'?'clips':'scenes']:[{...item!,selected:path,generation_status:'done'}]}));}
  function toggleAsset(asset:AssetItem){
    if(!asset.selected)return;
    const alreadyBound=bound.some(item=>item.id===asset.id);
    if(!alreadyBound&&bound.length>=10)return;
    patch({reference_asset_ids:alreadyBound?bound.filter(item=>item.id!==asset.id).map(item=>item.id):[...bound.map(item=>item.id),asset.id]});
  }
  const clipStatus=videoItemStatus(clip);
  return <>
    <div className="yc-shot-editor">
      <header className="hb-shot-editor-header">
        <div><h3>分镜编辑</h3><small>{dirty?'未保存修改':message||'已保存'}</small></div>
        <button className="yc-secondary-button" disabled={disabled||saving||!dirty||issues.length>0} onClick={()=>void save()}><Save size={12}/>{saving?'保存中…':'保存'}</button>
      </header>
      {failure&&<p role="alert" className="yc-project-error">{failure}</p>}
      {changed&&dirty&&<p className="yc-studio-notice">服务器内容有更新。你的输入仍保留，请复制需要的内容，再重新读取。</p>}
      {recovery&&!dirty&&<div className="yc-recovery-notice">有未保存编辑。<button onClick={()=>{try{setDraft(JSON.parse(recovery));}catch{setFailure('备份格式无效');}}}>恢复</button><button onClick={clearRecovery}>丢弃</button></div>}
      <fieldset disabled={disabled||saving} className="yc-shot-fields">
        <div className="hb-shot-summary-grid">
          <section className="hb-shot-description">
            <h4>分镜描述</h4>
            <p className="hb-field-hint">按镜头编辑画面与对白，人物名与说话人保持对应。</p>
            {draft.shots.map((s,index)=><label key={index} className="hb-description-field">
              {draft.shots.length>1&&<span>镜头 {index+1} · {s.shot_type||'未指定景别'}</span>}
              <textarea aria-label={`镜头 ${index+1} 内容`} rows={draft.shots.length===1?7:4} value={s.content||s.plot||''} onChange={e=>shot(index,{content:e.target.value,plot:e.target.value})}/>
            </label>)}
            <label className="hb-description-field"><span>画面与氛围 · 参考图提示词</span><textarea aria-label="参考图提示词" rows={2} value={draft.visual_prompt||''} onChange={e=>patch({visual_prompt:e.target.value})} placeholder="描述画面风格与环境；留空时由生成流程整理"/></label>
          </section>
          <section className="hb-reference-section">
            <div className="hb-inspector-label"><h4>参考素材</h4><span>{bound.length}/{assetCount} 已绑定</span></div>
            <div className="hb-reference-list">
              {assetGroups.map(group=>(art[group.key]?.length||0)>0&&<div className="hb-reference-group" key={group.key}>
                <h5>{group.label}</h5>
                {art[group.key]?.map(asset=>{const selected=bound.some(item=>item.id===asset.id);return <button type="button" className="hb-reference-item" key={asset.id}
                  aria-label={`${selected?'解除绑定':'绑定'}${group.label} ${asset.name}`} aria-pressed={selected}
                  title={`${asset.name} · ${asset.description}${!asset.selected?' · 需先制作图片':''}`}
                  disabled={!asset.selected||(!selected&&bound.length>=10)} onClick={()=>toggleAsset(asset)}>
                  <span className="hb-reference-thumb">{asset.selected?<Image unoptimized src={assetUrl(asset.selected)} alt="" width={30} height={30}/>:<ImagePlus size={14}/>}</span>
                  <span className="hb-reference-copy"><strong>{asset.name}</strong><small>{selected?'可参考':asset.selected?'未绑定':'尚无图片'}</small></span>
                </button>;})}
              </div>)}
              {!assetCount&&<p className="hb-reference-empty">还没有可绑定素材，请先在资产制作中完成角色、场景和道具图片。</p>}
            </div>
            <small className="hb-reference-limit">最多绑定 10 项；修改后需保存分镜。</small>
          </section>
        </div>
        <section className="hb-video-prompt-section">
          <div className="hb-inspector-label"><h4>视频提示词</h4><small>{draft.video_prompt?'当前镜头的动作、运镜与对白要求':'留空时使用分镜内容'}</small></div>
          <textarea aria-label="视频提示词" rows={10} placeholder="可直接修改本段的动作、运镜和对白要求" value={draft.video_prompt||''} onChange={e=>patch({video_prompt:e.target.value})}/>
        </section>
        <details className="hb-shot-details">
          <summary>场景、人物、景别与镜头时长</summary>
          <div className="yc-shot-meta"><label>场景<input value={draft.location||''} onChange={e=>patch({location:e.target.value})}/></label><label>出场人物<input value={(draft.characters||[]).join('、')} placeholder="人物之间用顿号分隔" onChange={e=>patch({characters:e.target.value.split(/[、,，]/).map(n=>n.trim()).filter(Boolean)})}/></label></div>
          {draft.shots.map((s,index)=><div className="yc-shot-content" key={index}><div>
            <span>镜头 {index+1}</span><input aria-label={`镜头 ${index+1} 景别`} value={s.shot_type||''} placeholder="景别" onChange={e=>shot(index,{shot_type:e.target.value})}/>
            <label>时长<input aria-label={`镜头 ${index+1} 时长`} type="number" min={1} max={600} value={s.duration||1} onChange={e=>shot(index,{duration:Number(e.target.value)})}/>s</label>
            {draft.shots.length>1&&<button aria-label={`删除镜头 ${index+1}`} onClick={()=>{const shots=draft.shots.filter((_,i)=>i!==index).map((value,i)=>({...value,shot_number:i+1}));patch({shots,total_duration:shots.reduce((n,value)=>n+(value.duration||0),0)});}}><X size={14}/></button>}
          </div></div>)}
          <button className="yc-secondary-button" onClick={()=>patch({shots:[...draft.shots,{shot_number:draft.shots.length+1,shot_type:'中景',duration:3,content:''}],total_duration:segmentDuration(draft)+3})}><Plus size={14}/>添加镜头</button>
        </details>
        <details className="hb-shot-details"><summary>对白时间表与角色校验{issues.length?` · ${issues.length} 项需处理`:''}</summary>
          {timeline?<DialogueTimeline segment={timeline} editing disabled={disabled||saving} issues={issues} onChange={value=>patch(value)}/>:<p className="hb-field-hint">补全分镜内容后显示对白时间表。</p>}
        </details>
      </fieldset>
      {issues.length>0&&<div role="alert" className="yc-project-error">{issues.map((issue,index)=><p key={index}>{issue.message}</p>)}</div>}
    </div>
    <aside className="yc-shot-preview" aria-label="当前片段预览与版本">
      <header className="hb-shot-player-header"><h3>分镜 {String(number).padStart(2,'0')}</h3><StatusTag tone={clipStatus==='done'?'done':clipStatus==='failed'?'error':clipStatus==='running'?'running':'todo'} label={statusNames[clipStatus]}/><small>{segmentDuration(draft)}s</small>{item?.selected&&<a href={assetUrl(item.selected)} download><Download size={12}/>下载</a>}</header>
      <nav className="hb-preview-switch" aria-label="预览类型"><button aria-pressed={tab==='video'} onClick={()=>setTab('video')}>视频预览</button><button aria-pressed={tab==='image'} onClick={()=>setTab('image')}>参考图</button></nav>
      <div className="yc-shot-player" data-ratio={meta.video_ratio||'16:9'}>{item?.selected?(tab==='video'?<VideoPreview key={item.selected} src={assetUrl(item.selected)} controls className="w-full max-h-[420px]"/>:<Image unoptimized width={640} height={480} src={assetUrl(item.selected)} alt={`片段 ${number} 参考图`}/>):<div><Film size={30}/><p>{tab==='video'?'尚未生成视频':'尚未生成参考图'}</p><small>{tab==='video'?'先检查参考图，再生成当前分镜视频':'可生成或上传本地图片'}</small></div>}</div>
      <section className="hb-preview-history"><h4>历史{tab==='video'?'视频':'图片'} <span>{versions.length}</span></h4><div className="yc-media-versions">{versions.map((path,index)=><button key={path} aria-pressed={path===item?.selected} disabled={disabled||dirty} onClick={()=>void selectVersion(path)}>{tab==='image'?<Image unoptimized width={90} height={68} src={assetUrl(path)} alt=""/>:<video aria-hidden="true" tabIndex={-1} src={assetUrl(path)} muted playsInline preload="metadata"/>}<span>版本 {index+1}{path===item?.selected?' · 当前':''}</span></button>)}</div></section>
      <section className="hb-preview-references"><div className="hb-inspector-label"><h4>绑定参考图</h4><span>{bound.length}</span></div><div className="yc-bound-assets">{bound.map(asset=><button key={asset.id} disabled={disabled||saving} title={`解除绑定 ${asset.name}`} aria-label={`解除绑定 ${asset.name}`} onClick={()=>toggleAsset(asset)}><Image unoptimized src={assetUrl(asset.selected)} alt={asset.name} width={68} height={64}/><span>{asset.name}</span></button>)}{!bound.length&&<p>尚未绑定参考图，可在中间参考素材区域选择。</p>}</div></section>
      <div className="yc-shot-generate"><div className="hb-duration-card"><span>分镜时长</span><strong>{segmentDuration(draft)}<small>s</small></strong><p>在详细字段中调整，保存后生效。</p></div><p>{modelDisplayName(meta.video_model||'尚未选择视频模型')} · {meta.video_resolution||'720P'} · {segmentDuration(draft)}s</p>{dirty&&<small>先保存当前修改，再生成</small>}<button className="yc-primary-button" disabled={disabled||dirty||!confirmed.includes(tab==='video'?'reference_generation':'storyboard')} onClick={()=>void onGenerate(tab==='video'?'video_generation':'reference_generation',[segment.segment_id])}>{item?.selected?'重新生成':'生成'}{tab==='video'?'本段视频':'本段参考图'}</button><label className="yc-upload-button"><Upload size={13}/>上传参考图<input type="file" accept="image/png,image/jpeg,image/webp" aria-label="上传本段参考图" disabled={disabled||dirty||!confirmed.includes('storyboard')} onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)void onAct(()=>uploadArtifactImage(id,'reference_generation','scenes',segment.segment_id,file));}}/></label></div>
    </aside>
  </>;
}
