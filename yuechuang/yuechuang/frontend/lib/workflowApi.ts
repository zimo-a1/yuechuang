import type { AssetItem, WorkflowArtifact, ProjectMeta, SessionSummary, ActCompleteData, WorkflowShot } from './workflowTypes';

/**
 * 工作流 API 客户端
 *
 * 仅保留 IP 短剧主流程所需接口。
 * 已移除：三个专项流水线（文艺短视频 / 动作迁移 / 数字人口播）与临时工作台（sandbox）相关接口。
 */

const taskSignals = new WeakMap<Response, AbortSignal>();

export interface ProjectStatus extends ProjectMeta {
  session_id: string;
  current_stage: string;
  status: Record<string, string>;
  error: string | null;
  confirmed_stages: string[];
  meta?: ProjectMeta;
  artifacts: Record<string, WorkflowArtifact>;
  stage_progress?: Record<string, { percent?: number; message?: string; step?: string }>;
}

export interface StreamEvent {
  type: 'progress' | 'heartbeat' | 'stage_complete' | 'error' | 'content';
  message?: string;
  phase?: string;
  step_desc?: string;
  percent?: number;
  stage?: string;
  status?: string;
  requires_intervention?: boolean;
  payload_summary?: WorkflowArtifact;
  content?: string;
  time?: number;
  data?: {
    assets_preview?: WorkflowArtifact;
    asset_complete?: Partial<AssetItem> & {id: string; type: 'characters' | 'settings' | 'scenes' | 'clips'};
    beat_sheet?: string;
    act_complete?: ActCompleteData;
    scene_shots_complete?: {shots: WorkflowShot[]};
  };
}

export interface ApiModelOption {
  id: string;
  label: string;
  provider: string;
  family?: string;
  media_type?: 'image' | 'video';
  model_type?: 'llm' | 'vlm' | 't2i' | 'i2i' | 'video';
  type?: string[];
  ability_type?: string;
  ability_types?: string[];
  adapter_ability_types?: string[];
  input_modalities?: string[];
  adapter_input_modalities?: string[];
  api_contract_verified?: boolean;
  capabilities?: Record<string, unknown>;
}

async function requireOkResponse(response: Response, fallbackMessage: string): Promise<Response> {
  if (response.ok) return response;

  const payload = await response.json().catch(() => null);
  const detail = payload?.error?.message || (typeof payload?.detail === 'string' ? payload.detail : fallbackMessage);
  throw new Error(`${detail} (HTTP ${response.status})`);
}

async function projectRequest(url: string, options?: RequestInit): Promise<Response> {
  try {return await fetch(url, options);}
  catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error;
    throw new Error('无法连接本地服务，当前输入已保留。请检查后端是否启动，然后重试。');
  }
}

// ─── 会话 ───

export async function fetchSessions(): Promise<SessionSummary[]> {
  const resp = await projectRequest('/api/sessions');
  await requireOkResponse(resp, '获取会话列表失败');
  const data = await resp.json();
  return data.sessions || [];
}

export async function deleteSession(
  sessionId: string,
): Promise<{ status: string }> {
  const resp = await projectRequest(`/api/sessions/${sessionId}`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
  });
  await requireOkResponse(resp, '删除失败');
  return resp.json();
}

export interface DraftProject { session_id: string; status: Record<string, string>; meta: ProjectMeta }

export async function createProjectDraft(params: ProjectMeta & {file_path?: string}, key: string): Promise<DraftProject> {
  const response = await projectRequest('/api/projects', {
    method: 'POST', headers: {'Content-Type': 'application/json', 'Idempotency-Key': key},
    body: JSON.stringify(params),
  });
  await requireOkResponse(response, '创建项目失败，请重试');
  return response.json();
}

export async function updateProjectDraft(sessionId: string, values: Partial<ProjectMeta>, revision: number): Promise<DraftProject> {
  const response = await projectRequest(`/api/projects/${encodeURIComponent(sessionId)}`, {
    method: 'PATCH', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({...values, expected_revision: revision}),
  });
  await requireOkResponse(response, '保存项目失败，修改已保留在当前页面');
  return response.json();
}

// ─── 模型清单 ───

export async function fetchApiModels(params: {
  mediaType?: 'image' | 'video';
  modelType?: 'llm' | 'vlm' | 't2i' | 'i2i' | 'video';
  ability?: string;
  verifiedOnly?: boolean;
} = {}): Promise<ApiModelOption[]> {
  const search = new URLSearchParams();
  if (params.mediaType) search.set('media_type', params.mediaType);
  if (params.modelType) search.set('model_type', params.modelType);
  if (params.ability) search.set('ability', params.ability);
  if (params.verifiedOnly) search.set('verified_only', 'true');
  const resp = await fetch(`/api/models${search.toString() ? `?${search.toString()}` : ''}`);
  if (!resp.ok) throw new Error('获取模型列表失败');
  const data = await resp.json();
  return data.models || [];
}

// ─── 缓存 ───

