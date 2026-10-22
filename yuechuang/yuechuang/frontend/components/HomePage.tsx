'use client';

import { errorMessage, type ProjectMeta, type SessionSummary } from '@/lib/workflowTypes';

import React, { useState, useRef, useEffect } from 'react';
import Link from 'next/link';
import { Settings2, ArrowRight, Check, Trash2, Loader2, Upload, Lightbulb, FileText, X, Sparkles } from 'lucide-react';
import Image from 'next/image';
import { PROMPT_EXAMPLES } from '@/config/examples';
import {
  STYLES,
  VIDEO_RATIOS,
  VIDEO_RESOLUTIONS,
  VIDEO_GENERATION_MODES,
  type ProviderGroup,
  type VideoGenerationMode,
} from '@/config/models';
import ProjectCard from './ProjectCard';
import {useProjectCovers} from './ProjectCover';
import {modelDisplayName} from '@/lib/modelLabel';
import {toast} from '@/lib/uiFeedback';
import { fetchModelGroupsByType, fetchVideoModelGroupsByAbility } from '@/lib/modelRegistry';
import { fetchApiModels, type ApiModelOption } from '@/lib/workflowApi';
import { compatibleVideoFormat } from '@/lib/modelSelection';

export interface ProjectParams extends ProjectMeta {
  title?: string;
  source_type?: 'original' | 'ip' | 'legacy';
  project_revision?: number;
  target_duration?: number;
  user_textbox_input?: string;
  idea: string;
  file_path?: string; // 上传的文件路径 (由后端返回的文件名)
  style: string;
  video_ratio: string;
  video_resolution: string;
  llm_model: string;
  vlm_model: string;
  image_t2i_model: string;
  image_it2i_model: string;
  video_model: string;
  video_first_frame_model: string;
  video_start_end_model: string;
  video_reference_model: string;
  video_text_model: string;
  video_generation_mode: VideoGenerationMode;
  expand_idea?: boolean;
  enable_concurrency?: boolean;
  web_search?: boolean;
  episodes?: number;
}

interface HistoryItem {
  id: string;
  idea: string;
  style?: string;
  date: string;
  status: string;
  stages?: Record<string, string>;
  confirmed_stages?: string[];
  summary?: SessionSummary;
  episodes?: SessionSummary[];
}

interface HomePageProps {
  onStartProject: (params: ProjectParams) => Promise<void>;
  onResumeProject: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => Promise<void>;
  history: HistoryItem[];
}

const INSPIRATION_IMAGES = [
  '/ui/inspiration-space.png',
  '/ui/inspiration-ink.png',
  '/ui/inspiration-detective.png',
  '/ui/inspiration-cat.png',
  '/ui/inspiration-mars.png',
  '/ui/inspiration-wuxia.png',
];

const STYLE_PRESETS = [
  {id:'realistic',label:'写实短剧',note:'电影光 · 真人质感',image:'/ui/style-realistic.jpg'},
  {id:'anime',label:'动漫故事',note:'平涂线稿 · 黄昏天际',image:'/ui/style-anime.jpg'},
  {id:'3d-animation',label:'3D 动画',note:'圆润造型 · 柔和光',image:'/ui/style-3d.jpg'},
  {id:'chinese-ink',label:'国风水墨',note:'留白 · 淡墨山水',image:'/ui/style-ink.jpg'},
];

