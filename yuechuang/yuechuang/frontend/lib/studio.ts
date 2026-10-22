import type {ProjectMeta} from './workflowTypes';
export type StudioStep='source'|'rewrite'|'assets'|'videos'|'export';
export const STUDIO_STEPS: {id:StudioStep;label:string;group:string}[]=[{id:'source',label:'原始内容',group:'剧本'},{id:'rewrite',label:'AI 改写',group:'剧本'},{id:'assets',label:'资产制作',group:'制作'},{id:'videos',label:'视频制作',group:'制作'},{id:'export',label:'拼接导出',group:'导出'}];
export function stepForStage(stage:string):StudioStep{return stage==='character_design'?'assets':['storyboard','reference_generation','video_generation'].includes(stage)?'videos':stage==='post_production'?'export':'rewrite';}
export function stageForStep(step:StudioStep,confirmed:string[]):string{return step==='assets'?'character_design':step==='export'?'post_production':step==='videos'?(!confirmed.includes('storyboard')?'storyboard':!confirmed.includes('reference_generation')?'reference_generation':'video_generation'):'script_generation';}
export function stepComplete(step:StudioStep,meta:ProjectMeta,confirmed:string[]){if(step==='source')return !!meta.idea?.trim()||!!meta.ip_binding;if(step==='rewrite')return !meta.source_changed&&confirmed.includes('script_generation');if(step==='assets')return confirmed.includes('character_design');if(step==='videos')return ['storyboard','reference_generation','video_generation'].every(s=>confirmed.includes(s));return confirmed.includes('post_production');}

export function studioHref(id:string,stage:string){return `/studio/${encodeURIComponent(id)}?step=${stepForStage(stage)}&stage=${stage}`;}