export async function clearTempCache(): Promise<{ status: string; deleted: number; freed_bytes?: number; freed_mb?: number; errors?: Array<{ path: string; error: string }> }> {
  const resp = await fetch('/api/cache/temp', { method: 'DELETE' });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: '清空缓存失败' }));
    throw new Error(err.detail || '清空缓存失败');
  }
  return resp.json();
}

// ─── 产物上传 ───

export async function uploadArtifactImage(
  sessionId: string,
  stage: string,
  itemType: string,
  itemId: string,
  file: File,
): Promise<{ status: string; path: string; artifact: WorkflowArtifact; status_map: Record<string, string> }> {
  const formData = new FormData();
  formData.append('item_type', itemType);
  formData.append('item_id', itemId);
  formData.append('file', file);
  const resp = await fetch(`/api/project/${sessionId}/artifact/${stage}/upload_image`, {
    method: 'POST',
    body: formData,
  });
  await requireOkResponse(resp, '上传图片失败');
  return resp.json();
}

// ─── 项目主流程 ───

export async function startProject(params: {
  idea: string;
  file_path?: string;
  style?: string;
  video_ratio?: string;
  video_resolution?: string;
  llm_model?: string;
  vlm_model?: string;
  image_t2i_model?: string;
  image_it2i_model?: string;
  video_model?: string;
  video_first_frame_model?: string;
  video_start_end_model?: string;
  video_reference_model?: string;
  video_text_model?: string;
  video_generation_mode?: string;
  scene_number?: number;
  enable_concurrency?: boolean;
  web_search?: boolean;
  expand_idea?: boolean;
  episodes?: number;
  pilot_mode?: boolean;
}): Promise<{ session_id: string; status: string; params: ProjectMeta }> {
  const resp = await fetch('/api/project/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: '项目创建失败' }));
    throw new Error(err.detail || '项目创建失败');
  }
  return resp.json();
}

export async function getProjectStatus(sessionId: string): Promise<ProjectStatus> {
  const resp = await fetch(`/api/project/${sessionId}/status`);
  if (!resp.ok) throw new Error('Failed to get project status');
  return resp.json();
}

// 兼容旧路由名；后端实际会通过统一的 workflow state 入口返回状态。
export async function getProjectStatusFromDisk(sessionId: string): Promise<ProjectStatus> {
  const resp = await fetch(`/api/project/${sessionId}/status/from_disk`);
  if (!resp.ok) throw new Error('Failed to get project status snapshot');
  return resp.json();
}

export async function getArtifact(sessionId: string, stage: string): Promise<{stage: string; artifact: WorkflowArtifact}> {
  const resp = await fetch(`/api/project/${sessionId}/artifact/${stage}`);
  if (!resp.ok) throw new Error(`Artifact for stage '${stage}' not found`);
  return resp.json();
}

export async function executeStage(
  sessionId: string,
  stage: string,
  inputData: Record<string, unknown> = {},
  signal?: AbortSignal,
): Promise<Response> {
  const response = await fetch(`/api/project/${sessionId}/execute/${stage}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Prefer: 'respond-async', 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify(inputData),
    signal,
  });
  if (signal) taskSignals.set(response, signal);
  return requireOkResponse(response, `阶段 ${stage} 启动失败`);
}

export async function intervene(
  sessionId: string,
  stage: string,
  modifications: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<Response> {
  const response = await fetch(`/api/project/${sessionId}/intervene`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Prefer: 'respond-async', 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify({ stage, modifications }),
    signal,
  });
  if (signal) taskSignals.set(response, signal);
  return requireOkResponse(response, modifications.action === 'extract_assets' ? '素材提取失败，请重试' : `阶段 ${stage} 修改失败`);
}

export async function stopProject(sessionId: string): Promise<{ status: string }> {
  const resp = await fetch(`/api/project/${sessionId}/stop`, {
    method: 'POST',
  });
  await requireOkResponse(resp, '停止项目失败');
  return resp.json();
}

export async function updateModels(
  sessionId: string,
  models: Partial<Record<string, string | boolean>>,
): Promise<{ status: string }> {
  const resp = await fetch(`/api/project/${sessionId}/models`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(models),
  });
  await requireOkResponse(resp, '更新项目模型失败');
  return resp.json();
}

export async function saveSelections(
  sessionId: string,
  stage: string,
  selections: WorkflowArtifact,
): Promise<{ status: string }> {
  const resp = await projectRequest(`/api/project/${sessionId}/artifact/${stage}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(selections),
  });
  await requireOkResponse(resp, '保存选项失败');
  return resp.json();
}

export async function continueWorkflow(sessionId: string, stage?: string): Promise<{ status: string; next_stage?: string; status_map: Record<string, string>; confirmed_stages: string[] }> {
  const resp = await fetch(`/api/project/${sessionId}/continue`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stage }),
  });
  await requireOkResponse(resp, '继续工作流失败');
  return resp.json();
}

// ─── 阶段事件流 ───

