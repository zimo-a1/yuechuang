'use client';

import { errorMessage, type ProjectMeta, type SessionSummary } from '@/lib/workflowTypes';

import React, { useState, useRef, useEffect, useId } from 'react';
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
  {id:'realistic',label:'写实短剧',note:'真实人物 · 电影质感',image:'/ui/inspiration-detective.png',sample:'风格示例'},
  {id:'anime',label:'动漫故事',note:'清晰线稿 · 平涂叙事',image:'',sample:'概念插画'},
  {id:'3d-animation',label:'3D 动画',note:'立体角色 · 柔和光影',image:'',sample:'概念插画'},
  {id:'chinese-ink',label:'国风水墨',note:'东方意境 · 山水之间',image:'/ui/inspiration-ink.png',sample:'风格示例'},
];

/** Local illustrations communicate a style, not a generated project or promised model output. */
function StylePreview({preset,priority=false,decorative=false}:{preset:typeof STYLE_PRESETS[number];priority?:boolean;decorative?:boolean}) {
  const uid=useId().replace(/:/g,'');
  if(preset.image) return <Image src={preset.image} alt={decorative?'':`${preset.label}风格示例`} fill sizes="(max-width:600px) 50vw,280px" priority={priority}/>;
  if(preset.id==='anime') return <svg className="yc-home-style-art" viewBox="0 0 600 360" preserveAspectRatio="xMidYMid slice" role="img" aria-label="动漫概念插画：蓝衣少年与月夜山峦" aria-hidden={decorative||undefined}>
    <defs><linearGradient id={`${uid}-night`} x2="0" y2="1"><stop stopColor="#193459"/><stop offset="1" stopColor="#527ca7"/></linearGradient></defs>
    <rect width="600" height="360" fill={`url(#${uid}-night)`}/>
    <circle cx="155" cy="97" r="54" fill="#e5edf3"/>
    <circle cx="178" cy="74" r="52" fill="#284569"/>
    <path d="M0 238 92 148 198 258 322 127 450 252 537 153 600 219V360H0Z" fill="#375674"/>
    <path d="m0 292 134-92 139 111 166-106 161 68v87H0Z" fill="#1f3857"/>
    <g fill="#e2efff"><circle cx="83" cy="60" r="2"/><circle cx="238" cy="72" r="1.5"/><circle cx="475" cy="70" r="2"/><circle cx="525" cy="117" r="1.5"/><path d="m438 107 3 8 8 3-8 3-3 8-3-8-8-3 8-3Z"/></g>
    <path d="M216 360v-30q2-56 71-72h67q70 17 87 102Z" fill="#b9d9e9" stroke="#15283e" strokeWidth="5"/>
    <path d="m273 261-28 67 43-13 22 45h71l-23-99Z" fill="#5d9bd1"/>
    <path d="m275 258 26-9h42l22 17-22 43-36-6Z" fill="#eed6c5" stroke="#15283e" strokeWidth="4"/>
    <path d="M266 143q-1-71 66-72 70 4 67 74l-9 66q-11 41-48 64-44-8-67-50Z" fill="#f2ded0" stroke="#15283e" strokeWidth="5"/>
    <path d="m372 136 23 6-5 69q-11 41-48 64l-22-7q44-19 50-64Z" fill="#d7b7aa"/>
    <path d="M273 182q-22-13-21-34l-13 8q0-39 21-57l-9-16 27-2q43-45 94-20l13-9-3 21q35 26 23 83l-17-8-4-34-21 24-3-26-30 37-5-29-33 41-9-19Z" fill="#142638" stroke="#112235" strokeWidth="5" strokeLinejoin="round"/>
    <path d="m269 111 33-26 49-6-40 21-28 32Zm73-9 19-20 11 10-25 27Z" fill="#304c64"/>
    <path d="m284 174 28-6m32-5 25-2" stroke="#263e51" strokeWidth="5" strokeLinecap="round"/>
    <path d="M282 184q16-11 32 0l-5 18q-17 6-27-5Zm62-3q13-12 27-3l-2 18q-14 8-25-1Z" fill="#fff" stroke="#20394f" strokeWidth="3"/>
    <g fill="#447498"><ellipse cx="301" cy="190" rx="7" ry="12"/><ellipse cx="357" cy="186" rx="7" ry="12"/></g>
    <g fill="#122536"><ellipse cx="302" cy="191" rx="3" ry="8"/><ellipse cx="358" cy="187" rx="3" ry="8"/></g>
    <g fill="#fff"><circle cx="298" cy="183" r="3"/><circle cx="354" cy="179" r="3"/></g>
    <path d="m329 190-4 26 8 1m-18 18q12 5 23-2" fill="none" stroke="#9c746d" strokeWidth="3" strokeLinecap="round"/>
    <path d="m282 281 29 34-19 31-26-22m98-46-21 30 23 36 20-26" fill="#80b4dc" stroke="#254967" strokeWidth="4"/>
    <path d="m312 317 8 43m-57-19 12 19" stroke="#e0eff9" strokeWidth="4"/>
  </svg>;
  return <svg className="yc-home-style-art" viewBox="0 0 600 360" preserveAspectRatio="xMidYMid slice" role="img" aria-label="3D概念插画：柔和光影中的圆润玩具机器人" aria-hidden={decorative||undefined}>
    <defs>
      <radialGradient id={`${uid}-room`} cx=".36" cy=".18" r=".9"><stop stopColor="#8db8df"/><stop offset=".5" stopColor="#4b749d"/><stop offset="1" stopColor="#20394f"/></radialGradient>
      <radialGradient id={`${uid}-shell`} cx=".28" cy=".12" r=".85"><stop stopColor="#fff"/><stop offset=".5" stopColor="#d5e5f1"/><stop offset=".85" stopColor="#8faec5"/><stop offset="1" stopColor="#526f89"/></radialGradient>
      <radialGradient id={`${uid}-blue`} cx=".3" cy=".2" r=".9"><stop stopColor="#94cefa"/><stop offset=".42" stopColor="#4f99d8"/><stop offset="1" stopColor="#214777"/></radialGradient>
      <linearGradient id={`${uid}-glass`} x2=".5" y2="1"><stop stopColor="#2d4d68"/><stop offset=".5" stopColor="#162d43"/><stop offset="1" stopColor="#0c1d32"/></linearGradient>
      <radialGradient id={`${uid}-shadow`}><stop stopColor="#12283ee0"/><stop offset="1" stopColor="#12283e00"/></radialGradient>
    </defs>
    <rect width="600" height="360" fill={`url(#${uid}-room)`}/>
    <path d="M0 290q301-97 600 0v70H0Z" fill="#7f9dbd" opacity=".3"/>
    <ellipse cx="320" cy="317" rx="140" ry="29" fill={`url(#${uid}-shadow)`}/>
    <ellipse cx="320" cy="303" rx="112" ry="30" fill="#597992"/>
    <ellipse cx="320" cy="294" rx="112" ry="28" fill="#a6bfce"/>
    <g fill={`url(#${uid}-shell)`}>
      <rect x="270" y="204" width="94" height="83" rx="35"/>
      <rect x="273" y="264" width="34" height="32" rx="14"/>
      <rect x="332" y="264" width="34" height="32" rx="14"/>
      <rect x="236" y="208" width="28" height="62" rx="14" transform="rotate(22 250 239)"/>
      <rect x="369" y="207" width="28" height="62" rx="14" transform="rotate(-22 383 238)"/>
    </g>
    <rect x="296" y="232" width="40" height="20" rx="10" fill={`url(#${uid}-blue)`}/>
    <path d="M315 93V77" stroke="#d2e5f2" strokeWidth="8" strokeLinecap="round"/>
    <circle cx="315" cy="70" r="12" fill={`url(#${uid}-blue)`}/>
    <g fill={`url(#${uid}-blue)`}><ellipse cx="240" cy="156" rx="19" ry="32"/><ellipse cx="397" cy="156" rx="19" ry="32"/></g>
    <rect x="244" y="98" width="148" height="122" rx="49" fill={`url(#${uid}-shell)`}/>
    <rect x="260" y="125" width="117" height="69" rx="27" fill={`url(#${uid}-glass)`} stroke="#b8d7e8" strokeWidth="2"/>
    <path d="M274 138q33-9 75-3" fill="none" stroke="#d4efff" strokeWidth="4" strokeLinecap="round" opacity=".16"/>
    <g fill="#a1dcfb"><rect x="282" y="149" width="14" height="21" rx="7"/><rect x="342" y="149" width="14" height="21" rx="7"/></g>
    <path d="M309 176q10 6 20 0" fill="none" stroke="#a1dcfb" strokeWidth="3" strokeLinecap="round"/>
    <ellipse cx="278" cy="111" rx="19" ry="5" fill="#fff" opacity=".65" transform="rotate(-12 278 111)"/>
    <circle cx="153" cy="198" r="25" fill={`url(#${uid}-blue)`}/>
    <circle cx="455" cy="115" r="19" fill={`url(#${uid}-shell)`}/>
    <circle cx="457" cy="267" r="12" fill={`url(#${uid}-blue)`}/>
    <path d="M157 253q-47-85 13-144m267 110q36-8 39-56" fill="none" stroke="#d0e7fa" strokeWidth="2" strokeDasharray="3 8" opacity=".4"/>
  </svg>;
}

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
    <div className="yc-home-content">
      <section className="yc-home-hero" aria-labelledby="home-heading">
        <div className="yc-home-hero-copy"><span className="yc-home-eyebrow"><Sparkles size={14}/> 悦创 · 创作工作空间</span><h1 id="home-heading">从一个想法，<br/>到一部<span>短片</span>。</h1><p>写下故事，选择风格。<br/>从剧本到成片，让创意一步步成为画面。</p><a href="#home-composer" className="yc-home-hero-link" onClick={()=>promptRef.current?.focus()}>开始你的故事<ArrowRight size={16}/></a></div>
        <div className="yc-home-gallery" aria-label="风格示例画廊">{[STYLE_PRESETS[3],STYLE_PRESETS[0],STYLE_PRESETS[1]].map((item,index)=><div className={`yc-home-gallery-card gallery-${index}`} key={item.id}><StylePreview preset={item} priority={index===1}/><span>{item.label}<small>{item.sample}</small></span></div>)}</div>
        <button className="yc-home-settings-link" aria-expanded={showSettings} aria-controls="home-creation-settings" onClick={()=>{setShowSettings(value=>!value);if(!showSettings)requestAnimationFrame(()=>document.getElementById('home-creation-settings')?.scrollIntoView({behavior:'smooth',block:'nearest'}));}}><Settings2 size={14}/>创作设置<ArrowRight size={14}/></button>
      </section>
      <section className="yc-home-create" aria-labelledby="create-heading">
        <div className="yc-home-section-head"><div><h2 id="create-heading">把灵感写下来</h2><p>先选一个画面方向，之后还可以调整。</p></div><span>01 / 创意开始</span></div>
        <div className="yc-home-style-grid" aria-label="创作风格">{STYLE_PRESETS.map(item=><button key={item.id} type="button" aria-pressed={selectedStyle===item.id} className="yc-home-style-card" onClick={()=>{setSelectedStyle(item.id);promptRef.current?.focus();}}><span className="yc-home-style-image"><StylePreview preset={item} decorative/><small>{item.sample}</small>{selectedStyle===item.id&&<i><Check size={14}/></i>}</span><span className="yc-home-style-copy"><b>{item.label}</b><small>{item.note}</small></span></button>)}</div>
        <div className="yc-home-composer" id="home-composer">
          <label className="yc-visually-hidden" htmlFor="home-idea">创作想法</label><textarea ref={promptRef} id="home-idea" value={idea} onChange={event=>{setIdea(event.target.value);setCreateError('');}} placeholder="一个人物、一处场景，或一次意想不到的转折……" maxLength={20000} rows={3} onKeyDown={event=>{if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)&&!event.nativeEvent.isComposing){event.preventDefault();void handleStart();}}}/>
          {uploadedFile&&<div className="yc-home-uploaded"><FileText size={15}/><span title={uploadedFile.name}>{uploadedFile.name}</span><button aria-label="移除已导入文档" title="移除已导入文档" onClick={()=>setUploadedFile(null)}><X size={14}/></button></div>}
          <div className="yc-home-composer-toolbar"><div className="yc-home-composer-fields"><label>集数<input aria-label="计划集数" type="number" min={1} max={24} value={episodes} onChange={event=>{const value=Number(event.target.value);setEpisodes(Math.max(1,Math.min(24,Number.isFinite(value)?Math.trunc(value):1)));}}/></label><label>每集<select aria-label="每集目标时长" value={targetDuration} onChange={event=>setTargetDuration(Number(event.target.value))}>{Array.from(new Set([30,60,90,120,targetDuration])).sort((a,b)=>a-b).map(duration=><option key={duration} value={duration}>{duration} 秒</option>)}</select></label><span className="yc-home-current-style">{STYLES.find(item=>item.id===selectedStyle)?.label.split(' / ').at(-1)} · {selectedRatio}</span></div><button className="hb-project-button is-primary yc-home-create-button" type="button" disabled={creating||uploading||configLoading} title={configLoading?'正在读取创作设置':uploading?'请等待文件上传完成':undefined} onClick={()=>void handleStart()}>{creating?<Loader2 size={16} className="animate-spin"/>:<Sparkles size={16}/>} {creating?'正在创建…':'创建项目'}<ArrowRight size={16}/></button></div>
          <div className="yc-home-cost-preview"><span>将创建 <b>{episodes} 集</b> · 每集目标 {targetDuration} 秒</span><span>创建项目免费 · 生成费用在执行前确认</span></div>
          <p className="yc-home-input-help">{!idea.trim()&&!uploadedFile?'先写一句话，不必是一份完整的剧本。':'想法会先保存为项目草稿，不会自动生成图片或视频。'}<kbd>⌘ / Ctrl + Enter</kbd></p>
          {createError&&<p role="alert" className="yc-home-inline-error">{createError}</p>}
          {configError&&<p role="alert" className="yc-home-inline-error">{configError} <Link href="/settings">查看服务设置 →</Link></p>}
        </div>
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
