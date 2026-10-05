import type {IPBinding} from './ipApi';
export type NovelAction='analysis'|'direction'|'outline'|'script';
export interface NovelPerson {name:string;aliases:string[];description:string}
export interface NovelBible {summary:string;characters:NovelPerson[];relationships:{source:string;target:string;description:string}[];open_threads:string[]}
export interface NovelDirection {premise:string;tone:string;focus_characters:string[];changes:string}
export interface NovelEpisode {number:number;title:string;summary:string;characters:string[];event_ids:string[];creative_additions:string}
export interface NovelOutline {episodes:NovelEpisode[];omissions:{event_id:string;reason:string}[]}
export interface NovelEvent {id:string;description:string;quote:string;start:number;end:number;chapter_id:string;chapter_title:string;chunk_id:string}
export interface NovelScene {location:string;characters:string[];duration:number;action:string;dialogue:{speaker:string;text:string;creative_addition:boolean}[];event_ids:string[];creative_addition:boolean}
export interface NovelScript {value:{title:string;scenes:NovelScene[]};stale:boolean;session_id:string|null;created_at:string}
export interface NovelChunk {id:string;chapter_id:string;title:string;start:number;end:number;characters:number;hard_split:boolean;status:string;result:{summary:string;characters:NovelPerson[];relationships:NovelBible['relationships'];events:{description:string;quote:string;start:number;end:number}[]}|null}
export interface NovelPlan {action:NovelAction;episode:number|null;chapter_ids:string[];revision:number;model:string;provider_host:string;input_characters:number;pending_chunks:number;merge_requests:number;max_requests:number;max_output_tokens:number;images:number;video_seconds:number;retries:number;cost:number|null;plan_hash:string}
export interface NovelJob {id:string;recovery_hint?:string;status:string;step:string;action:{action:NovelAction;episode:number|null;chapter_ids:string[]};error:{code:string;message:string}|null;plan:NovelPlan;calls:{kind:string;status:string;attempt?:number;input_tokens:number|null;output_tokens:number|null;elapsed_seconds:number|null;cost:number|null}[]}
export interface NovelDetail {project_id:string;title:string;revision:number;project_revision:number;model:string;target_duration:number;episode_count:number;binding:IPBinding;config_warning:string;stale:boolean;saved_versions:number;events:NovelEvent[];jobs:NovelJob[];variant:{chunks:NovelChunk[];bible:NovelBible|null;direction:NovelDirection|null;direction_confirmed:boolean;outline:NovelOutline|null;outline_confirmed:boolean;scripts:Record<string,NovelScript>}|null}
export const NOVEL_STEPS:Record<NovelAction,string>={analysis:'原著理解',direction:'改编方向',outline:'分集大纲',script:'单集剧本'};
export const NOVEL_STATUS:Record<string,string>={pending:'待分析',running:'分析中',completed:'已完成',error:'失败',interrupted:'已中断',stopped:'已停止',queued:'排队中',stopping:'停止中'};
export function novelActive(job:NovelJob|undefined){return !!job&&['queued','running','stopping'].includes(job.status);}
export function chapterProgress(chunks:NovelChunk[]){const done=chunks.filter(c=>c.status==='completed').length;return {done,total:chunks.length,percent:chunks.length?Math.round(done/chunks.length*100):0};}
export function accountEvents(outline:NovelOutline,events:NovelEvent[]){const used=new Set(outline.episodes.flatMap(e=>e.event_ids));const reasons=new Map(outline.omissions.map(o=>[o.event_id,o.reason]));return events.filter(e=>!used.has(e.id)).map(e=>({event_id:e.id,reason:reasons.get(e.id)||''}));}
export async function novelRequest<T>(id:string,path='',options:{method?:string;body?:unknown;key?:string}={}):Promise<T>{
  const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),30000);
  try{const headers:Record<string,string>={'Content-Type':'application/json'};if(options.key)headers['Idempotency-Key']=options.key;
    const response=await fetch(`/api/projects/${encodeURIComponent(id)}/adaptation${path}`,{method:options.method||'GET',headers,body:options.body?JSON.stringify(options.body):undefined,cache:'no-store',signal:controller.signal});
    const data=await response.json().catch(()=>null);if(!response.ok)throw new Error(data?.error?.message||`请求失败（${response.status}）`);if(!data)throw new Error('响应不完整，请重试');return data;
  }catch(e){if(e instanceof Error&&e.name!=='AbortError'&&!(e instanceof TypeError))throw e;throw new Error('连接中断，已保存内容保留；生成请求重试会沿用操作标识。');}finally{clearTimeout(timer);}
}