export default function HomePage({ onStartProject, onDeleteSession, history }: HomePageProps) {
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const [idea, setIdea] = useState('');
  const [showSettings, setShowSettings] = useState(false);
  const [selectedStyle, setSelectedStyle] = useState('realistic');
  const [selectedLLM, setSelectedLLM] = useState('');
  const [selectedVLM, setSelectedVLM] = useState('');
  const [selectedT2I, setSelectedT2I] = useState('');
  const [selectedI2I, setSelectedI2I] = useState('');
  const [selectedFirstFrameVideo, setSelectedFirstFrameVideo] = useState('');
  const [selectedStartEndVideo, setSelectedStartEndVideo] = useState('');
  const [selectedReferenceVideo, setSelectedReferenceVideo] = useState('');
  const [selectedTextVideo, setSelectedTextVideo] = useState('');
  const [selectedVideoMode, setSelectedVideoMode] = useState<VideoGenerationMode>('first_frame');
  const [selectedRatio, setSelectedRatio] = useState('9:16');
  const [selectedResolution, setSelectedResolution] = useState('720P');
  const [targetDuration,setTargetDuration]=useState(60);
  const [configLoading, setConfigLoading] = useState(true);
  const [configError, setConfigError] = useState('');
  const [enableConcurrency, setEnableConcurrency] = useState(false);
  const [webSearch, setWebSearch] = useState(false);
  const [episodes, setEpisodes] = useState(1);
  const deleteDialog=useRef<HTMLDialogElement>(null);
  const recent=history.slice(0,3);
  const covers=useProjectCovers(recent.map(item=>item.id));

  // 上传相关状态
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadedFile, setUploadedFile] = useState<{name: string, path: string} | null>(null);

  // 管理模式状态
  const [manageMode, setManageMode] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [videoCapabilities, setVideoCapabilities] = useState<ApiModelOption[]>([]);
  const [llmProviders, setLlmProviders] = useState<ProviderGroup[]>([]);
  const [vlmProviders, setVlmProviders] = useState<ProviderGroup[]>([]);
  const [t2iProviders, setT2iProviders] = useState<ProviderGroup[]>([]);
  const [i2iProviders, setI2iProviders] = useState<ProviderGroup[]>([]);
  const [firstFrameVideoProviders, setFirstFrameVideoProviders] = useState<ProviderGroup[]>([]);
  const [startEndVideoProviders, setStartEndVideoProviders] = useState<ProviderGroup[]>([]);
  const [referenceVideoProviders, setReferenceVideoProviders] = useState<ProviderGroup[]>([]);
  const [textVideoProviders, setTextVideoProviders] = useState<ProviderGroup[]>([]);
  const activeVideoModel =
    selectedVideoMode === 'start_end_frame'
      ? selectedStartEndVideo
      : selectedVideoMode === 'reference'
        ? selectedReferenceVideo
        : selectedVideoMode === 'text'
          ? selectedTextVideo
          : selectedFirstFrameVideo;
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState('');
  const createLock = useRef(false);
  const deleteLock=useRef(false);
  const canStart = Boolean((idea.trim() || uploadedFile) && !creating && !uploading && !configLoading);

  useEffect(() => {
    let cancelled = false;
    fetchApiModels({mediaType: 'video'})
      .then(models => { if (!cancelled) setVideoCapabilities(models); })
      .catch(() => {});
    fetchModelGroupsByType('llm')
      .then(groups => { if (!cancelled) setLlmProviders(groups); })
      .catch(() => {});
    fetchModelGroupsByType('vlm')
      .then(groups => { if (!cancelled) setVlmProviders(groups); })
      .catch(() => {});
    fetchModelGroupsByType('t2i')
      .then(groups => { if (!cancelled) setT2iProviders(groups); })
      .catch(() => {});
    fetchModelGroupsByType('i2i')
      .then(groups => { if (!cancelled) setI2iProviders(groups); })
      .catch(() => {});
    fetchVideoModelGroupsByAbility('first_frame_i2v')
      .then(groups => { if (!cancelled) setFirstFrameVideoProviders(groups); })
      .catch(() => {});
    fetchVideoModelGroupsByAbility('start_end_frame_i2v')
      .then(groups => { if (!cancelled) setStartEndVideoProviders(groups); })
      .catch(() => {});
    fetchVideoModelGroupsByAbility('reference_to_video')
      .then(groups => { if (!cancelled) setReferenceVideoProviders(groups); })
      .catch(() => {});
    fetchVideoModelGroupsByAbility('text_to_video')
      .then(groups => { if (!cancelled) setTextVideoProviders(groups); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadDefaultConfig = async () => {
      setConfigLoading(true);
      setConfigError('');
      try {
        const resp = await fetch('/api/settings');
        if (!resp.ok) throw new Error('读取默认模型配置失败');
        const data = await resp.json();
        const models = data.defaults?.models || {};
        const generation = data.defaults || {};
        // Legacy config compatibility: older config.yaml only has models.video, so treat it as first-frame video.
        const firstFrameModel = models.video_first_frame || '';
        const startEndModel = models.video_start_end || '';
        const referenceModel = models.video_reference || '';
        const textModel = models.video_text || '';
        const videoMode = (generation.video_generation_mode || 'first_frame') as VideoGenerationMode;
        if (cancelled) return;
        setSelectedStyle(generation.style || 'realistic');
        setTargetDuration(generation.target_duration || 60);
        setEnableConcurrency(!!generation.enable_concurrency);
        setSelectedLLM(models.llm || '');
        setSelectedVLM(models.vlm || '');
        setSelectedT2I(models.image_t2i || '');
        setSelectedI2I(models.image_it2i || '');
        setSelectedVideoMode(videoMode);
        setSelectedFirstFrameVideo(firstFrameModel);
        setSelectedStartEndVideo(startEndModel);
        setSelectedReferenceVideo(referenceModel);
        setSelectedTextVideo(textModel);
        setSelectedRatio(generation.video_ratio || '16:9');
        setSelectedResolution(generation.video_resolution || '720P');
      } catch (e) {
        if (!cancelled) setConfigError(errorMessage(e, '读取默认模型配置失败'));
      } finally {
        if (!cancelled) setConfigLoading(false);
      }
    };
    loadDefaultConfig();
    return () => { cancelled = true; };
  }, []);

  useEffect(()=>{if(deleteTarget)deleteDialog.current?.showModal();else deleteDialog.current?.close();},[deleteTarget]);

  const handleDelete = async () => {
    if (!deleteTarget||deleteLock.current) return;
    deleteLock.current=true;setDeleting(true);
    setDeleteError('');
    try {
      await onDeleteSession(deleteTarget);
      setDeleteTarget(null);toast.success('项目已删除');
    } catch (e) {
      const message=errorMessage(e, '删除失败');setDeleteError(message);toast.error(message);
    } finally {
      deleteLock.current=false;setDeleting(false);
    }
  };

  const activeVideoProviders =
    selectedVideoMode === 'start_end_frame'
      ? startEndVideoProviders
      : selectedVideoMode === 'reference'
        ? referenceVideoProviders
        : selectedVideoMode === 'text'
          ? textVideoProviders
          : firstFrameVideoProviders;

  const activeVideoCaps = videoCapabilities.find(model => model.id === activeVideoModel)?.capabilities;
  const supportedResolutions = Array.isArray(activeVideoCaps?.resolutions)
    ? activeVideoCaps.resolutions.filter((value): value is string => typeof value === 'string')
    : VIDEO_RESOLUTIONS.map(item => item.id);
  const supportedRatios = Array.isArray(activeVideoCaps?.ratios) ? activeVideoCaps.ratios : null;
  const applyVideoFormat = (model: string) => {
    const format = compatibleVideoFormat({video_resolution: selectedResolution, video_ratio: selectedRatio},
      videoCapabilities.find(item => item.id === model)?.capabilities);
    setSelectedResolution(format.video_resolution);
    setSelectedRatio(format.video_ratio);
  };
  const missingModelOption = (value: string, groups: ProviderGroup[]) =>
    groups.some(group => group.models.some(model => model.id === value)) ? null :
      <option value={value} disabled>{value ? `${modelDisplayName(value)} · 当前不可用，请重新选择` : '请选择模型'}</option>;

  const setActiveVideoModel = (value: string) => {
    applyVideoFormat(value);
    if (selectedVideoMode === 'start_end_frame') {
      setSelectedStartEndVideo(value);
    } else if (selectedVideoMode === 'reference') {
      setSelectedReferenceVideo(value);
    } else if (selectedVideoMode === 'text') {
      setSelectedTextVideo(value);
    } else {
      setSelectedFirstFrameVideo(value);
    }
  };

  const selectedVideoModeLabel = VIDEO_GENERATION_MODES.find(item => item.id === selectedVideoMode)?.label || '首帧生视频';

  const handleStart = async () => {
    if (!canStart || createLock.current) {if(!idea.trim()&&!uploadedFile){promptRef.current?.focus();setCreateError('先写下一句话创意，或导入故事文件。');}return;}
    createLock.current = true; setCreating(true); setCreateError('');
    try { await onStartProject({
      idea,
      file_path: uploadedFile?.path, // 如果上传了文件，传给后端
      style: selectedStyle,
      video_ratio: selectedRatio,
      video_resolution: selectedResolution,
      llm_model: selectedLLM,
      vlm_model: selectedVLM,
      image_t2i_model: selectedT2I,
      image_it2i_model: selectedI2I,
      video_generation_mode: selectedVideoMode,
      video_first_frame_model: selectedFirstFrameVideo,
      video_start_end_model: selectedStartEndVideo,
      video_reference_model: selectedReferenceVideo,
      video_text_model: selectedTextVideo,
      video_model: activeVideoModel,
      enable_concurrency: enableConcurrency,
      web_search: webSearch,
      episodes, target_duration: targetDuration,
    }); } catch (e) {const message=errorMessage(e, '创建失败，输入已保留');setCreateError(message);toast.error(message);}
    finally {createLock.current = false; setCreating(false);}
  };

  const handleExampleClick = (text: string) => {
    setIdea(text);
    promptRef.current?.focus();
    promptRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const allowedExtensions = ['.doc', '.docx', '.txt', '.md', '.pdf'];
    const extension = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();

    if (!allowedExtensions.includes(extension)) {
      toast.error(`仅支持 ${allowedExtensions.join(', ')} 格式的文件`);
      return;
    }

    setUploading(true);
    const formData = new FormData();
    formData.append('file', file);

    try {
      const response = await fetch('/api/upload_file', {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        throw new Error('文件上传失败');
      }

      const data = await response.json();
      if (data.file_path) {
        // 记录已上传的文件信息，不修改输入框
        toast.success('文档已导入，创建项目后可核对正文');
        setUploadedFile({
          name: file.name,
          path: data.file_path
        });
      }
    } catch (error) {
      toast.error(errorMessage(error,'上传提取内容失败，请重试'));
    } finally {
      setUploading(false);
      // 清空 input 方便下次选择同一文件
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const selectModel=(label:string,value:string,onChange:(value:string)=>void,groups:ProviderGroup[]) => <label>{label}<select value={value} onChange={event=>onChange(event.target.value)}>{missingModelOption(value,groups)}{groups.map(group=><optgroup key={group.provider} label={group.label}>{group.models.map(model=><option key={model.id} value={model.id}>{modelDisplayName(model.id,model.label)}</option>)}</optgroup>)}</select></label>;
  return <div className="yc-home-experience">
    <div className="yc-home-glow" aria-hidden="true"><span/><span/><span/></div>
    <div className="yc-home-content">
      <section className="yc-home-create" aria-labelledby="home-heading">
        <div className="yc-home-stage"><div><h1 id="home-heading">写下这一集</h1><p>故事和画面方向一起存进项目。创建本身不生成画面。</p></div><button className="yc-home-settings-link" aria-expanded={showSettings} aria-controls="home-creation-settings" onClick={()=>{setShowSettings(value=>!value);if(!showSettings)requestAnimationFrame(()=>document.getElementById('home-creation-settings')?.scrollIntoView({behavior:'smooth',block:'nearest'}));}}><Settings2 size={14}/>创作设置</button></div>
        <div className="yc-home-composer" id="home-composer">
          <label className="yc-visually-hidden" htmlFor="home-idea">创作想法</label><textarea ref={promptRef} id="home-idea" value={idea} onChange={event=>{setIdea(event.target.value);setCreateError('');}} placeholder="一个人物、一处场景，或一次意想不到的转折……" maxLength={20000} rows={3} onKeyDown={event=>{if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)&&!event.nativeEvent.isComposing){event.preventDefault();void handleStart();}}}/>
          {uploadedFile&&<div className="yc-home-uploaded"><FileText size={15}/><span title={uploadedFile.name}>{uploadedFile.name}</span><button aria-label="移除已导入文档" title="移除已导入文档" onClick={()=>setUploadedFile(null)}><X size={14}/></button></div>}
          <div className="yc-home-composer-toolbar"><div className="yc-home-composer-fields"><label>集数<input aria-label="计划集数" type="number" min={1} max={24} value={episodes} onChange={event=>{const value=Number(event.target.value);setEpisodes(Math.max(1,Math.min(24,Number.isFinite(value)?Math.trunc(value):1)));}}/></label><label>每集<select aria-label="每集目标时长" value={targetDuration} onChange={event=>setTargetDuration(Number(event.target.value))}>{Array.from(new Set([30,60,90,120,targetDuration])).sort((a,b)=>a-b).map(duration=><option key={duration} value={duration}>{duration} 秒</option>)}</select></label><span className="yc-home-current-style">{STYLES.find(item=>item.id===selectedStyle)?.label.split(' / ').at(-1)} · {selectedRatio}</span></div><button className="hb-project-button is-primary yc-home-create-button" type="button" disabled={creating||uploading||configLoading} title={configLoading?'正在读取创作设置':uploading?'请等待文件上传完成':undefined} onClick={()=>void handleStart()}>{creating?<Loader2 size={16} className="animate-spin"/>:<Sparkles size={16}/>} {creating?'正在创建…':'创建项目'}<ArrowRight size={16}/></button></div>
          <div className="yc-home-cost-preview"><span>将创建 <b>{episodes} 集</b> · 每集目标 {targetDuration} 秒</span><span>创建项目免费 · 生成费用在执行前确认</span></div>
          <p className="yc-home-input-help">{!idea.trim()&&!uploadedFile?'先写一句话，不必是一份完整的剧本。':'想法会先保存为项目草稿，不会自动生成图片或视频。'}<kbd>⌘ / Ctrl + Enter</kbd></p>
          {createError&&<p role="alert" className="yc-home-inline-error">{createError}</p>}
          {configError&&<p role="alert" className="yc-home-inline-error">{configError} <Link href="/settings">查看服务设置 →</Link></p>}
        </div>
        <div className="yc-home-style-grid" aria-label="创作风格">{STYLE_PRESETS.map(item=><button key={item.id} type="button" aria-pressed={selectedStyle===item.id} className="yc-home-style-card" onClick={()=>{setSelectedStyle(item.id);promptRef.current?.focus();}}><span className="yc-home-style-image"><Image src={item.image} alt="" fill sizes="(max-width:700px) 50vw, 280px"/>{selectedStyle===item.id&&<i><Check size={14}/></i>}</span><span className="yc-home-style-copy"><b>{item.label}</b><small>{item.note}</small></span></button>)}</div>
        {showSettings&&<section className="yc-home-settings" id="home-creation-settings" aria-label="创作设置"><header><div><h3>创作设置</h3><p>这些选项会随创意保存到项目，可在后续制作中调整。</p></div><button aria-label="收起创作设置" onClick={()=>setShowSettings(false)}><X size={18}/></button></header><div className="yc-home-settings-grid">
          <label>视觉风格<select value={selectedStyle} onChange={event=>setSelectedStyle(event.target.value)}>{STYLES.map(style=><option key={style.id} value={style.id}>{style.label.split(' / ').at(-1)}</option>)}</select></label>
          <label>画面比例<select value={selectedRatio} onChange={event=>setSelectedRatio(event.target.value)}>{VIDEO_RATIOS.map(ratio=><option key={ratio.id} value={ratio.id} disabled={!!supportedRatios&&!supportedRatios.includes(ratio.id)}>{ratio.label}{supportedRatios&&!supportedRatios.includes(ratio.id)?' · 当前模型不支持':''}</option>)}</select></label>
          <label>视频分辨率<select value={selectedResolution} onChange={event=>setSelectedResolution(event.target.value)}>{Array.from(new Set([selectedResolution,...supportedResolutions])).map(value=><option key={value} value={value} disabled={!supportedResolutions.includes(value)}>{value}{!supportedResolutions.includes(value)?' · 当前模型不支持':''}</option>)}</select></label>
          {selectModel('文本模型',selectedLLM,setSelectedLLM,llmProviders)}{selectModel('视觉评估模型',selectedVLM,setSelectedVLM,vlmProviders)}{selectModel('文生图模型',selectedT2I,setSelectedT2I,t2iProviders)}{selectModel('图生图模型',selectedI2I,setSelectedI2I,i2iProviders)}
          <label>视频生成方式<select value={selectedVideoMode} onChange={event=>{const mode=event.target.value as VideoGenerationMode;setSelectedVideoMode(mode);applyVideoFormat(({first_frame:selectedFirstFrameVideo,start_end_frame:selectedStartEndVideo,reference:selectedReferenceVideo,text:selectedTextVideo})[mode]);}}>{VIDEO_GENERATION_MODES.map(mode=><option key={mode.id} value={mode.id}>{mode.label}</option>)}</select></label>
          {selectModel(`${selectedVideoModeLabel}模型`,activeVideoModel,setActiveVideoModel,activeVideoProviders)}
        </div><div className="yc-home-setting-checks"><label><input type="checkbox" checked={enableConcurrency} onChange={event=>setEnableConcurrency(event.target.checked)}/>并发生成</label><label><input type="checkbox" checked={webSearch} onChange={event=>setWebSearch(event.target.checked)}/>联网搜索</label><Link href="/settings">管理服务连接 →</Link></div></section>}
      </section>
      <input ref={fileInputRef} type="file" accept=".doc,.docx,.txt,.md,.pdf" className="yc-visually-hidden" tabIndex={-1} aria-label="上传创作文件" onChange={handleFileUpload}/>
      <div className="yc-home-shortcuts" aria-label="创作快捷入口"><button onClick={()=>fileInputRef.current?.click()} disabled={uploading}><span className="yc-home-shortcut-icon">{uploading?<Loader2 className="animate-spin"/>:<Upload/>}</span><span><b>{uploading?'正在导入…':'上传文件'}</b><small>导入小说或故事文档</small></span><ArrowRight size={16}/></button><button onClick={()=>{promptRef.current?.focus();promptRef.current?.scrollIntoView({behavior:'smooth',block:'center'});toast.info('可直接在创意输入框粘贴已有剧本，也可上传文档');}}><span className="yc-home-shortcut-icon"><FileText/></span><span><b>导入剧本</b><small>从已有的故事继续创作</small></span><ArrowRight size={16}/></button><button onClick={()=>document.getElementById('home-inspiration')?.scrollIntoView({behavior:'smooth',block:'start'})}><span className="yc-home-shortcut-icon"><Lightbulb/></span><span><b>寻找灵感</b><small>给下一个故事一个起点</small></span><ArrowRight size={16}/></button></div>
      {recent.length>0&&<section className="yc-home-recent" aria-labelledby="recent-heading"><div className="yc-home-section-head"><div><h2 id="recent-heading">继续创作</h2><p>最近的故事，等你写下下一幕。</p></div><div className="yc-home-section-actions"><button aria-pressed={manageMode} onClick={()=>setManageMode(value=>!value)}>{manageMode?'完成管理':'管理'}</button><Link href="/projects">查看全部<ArrowRight size={14}/></Link></div></div><div className="yc-project-card-grid">{recent.map(item=><ProjectCard key={item.id} compact project={item.summary||{id:item.id,title:item.idea,style:item.style,status:item.stages||{},confirmed_stages:item.confirmed_stages}} episodes={item.episodes} cover={covers[item.id]} actions={manageMode?<button className="yc-home-delete-project" aria-label={`删除 ${item.summary?.title||item.idea}`} title="删除项目" onClick={()=>{setDeleteTarget(item.id);setDeleteError('');}}><Trash2 size={16}/></button>:undefined}/>)}</div></section>}
      <section id="home-inspiration" className="yc-home-inspiration" aria-labelledby="inspiration-heading"><div className="yc-home-section-head"><div><h2 id="inspiration-heading">找一个故事的起点</h2><p>选择灵感示例，改写成你的版本。</p></div><span>灵感示例 · 非项目作品</span></div><div className="yc-home-inspiration-grid">{PROMPT_EXAMPLES.map((example,index)=><button key={example.title} onClick={()=>handleExampleClick(example.text)}><div><Image src={INSPIRATION_IMAGES[index]} alt="" fill sizes="(max-width:600px) 50vw,33vw"/></div><span><b>{example.title}</b><small>{example.description}</small></span><ArrowRight size={15}/></button>)}</div></section>
    </div>
    <dialog ref={deleteDialog} className="hb-project-dialog hb-delete-dialog" aria-labelledby="home-delete-title" onCancel={event=>{if(deleting)event.preventDefault();else setDeleteTarget(null);}}><form onSubmit={event=>{event.preventDefault();void handleDelete();}}><div className="hb-delete-icon"><Trash2 size={20}/></div><h2 id="home-delete-title">删除项目？</h2><p className="hb-delete-message">将删除「{history.find(item=>item.id===deleteTarget)?.idea||'这个项目'}」及关联产物，此操作无法恢复。</p>{deleteError&&<p role="alert" className="hb-project-error">{deleteError}</p>}<footer><button className="hb-project-button" type="button" disabled={deleting} onClick={()=>setDeleteTarget(null)}>取消</button><button className="hb-project-button is-danger-solid" disabled={deleting}>{deleting?'删除中…':'确认删除'}</button></footer></form></dialog>
  </div>;
}