export async function* parseStreamEvents(response: Response, signal?: AbortSignal): AsyncGenerator<StreamEvent> {
  signal ??= taskSignals.get(response);
  if (response.status === 202) {
    const accepted = await response.json();
    let sequence = 0;
    // Each poll is a short request. Unmounting/closing this page does not stop the worker.
    while (true) {
      signal?.throwIfAborted();
      const poll = await fetch(`${accepted.status_url}?after=${sequence}`, { cache: 'no-store', signal });
      await requireOkResponse(poll, '查询任务失败，重新打开项目可继续查看');
      const task = await poll.json();
      for (const event of task.events || []) {
        sequence = Math.max(sequence, event.seq);
        yield event as StreamEvent;
      }
      if (task.status === 'completed') {
        yield {
          type: 'stage_complete', stage: task.stage,
          requires_intervention: task.result?.requires_intervention || false,
          payload_summary: task.result?.payload,
        };
        return;
      }
      if (['error', 'stopped', 'interrupted'].includes(task.status)) {
        yield { type: 'error', content: task.error?.message || '任务执行失败' };
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 1500));
    }
  }
  if (!response.body) throw new Error('阶段事件流没有响应体');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let terminalEventReceived = false;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || ''; // keep incomplete trailing line in buffer
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const event: StreamEvent = JSON.parse(line);
        if (event.type === 'stage_complete' || event.type === 'error') terminalEventReceived = true;
        if (event.type !== 'heartbeat') yield event;
      } catch { /* skip malformed */ }
    }
  }
  // Process any remaining data in buffer after stream ends
  if (buffer.trim()) {
    try {
      const event: StreamEvent = JSON.parse(buffer);
      if (event.type === 'stage_complete' || event.type === 'error') terminalEventReceived = true;
      if (event.type !== 'heartbeat') yield event;
    } catch { /* skip malformed */ }
  }
  if (!terminalEventReceived) {
    throw new Error('阶段事件流提前结束，未收到完成或错误事件');
  }
}


export interface ProjectDetail {
  project: ProjectStatus;
  episodes: SessionSummary[];
  assets: (AssetItem & {kind: 'characters'|'settings'|'props'; session_id: string; episode_number: number})[];
}
export async function fetchProjectDetail(id: string): Promise<ProjectDetail> {
  const response = await projectRequest(`/api/projects/${encodeURIComponent(id)}`);
  await requireOkResponse(response, '项目读取失败'); return response.json();
}
export async function createEpisode(id: string, values: {title: string; video_resolution: string; target_duration?: number}, key: string): Promise<DraftProject> {
  const response = await projectRequest(`/api/projects/${encodeURIComponent(id)}/episodes`, {method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':key},body:JSON.stringify(values)});
  await requireOkResponse(response, '添加剧集失败'); return response.json();
}
export async function saveOriginalContent(id: string, content: string, revision: number, file_path?: string): Promise<DraftProject> {
  const response=await projectRequest(`/api/projects/${id}/source`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({content,expected_revision:revision,file_path})});
  await requireOkResponse(response,'原始内容保存失败');return response.json();
}
export async function saveScriptContent(id: string, content: string, revision: number, use_original=false): Promise<{artifact:WorkflowArtifact;meta:ProjectMeta}> {
  const response=await projectRequest(`/api/projects/${id}/script-content`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({content,expected_revision:revision,use_original})});
  await requireOkResponse(response,'剧本保存失败');return response.json();
}
export async function uploadSourceDocument(file: File): Promise<string> {
  const body=new FormData();body.append('file',file);const response=await projectRequest('/api/upload_file',{method:'POST',body});
  await requireOkResponse(response,'文件上传失败');return (await response.json()).file_path;
}
export async function fetchProjectTasks(id: string): Promise<{task_id:string;stage:string;status:string;error?:{message:string};updated_at:number;events?:{percent?:number;step_desc?:string;message?:string}[]}[]> {
  const response=await projectRequest(`/api/project/${id}/tasks`);await requireOkResponse(response,'任务读取失败');return (await response.json()).tasks;
}

export async function manageStudioAssets(id:string, values:{kind?:'characters'|'settings'|'props';id?:string;name?:string;description?:string;selected?:string;delete?:boolean}):Promise<{artifact:WorkflowArtifact}> {
  const response=await projectRequest(`/api/projects/${id}/assets`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});
  await requireOkResponse(response,'素材保存失败');return response.json();
}

export async function saveStudioSegment(id:string, segmentId:string, expected:import('./workflowTypes').WorkflowSegment, segment:import('./workflowTypes').WorkflowSegment):Promise<{artifact:WorkflowArtifact}> {
  const response=await projectRequest(`/api/projects/${id}/segments/${segmentId}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_segment:expected,segment})});
  await requireOkResponse(response,'分镜保存失败');return response.json();
}
export async function importStudioAsset(id:string, source_session_id:string, kind:'characters'|'settings'|'props', asset_id:string):Promise<{artifact:WorkflowArtifact}> {
  const response=await projectRequest(`/api/projects/${id}/assets/import`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source_session_id,kind,asset_id})});
  await requireOkResponse(response,'导入素材失败');return response.json();
}
export async function retryProjectTask(id:string, taskId:string):Promise<void> {
  const response=await projectRequest(`/api/project/${id}/tasks/${taskId}/retry`,{method:'POST',headers:{Prefer:'respond-async','Idempotency-Key':crypto.randomUUID()}});
  await requireOkResponse(response,'任务重试失败');
}
