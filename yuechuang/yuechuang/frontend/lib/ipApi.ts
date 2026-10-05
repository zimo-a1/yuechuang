export interface IPMetadata {title:string;author:string;genre:string;synopsis:string;rights_status:'pending'|'owned'|'licensed';rights_basis:string;rights_scope:string}
export type ContentScope='full'|'excerpt'|'unknown';
export interface IPCatalog {source:string;source_url:string;source_id:string;position:number;fetched_at:string;cover_path:string}
export interface IPItem extends IPMetadata {catalog?:IPCatalog|null;cover_path?:string;featured?:boolean;id:string;revision:number;created_at:string;updated_at:string;latest_version:string|null;version_count:number;characters:number;chapter_count:number;content_scope:ContentScope;availability:'ready'|'missing'|'restricted';restriction:string}
export interface IPListData {items:IPItem[];total:number;genres:string[]}
export interface IPChapter {id:string;title:string;start:number;end:number;start_line:number;characters:number}
export interface IPVersion {id:string;number:number;created_at:string;filename:string;sha256:string;text_sha256:string;content_scope:ContentScope;characters:number;line_count:number;encoding:string;warnings:string[];chapters:IPChapter[]}
export interface IPDetailData {item:IPItem;versions:IPVersion[];projects:{id:string;title:string;version_number:number;version_id:string;first_chapter:number;last_chapter:number}[]}
export interface IPBinding {chapters:IPChapter[];ip_id:string;title:string;author:string;version_id:string;version_number:number;content_scope:ContentScope;first_chapter:number;last_chapter:number;direction:string;rights_basis:string;rights_scope:string}
export interface ChapterText {chapter:IPChapter;text:string;offset:number;first_line:number;next_offset:number|null;total:number}
export const SCOPE_LABELS:Record<ContentScope,string>={full:'完整正文',excerpt:'节选正文',unknown:'范围待确认'};
export const RIGHTS_LABELS:Record<IPMetadata['rights_status'],string>={pending:'待核对',owned:'本人原创声明',licensed:'已获授权声明'};
export const AVAILABILITY_LABELS:Record<IPItem['availability'],string>={ready:'可创建项目',missing:'待导入正文',restricted:'资料待完善'};
export const EMPTY_METADATA:IPMetadata={title:'',author:'',genre:'',synopsis:'',rights_status:'pending',rights_basis:'',rights_scope:''};
export function metadataOf(item:IPMetadata):IPMetadata {return {title:item.title,author:item.author,genre:item.genre,synopsis:item.synopsis,rights_status:item.rights_status,rights_basis:item.rights_basis,rights_scope:item.rights_scope};}
export function versionRestriction(item:IPItem,version:IPVersion|undefined){return !version?'尚未导入正文':version.content_scope==='unknown'?'正文范围待确认':item.rights_status==='pending'||!item.rights_basis.trim()||!item.rights_scope.trim()?'使用依据待核对':'';}
export function validateBoundaries(chapters:{title:string;start_line:number}[],lines:number){return !!chapters.length&&chapters[0].start_line===1&&chapters.every((c,i)=>!!c.title.trim()&&Number.isInteger(c.start_line)&&c.start_line>=1&&c.start_line<=lines&&(i===0||c.start_line>chapters[i-1].start_line));}
export async function ipRequest<T>(path:string,options:{method?:string;body?:unknown;key?:string}={}):Promise<T>{
  const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),60000);
  try{
    const form=typeof FormData!=='undefined'&&options.body instanceof FormData;
    const headers:Record<string,string>={};if(options.body&&!form)headers['Content-Type']='application/json';if(options.key)headers['Idempotency-Key']=options.key;
    const response=await fetch(`/api/ip${path}`,{method:options.method||'GET',headers,body:form?options.body as FormData:options.body?JSON.stringify(options.body):undefined,cache:'no-store',signal:controller.signal});
    const data=await response.json().catch(()=>null);
    if(!response.ok)throw new Error(data?.error?.message||`请求失败（${response.status}）`);
    if(!data)throw new Error('响应不完整，请刷新后重试');
    return data as T;
  }catch(error){if(error instanceof Error&&error.name!=='AbortError'&&!(error instanceof TypeError))throw error;throw new Error('连接失败或等待超时，输入已保留。重试将沿用本次操作标识。');}
  finally{clearTimeout(timer);}
}
