import {modelDisplayName} from './modelLabel.ts';
export type ServiceKind = 'text' | 'image' | 'video';
export type ModelField = 'llm' | 'vlm' | 'image_t2i' | 'image_it2i' | 'video_first_frame' | 'video_start_end' | 'video_reference' | 'video_text';
export interface Capabilities {api_contract_verified?:boolean; adapter_ability_types?:string[]; ratios?:string[]; resolutions?:string[]; duration?:{min?:number;max?:number;integer?:boolean;verified?:boolean;values?:number[]}}
export interface CatalogModel {id:string; name:string; provider:string; type:string[]; capabilities?:Capabilities; concurrency?:number}
export interface ServiceConnection {id:string;name:string;kind:ServiceKind;provider:string;base_url:string;models:string[];enabled:boolean;enable_proxy:boolean;credentials:Record<string,boolean>;test:{status:string;message:string;tested_at:number|null}}
export interface CreationDefaults {style:string;video_ratio:string;video_resolution:string;video_generation_mode:'text'|'first_frame'|'start_end_frame'|'reference';target_duration:number;language:'zh-CN';enable_concurrency:boolean;models:Record<ModelField,string>}
export interface AdvancedSettings {server:{host:string;port:number;log_level:string;access_log:boolean};common:{proxy:string;print_model_input:boolean}}
export interface SettingsDocument {revision:number;connections:ServiceConnection[];defaults:CreationDefaults;catalog:CatalogModel[];providers:{id:string;label:string;base_url:string}[];advanced:AdvancedSettings}
export interface ConnectionDraft {name:string;kind:ServiceKind;provider:string;base_url:string;models:string[];enabled:boolean;enable_proxy:boolean;credentials:Record<string,string>;clear_credentials:string[]}
export interface StorageInfo {paths:Record<string,string>;disk:{total:number;used:number;free:number};worker_concurrency:number;task_count:number;tasks:{task_id:string;session_id:string;stage:string;status:string;created_at:number;config_version?:string}[]}
export const KIND_LABELS:Record<ServiceKind,string>={text:'文本',image:'图片',video:'视频'};
export const MODEL_LABELS:Record<ModelField,string>={llm:'剧本与分镜',vlm:'图片评估',image_t2i:'文生图',image_it2i:'图生图',video_first_frame:'首帧生视频',video_start_end:'首尾帧生视频',video_reference:'参考图生视频',video_text:'文生视频'};
export const SERVICE_STATUS:Record<string,string>={untested:'未测试',available:'连接可用',unsupported:'能力待验',missing:'未配置',error:'连接异常'};

export function settingsEqual(a:unknown,b:unknown):boolean{
  if(a===b)return true;
  if(!a||!b||typeof a!=='object'||typeof b!=='object')return false;
  const left=a as Record<string,unknown>,right=b as Record<string,unknown>;
  const keys=Object.keys(left);
  return Array.isArray(a)===Array.isArray(b)&&keys.length===Object.keys(right).length&&keys.every(key=>Object.hasOwn(right,key)&&settingsEqual(left[key],right[key]));
}

export async function settingsRequest<T>(path:string,method='GET',body?:unknown):Promise<T>{
  let response:Response;
  const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),20000);
  try{response=await fetch(`/api/settings${path}`,{method,headers:body?{'Content-Type':'application/json'}:undefined,body:body?JSON.stringify(body):undefined,cache:'no-store',signal:controller.signal});}
  catch{throw new Error('连接失败或超时，请检查后端后重试。当前输入已保留。');}
  finally{clearTimeout(timeout);}
  const data=await response.json().catch(()=>null);
  if(!response.ok)throw new Error(data?.error?.message||`请求失败（HTTP ${response.status}）`);
  return data as T;
}
export function connectionDraft(conn:ServiceConnection):ConnectionDraft{return {name:conn.name,kind:conn.kind,provider:conn.provider,base_url:conn.base_url,models:[...conn.models],enabled:conn.enabled,enable_proxy:conn.enable_proxy,credentials:{},clear_credentials:[]};}
export function serviceModelId(connection:string,model:string){return `svc/${connection}/${model}`;}
export function modelCapability(model:string,catalog:CatalogModel[]){return catalog.find(m=>m.id===(model.startsWith('svc/')?model.split('/').slice(2).join('/'):model))?.capabilities;}
export function supportsField(model:CatalogModel,field:ModelField){
  const types:Partial<Record<ModelField,string>>={llm:'llm',vlm:'vlm',image_t2i:'t2i',image_it2i:'i2i'};
  if(types[field])return model.type.includes(types[field]!);
  const abilities:Partial<Record<ModelField,string>>={video_first_frame:'first_frame_i2v',video_start_end:'start_end_frame_i2v',video_reference:'reference_to_video',video_text:'text_to_video'};
  const tags=model.capabilities?.adapter_ability_types||[];
  return tags.includes(abilities[field]!)||(field==='video_start_end'&&tags.includes('start_end_i2v'));
}
export function defaultOptions(doc:SettingsDocument,field:ModelField){
  const result:{id:string;label:string}[]=[];
  for(const conn of doc.connections.filter(c=>c.enabled))for(const model of doc.catalog.filter(m=>conn.models.includes(m.id)&&supportsField(m,field)))result.push({id:serviceModelId(conn.id,model.id),label:`${conn.name} · ${modelDisplayName(model.id,model.name)}`});
  for(const model of doc.catalog.filter(m=>supportsField(m,field)))result.push({id:model.id,label:modelDisplayName(model.id,model.name)});
  return result;
}
