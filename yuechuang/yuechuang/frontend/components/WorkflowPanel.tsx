'use client';

import { errorMessage, type WorkflowArtifact, type AssetItem, type LoglineData, type SessionSummary } from '@/lib/workflowTypes';
import {toast} from '@/lib/uiFeedback';

import React, { useState, useRef, useEffect, useCallback } from 'react';
import Link from 'next/link';
import ProjectDraftEditor from './ProjectDraftEditor';
import ProjectStudio from './ProjectStudio';
import StudioVideoWorkbench from './StudioVideoWorkbench';
import StudioModelControls from './StudioModelControls';
import StudioAssets from './StudioAssets';
import StudioScriptEditor from './StudioScriptEditor';
import {STUDIO_STEPS, stepForStage, stageForStep, type StudioStep} from '@/lib/studio';
import {confirmationFollowUp, projectHref} from '@/lib/projects';
import { canLeaveEditor } from '@/lib/unsavedChanges';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  type ProjectStatus,
  createProjectDraft,
  executeStage,
  parseStreamEvents,
  getProjectStatus,
  getProjectStatusFromDisk,
  continueWorkflow,
  stopProject,
  intervene,
  getArtifact,
  fetchSessions,
  updateModels,
  deleteSession,
  saveSelections,
} from '@/lib/workflowApi';
import TopBar, { STAGES, type ModelConfig } from './TopBar';
import HomePage, { type ProjectParams } from './HomePage';
import {
  ScriptStage,
  CharacterStage,
  StoryboardStage,
  ReferenceStage,
  VideoStage,
  PostProductionStage,
  type StageState,
  type StageStatus,
  type StageViewProps,
} from './stages';

const STAGE_ORDER: string[] = STAGES.map(s => s.id);

const STAGE_COMPONENTS: Record<string, React.ComponentType<StageViewProps>> = {
  script_generation: ScriptStage,
  character_design: CharacterStage,
  storyboard: StoryboardStage,
  reference_generation: ReferenceStage,
  video_generation: VideoStage,
  post_production: PostProductionStage,
};

interface HistoryItem {
  id: string;
  idea: string;
  style?: string;
  date: string;
  status: string;
  stages?: Record<string, string>;
  confirmed_stages?: string[]; // stageId => status 映射
  summary?: SessionSummary;
  episodes?: SessionSummary[];
}

function initStageStates(): Record<string, StageState> {
  const states: Record<string, StageState> = {};
  for (const s of STAGE_ORDER) {
    states[s] = {
      status: 'pending',
      progress: 0,
      progressMessage: '',
      artifact: null,
      error: null,
    };
  }
  return states;
}

function isLogline(value: unknown): value is LoglineData {
  const fields = recordValue(value);
  return ['logline', 'who', 'goal', 'conflict', 'twist', 'theme'].every(key => typeof fields[key] === 'string');
}

function recordValue(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : {};
}

function stringValue(value: unknown, fallback: string): string {
  return typeof value === 'string' ? value : fallback;
}

function stageStatus(value: string | undefined): StageStatus {
  switch (value) {
    case 'running': case 'waiting': case 'completed': case 'error': case 'stopped': return value;
    case 'session_completed': return 'completed';
    default: return 'pending';
  }
}

function hasDoneArtifactItems(artifact: WorkflowArtifact | null | undefined): boolean {
  if (!artifact) return false;
  return (['clips', 'images', 'scenes', 'characters', 'settings'] as const).some(key => {
    const items = artifact?.[key];
    return Array.isArray(items) && items.some((item) => item?.status === 'done' || item?.selected);
  });
}

function hasRunningArtifactItems(artifact: WorkflowArtifact | null | undefined): boolean {
  if (!artifact) return false;
  return (['clips', 'images', 'scenes', 'characters', 'settings'] as const).some(key => {
    const items = artifact?.[key];
    return Array.isArray(items) && items.some((item) => item?.status === 'running');
  });
}

function mergeAssetVersions(current?: string[], incoming?: string[]): string[] {
  const merged: string[] = [];
  [...(Array.isArray(current) ? current : []), ...(Array.isArray(incoming) ? incoming : [])].forEach(path => {
    if (path && !merged.includes(path)) merged.push(path);
  });
  return merged;
}

function mergeAssetUpdateItem(item: AssetItem, assetUpdate: Partial<AssetItem>): AssetItem {
  const hasExistingSelection = Boolean(item?.selected);
  const nextStatus = hasExistingSelection && ['done', 'failed'].includes(assetUpdate.status || '')
    ? 'done'
    : (assetUpdate.status ?? item.status);
  return {
    ...item,
    status: nextStatus,
    selected: hasExistingSelection
      ? item.selected
      : (assetUpdate.selected || item.selected),
    versions: assetUpdate.versions
      ? mergeAssetVersions(item.versions, assetUpdate.versions)
      : item.versions,
    ...carriedAssetFields(assetUpdate, nextStatus),
  };
}

function carriedAssetFields(assetUpdate: Partial<AssetItem>, nextStatus: AssetItem['status']): Partial<AssetItem> {
  const incomingError = assetUpdate.generation_error || assetUpdate.error;
  const succeeded = nextStatus === 'done' && !incomingError;
  return {
    ...(assetUpdate.review ? {review: assetUpdate.review} : {}),
    ...(incomingError ? {generation_error: incomingError} : {}),
    ...(succeeded ? {generation_error: undefined, skipped: undefined} : {}),
    ...(assetUpdate.skipped ? {skipped: true} : {}),
  };
}

function getStageProgressSnapshot(status: ProjectStatus, stageId: string): Partial<StageState> {
  const snapshot = status?.stage_progress?.[stageId];
  if (!snapshot || typeof snapshot !== 'object') return {};
  const progress = typeof snapshot.percent === 'number'
    ? Math.max(0, Math.min(100, Math.round(snapshot.percent)))
    : undefined;
  const progressMessage = typeof snapshot.message === 'string' && snapshot.message.trim()
    ? snapshot.message
    : (typeof snapshot.step === 'string' ? snapshot.step : undefined);
  return {
    ...(typeof progress === 'number' ? { progress } : {}),
    ...(progressMessage ? { progressMessage } : {}),
  };
}

export default function WorkflowPanel({sessionOverride}: {sessionOverride?:string} = {}) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const creationRequest = useRef<{payload: string; key: string} | null>(null);
  const [loadError, setLoadError] = useState('');
  const [structuredScript, setStructuredScript] = useState(false);
  const [loadRetry, setLoadRetry] = useState(0);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [activeStage, setActiveStage] = useState<string | null>(null);
  const [stageStates, setStageStates] = useState<Record<string, StageState>>(initStageStates());
  const [isRunning, setIsRunning] = useState(false);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [projectParams, setProjectParams] = useState<ProjectParams | null>(null);
  const [autoMode, setAutoMode] = useState(false);

  const [globalStatusMap, setGlobalStatusMap] = useState<Record<string, string>>({});
  const [confirmedStages, setConfirmedStages] = useState<string[]>([]);
  const [pendingSaves, setPendingSaves] = useState(0);

  const abortRef = useRef<AbortController | null>(null);
  const stoppedRef = useRef(false);
  const autoModeRef = useRef(autoMode);
  const pollRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    autoModeRef.current = autoMode;
  }, [autoMode]);

  const handleAutoModeChange = useCallback((nextAutoMode: boolean) => {
    autoModeRef.current = nextAutoMode;
    setAutoMode(nextAutoMode);
  }, []);

  const updateStageState = useCallback((stageId: string, update: Partial<StageState>) => {
    setStageStates(prev => {
      const current = prev[stageId];
      const nextUpdate = { ...update };
      if (
        current?.status === 'running' &&
        typeof update.progress === 'number' &&
        update.progress < (current.progress || 0) &&
        update.status !== 'completed' &&
        update.status !== 'waiting'
      ) {
        nextUpdate.progress = current.progress;
      }
      return {
        ...prev,
        [stageId]: { ...current, ...nextUpdate },
      };
    });
  }, []);


  const refreshSnapshot = useCallback(async (sid: string) => {
    const snapshot = await getProjectStatusFromDisk(sid);
    if (snapshot.meta) setProjectParams(prev => prev ? {...prev, ...snapshot.meta} : prev);
    setGlobalStatusMap(snapshot.status || {});
    setConfirmedStages(snapshot.confirmed_stages || []);
    setStageStates(prev => {
      const next = { ...prev };
      for (const stage of STAGE_ORDER) {
        next[stage] = { ...prev[stage], status: stageStatus(snapshot.status[stage]),
          artifact: snapshot.artifacts?.[stage] || null,
          progress: snapshot.stage_progress?.[stage]?.percent||0,
          progressMessage: snapshot.stage_progress?.[stage]?.message||snapshot.stage_progress?.[stage]?.step||'',
          error: snapshot.status[stage] === 'error' || snapshot.stage_progress?.[stage]?.step === '执行失败'
            ? snapshot.error || '执行失败，请重试或重新保存选择' : null };
      }
      return next;
    });
  }, []);

  // 清理轮询
  useEffect(() => {
    const polls = pollRef.current;
    return () => { polls.clear(); };
  }, []);

  // 轮询等待后端阶段完成。运行中优先读后端内存，后端重启后再回退到磁盘快照。
  const pollForCompletion = useCallback(async (sid: string, stageId: string) => {
    const key = `${sid}:${stageId}`;
    pollRef.current.add(key);
    while (pollRef.current.has(key)) {  // 视频任务可超过 45 分钟，持续到终态或离开页面
      await new Promise(r => setTimeout(r, 2000));
      if (!pollRef.current.has(key)) return;
      try {
        let status: ProjectStatus;
        try {
          status = await getProjectStatus(sid);
        } catch {
          status = await getProjectStatusFromDisk(sid);
        }
        if (!pollRef.current.has(key)) return;
        setGlobalStatusMap(status.status || {});
        setConfirmedStages(status.confirmed_stages || []);
        const done = Object.keys(status.status || {}).filter(k => ["completed", "session_completed"].includes(status.status[k]));
        const currentStageStatus = status.status?.[stageId] || 'idle';
        const artifacts = status.artifacts || {};
        const currentArtifact = artifacts[stageId];
        const hasRunningItems = hasRunningArtifactItems(currentArtifact);
        if (done.includes(stageId) && !hasRunningItems) {
          const isWait = currentStageStatus === 'waiting' && status.current_stage === stageId;
          let artifact = null;
          try { artifact = (await getArtifact(sid, stageId)).artifact; } catch {}
          updateStageState(stageId, {
            status: isWait ? 'waiting' : 'completed',
            progress: 100,
            progressMessage: isWait ? '等待确认' : '已完成',
            artifact,
          });
          pollRef.current.delete(key);
          return;
        }
        if (currentStageStatus === 'error' && !hasRunningItems) {
          updateStageState(stageId, {
            status: 'error',
            error: status.error || '执行出错',
            progressMessage: '执行失败',
          });
          pollRef.current.delete(key);
          return;
        }
        if (currentStageStatus === 'stopped' && !hasRunningItems) {
          updateStageState(stageId, {
            status: 'stopped',
            error: '手动停止',
            progressMessage: '已停止',
          });
          pollRef.current.delete(key);
          return;
        }
        // 后台单卡重生成时，阶段状态可能仍是 waiting/completed；只要 item 还在 running 就继续轮询。
        if (currentStageStatus !== 'running' && !hasRunningItems) {
          updateStageState(stageId, {
            status: currentStageStatus === 'waiting'
              ? 'waiting'
              : (done.includes(stageId) ? 'completed' : 'pending'),
            progress: done.includes(stageId) ? 100 : undefined,
            progressMessage: currentStageStatus === 'waiting'
              ? '等待确认'
              : (done.includes(stageId) ? '已完成' : undefined),
            artifact: currentArtifact || null,
          });
          pollRef.current.delete(key);
          return;
        }
        // 更新进度信息（优先使用后端持久化的 stage_progress 快照）
        const update: Partial<StageState> = {
          status: 'running',
          ...getStageProgressSnapshot(status, stageId),
        };

        // 无论何种状态，只要有数据就更新 artifact
        if (currentArtifact) {
          update.artifact = currentArtifact;
        }

        if (!update.progressMessage && currentArtifact) {
          // 检查是否有已生成的参考图/视频/分镜
          const hasProgress = currentArtifact.scenes?.some((s) => s.versions?.length > 0) ||
                             currentArtifact.clips?.some((c) => c.versions?.length > 0) ||
                             currentArtifact.episodes?.some((e) => (e.segments?.length || 0) > 0) ||
                             (currentArtifact.characters?.length || 0) > 0 ||
                             (currentArtifact.shots?.length || 0) > 0;
          if (hasProgress) {
            update.progressMessage = '执行中...（已生成部分资源）';
          } else {
            update.progressMessage = '执行中...';
          }
        } else if (!update.progressMessage) {
          update.progressMessage = '执行中...';
        }

        if (Object.keys(update).length > 0) {
          updateStageState(stageId, update);
        }
      } catch { /* retry */ }
    }
    pollRef.current.delete(key);
  }, [updateStageState]);

  // 页面加载时从后端获取历史记录
  useEffect(() => {
    fetchSessions()
      .then(sessions => {
        setHistory(
          sessions.filter(s=>!s.meta?.parent_project_id).map((s) => ({
            id: s.id,
            summary: s,
            episodes: sessions.filter(ep => ep.meta?.parent_project_id === s.id),
            idea: (s.title || s.idea || 'Untitled').slice(0, 60),
            style: s.style || '',
            date: s.date
              ? new Date(s.date * 1000).toLocaleDateString('zh-CN')
              : '',
            status: Object.keys(s.status || {}).length > 0 ? 'partial' : 'new',
            stages: s.status || {}, confirmed_stages: s.confirmed_stages || [],
          }))
        );
      })
      .catch(() => {});
  }, []);


  // ── 停止执行 ──
  const handleStop = async () => {
    stoppedRef.current = true;
    // 1. 断开前端 SSE 流
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    // 2. 通知后端停止
    if (sessionId) {
      try { await stopProject(sessionId); } catch { /* ignore */ }
    }
    setIsRunning(false);
    // 3. 将所有 running 阶段标记为 stopped，保留已有的 artifact
    setStageStates(prev => {
      const next = { ...prev };
      for (const s of STAGE_ORDER) {
        if (next[s]?.status === 'running') {
          // 如果已有 artifact 数据（如部分已生成的视频片段），保留为 waiting 状态以便用户操作
          const hasArtifact = hasDoneArtifactItems(next[s]?.artifact);
          if (hasArtifact) {
            next[s] = { ...next[s], status: 'waiting', error: null, progressMessage: '已停止（保留已完成内容）' };
          } else {
            next[s] = { ...next[s], status: 'error', error: '已手动停止', progressMessage: '已停止' };
          }
        }
      }
      return next;
    });
    // 4. 尝试从后端获取最新 artifact（后端可能已保存部分结果）
    if (sessionId) {
      setTimeout(async () => {
        for (const s of STAGE_ORDER) {
          try {
            const artResult = await getArtifact(sessionId, s);
            if (artResult?.artifact) {
              setStageStates(prev => {
                const cur = prev[s];
                if (!cur || cur.status === 'completed') return prev;
                // 检查 artifact 是否含有效内容
                const hasDone = hasDoneArtifactItems(artResult.artifact);
                if (hasDone) {
                  return { ...prev, [s]: { ...cur, status: 'waiting', artifact: artResult.artifact, error: null, progressMessage: '已停止（保留已完成内容）' } };
                }
                return prev;
              });
            }
          } catch { /* 该阶段无 artifact，跳过 */ }
        }
      }, 500);
    }
  };

  // ── 执行单个阶段 ──
  const runStage = async (sid: string, stageId: string, inputData: Record<string, unknown>) => {
    if (stoppedRef.current) throw new Error('Stopped');
    updateStageState(stageId, { status: 'running', progress: 0, progressMessage: '启动中...', error: null });
    setActiveStage(stageId);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await executeStage(sid, stageId, inputData, controller.signal);
      toast.info('生成任务已提交，可在任务中心查看');
      router.replace(projectHref(sid, stageId));

      for await (const event of parseStreamEvents(response)) {
        if (event.type === 'progress') {
          updateStageState(stageId, {
            progress: event.percent || 0,
            progressMessage: event.message || '',
          });
          // 处理素材预览和逐个完成事件
          if (event.data?.assets_preview) {
            updateStageState(stageId, { artifact: event.data.assets_preview });
          }
          if (event.data?.asset_complete) {
            const assetUpdate = event.data.asset_complete;
            setStageStates(prev => {
              const prevArt = prev[stageId]?.artifact;
              if (!prevArt) return prev;
              const key = assetUpdate.type;
              const items = [...(prevArt[key] || [])];
              const idx = items.findIndex((item) => item.id === assetUpdate.id);
              if (idx >= 0) {
                items[idx] = mergeAssetUpdateItem(items[idx], assetUpdate);
              }
              return {
                ...prev,
                [stageId]: { ...prev[stageId], artifact: { ...prevArt, [key]: items } },
              };
            });
          }
          // 处理剧本逐幕增量事件
          if (event.data?.beat_sheet) {
            const beatSheet = event.data.beat_sheet;
            setStageStates(prev => {
              const prevArt = prev[stageId]?.artifact || {};
              return {
                ...prev,
                [stageId]: {
                  ...prev[stageId],
                  artifact: { ...prevArt, phase: 'generating', beat_sheet: beatSheet },
                },
              };
            });
          }
          if (event.data?.act_complete) {
            const actData = event.data.act_complete;
            setStageStates(prev => {
              const prevArt = prev[stageId]?.artifact || {};
              const prevActs = prevArt.completed_acts || [];
              return {
                ...prev,
                [stageId]: {
                  ...prev[stageId],
                  artifact: {
                    ...prevArt,
                    phase: 'generating',
                    completed_acts: [...prevActs, actData],
                  },
                },
              };
            });
          }
          // 处理分镜逐场完成事件
          if (event.data?.scene_shots_complete) {
            const sceneData = event.data.scene_shots_complete;
            setStageStates(prev => {
              const prevArt = prev[stageId]?.artifact || {};
              const prevShots = prevArt.shots || [];
              return {
                ...prev,
                [stageId]: {
                  ...prev[stageId],
                  artifact: {
                    ...prevArt,
                    phase: 'generating',
                    shots: [...prevShots, ...sceneData.shots],
                  },
                },
              };
            });
          }
        } else if (event.type === 'stage_complete') {
          const newStatus: StageStatus = event.requires_intervention ? 'waiting' : 'completed';

          let artifact = null;
          try {
            const artResult = await getArtifact(sid, stageId);
            artifact = artResult.artifact;
          } catch { /* ignore */ }

          // 如果是后处理阶段，即使 artifact 为空也尝试从事件中读取预览数据
          if (stageId === 'post_production' && !artifact && event.data?.assets_preview) {
            artifact = event.data.assets_preview;
          }

          updateStageState(stageId, {
            status: newStatus,
            progress: 100,
            progressMessage: newStatus === 'waiting' ? '等待确认' : '已完成',
            artifact,
          });
          toast.success('生成结果已保存，请检查并确认');
        } else if (event.type === 'error') {
          // 取消/停止类错误：尝试获取部分结果而不是直接报错
          const isCancelError = /cancel|取消|停止/i.test(event.content || '');
          if (isCancelError) {
            let artifact = null;
            try {
              const artResult = await getArtifact(sid, stageId);
              artifact = artResult?.artifact;
            } catch { /* ignore */ }
            // 如果有已完成的内容，显示为 waiting 状态
            const hasDone = hasDoneArtifactItems(artifact);
            if (hasDone) {
              updateStageState(stageId, {
                status: 'waiting',
                progress: 100,
                progressMessage: '已停止（保留已完成内容）',
                artifact,
                error: null,
              });
              return; // 不抛异常，让工作流正常停下
            }
          }
          updateStageState(stageId, {
            status: 'error',
            error: event.content || 'Unknown error',
            progressMessage: '执行失败',
          });
          throw new Error(event.content);
        }
      }
    } catch (error) {
      if (!(error instanceof Error && error.name === 'AbortError')) {
        toast.error(errorMessage(error, '生成失败，已完成内容仍保留'));
        updateStageState(stageId, {
          status: 'error',
          error: errorMessage(error, '执行失败'),
          progressMessage: '执行失败',
        });
      }
      throw error;
    } finally {
      await refreshSnapshot(sid).catch(() => {});
    }
  };

  // ── 启动新项目 ──
  const handleStartProject = async (params: ProjectParams) => {
    if (isRunning) return;
    const payload = JSON.stringify(params);
    if (creationRequest.current?.payload !== payload) creationRequest.current = {payload, key: crypto.randomUUID()};
    setIsRunning(true);
    try {
      const result = await createProjectDraft(params, creationRequest.current.key);
      toast.success('项目已创建，接下来完善原始内容');
      creationRequest.current = null;
      stoppedRef.current = false;
      handleAutoModeChange(false);
      setProjectParams({...params, ...result.meta});
      setStageStates(initStageStates());
      setConfirmedStages([]);
      setGlobalStatusMap(result.status);
      setSessionId(result.session_id);
      setActiveStage('script_generation');
      router.push(`/studio/${result.session_id}?step=source`);
    } finally {setIsRunning(false);}
  };

  // ── 确认阶段并继续 ──
  const handleConfirmStage = async (stageId: string) => {
    if (!sessionId || isRunning || pendingSaves > 0 || stageStates[stageId]?.error) return;
    setIsRunning(true);

    try {
      stoppedRef.current = false;
      const result = await continueWorkflow(sessionId, stageId);
      toast.success('当前环节已确认');
      setConfirmedStages(result.confirmed_stages || []);
      updateStageState(stageId, { status: 'completed', progressMessage: '已确认' });
      if (result.status_map) setGlobalStatusMap(result.status_map);
      const follow = confirmationFollowUp(result.next_stage, sessionId);
      if (follow.href && result.next_stage) {
        setActiveStage(result.next_stage);
        router.push(follow.href);
      }
    } catch (error) {
      updateStageState(stageId, { error: error instanceof Error ? error.message : '确认失败，请重试' });
      toast.error(errorMessage(error, '确认失败，请重试'));
    } finally {
      setIsRunning(false);
    }
  };

  // ── 用户介入修改 ──
  const handleIntervene = async (stageId: string, modifications: Record<string, unknown>) => {
    if (!sessionId) return;
    const isBackgroundItemRegeneration = !sessionOverride && (
      (stageId === 'character_design' && (
        Array.isArray(modifications.regenerate_characters) ||
        Array.isArray(modifications.regenerate_settings) ||
        Array.isArray(modifications.regenerate_props)
      )) ||
      (stageId === 'reference_generation' && Array.isArray(modifications.regenerate_scenes)) ||
      (stageId === 'video_generation' && Array.isArray(modifications.regenerate_clips)));
    let operationError: string|null=null;
    
    // 构建完整的 inputData 传给后端，确保比例等参数能传递
    const inputData: Record<string, unknown> = {
      ...modifications,
      session_id: sessionId,
      style: projectParams?.style,
      video_ratio: projectParams?.video_ratio,
      video_resolution: projectParams?.video_resolution,
      llm_model: projectParams?.llm_model,
      vlm_model: projectParams?.vlm_model,
      image_t2i_model: projectParams?.image_t2i_model,
      image_it2i_model: projectParams?.image_it2i_model,
      video_generation_mode: projectParams?.video_generation_mode,
      video_first_frame_model: projectParams?.video_first_frame_model,
      video_start_end_model: projectParams?.video_start_end_model,
      video_reference_model: projectParams?.video_reference_model,
      video_model: projectParams?.video_model,
      video_sound: 'on',
      video_shot_type: 'multi',
    };

    // 设置 running 状态以便显示进度条（如 Logline 选择后生成剧本）
    // 若选择了 Logline，将其保存到 artifact 以便 ScriptStage 在生成期间展示
    // 若选择了模式，保留已选 Logline 并设置 generating 状态
    const artifactPatch = isLogline(modifications.selected_logline)
      ? { phase: 'generating', selected_logline: modifications.selected_logline }
      : modifications.selected_mode
        ? { phase: 'generating', selected_logline: stageStates[stageId]?.artifact?.selected_logline }
        : undefined;
    if (!isBackgroundItemRegeneration) {
      setIsRunning(true);
      updateStageState(stageId, { status: 'running', progress: 0, progressMessage: '处理中...', ...(artifactPatch ? { artifact: artifactPatch } : {}) });
    } else if (artifactPatch) {
      updateStageState(stageId, { artifact: artifactPatch });
    }
    try {
      const response = await intervene(sessionId, stageId, inputData);
      toast.info('生成任务已提交，可在任务中心查看');
      for await (const event of parseStreamEvents(response)) {
        if (event.type === 'progress') {
          if (!isBackgroundItemRegeneration) {
            updateStageState(stageId, {
              progress: event.percent || 0,
              progressMessage: event.message || '',
            });
          }
          // 处理 asset_complete 实时事件
          if (event.data?.asset_complete) {
            const assetUpdate = event.data.asset_complete;
            setStageStates(prev => {
              const prevArt = prev[stageId]?.artifact;
              if (!prevArt) return prev;
              const key = assetUpdate.type;
              const items = [...(prevArt[key] || [])];
              const idx = items.findIndex((item) => item.id === assetUpdate.id);
              if (idx >= 0) {
                items[idx] = mergeAssetUpdateItem(items[idx], assetUpdate);
              }
              return {
                ...prev,
                [stageId]: { ...prev[stageId], artifact: { ...prevArt, [key]: items } },
              };
            });
          }
          // 处理剧本逐幕增量事件 (介入模式)
          if (event.data?.beat_sheet) {
            const beatSheet = event.data.beat_sheet;
            setStageStates(prev => {
              const prevArt = prev[stageId]?.artifact || {};
              return {
                ...prev,
                [stageId]: {
                  ...prev[stageId],
                  artifact: { ...prevArt, phase: 'generating', beat_sheet: beatSheet },
                },
              };
            });
          }
          if (event.data?.act_complete) {
            const actData = event.data.act_complete;
            setStageStates(prev => {
              const prevArt = prev[stageId]?.artifact || {};
              const prevActs = prevArt.completed_acts || [];
              return {
                ...prev,
                [stageId]: {
                  ...prev[stageId],
                  artifact: {
                    ...prevArt,
                    phase: 'generating',
                    completed_acts: [...prevActs, actData],
                  },
                },
              };
            });
          }
          // 处理分镜逐场完成事件 (介入模式)
          if (event.data?.scene_shots_complete) {
            const sceneData = event.data.scene_shots_complete;
            setStageStates(prev => {
              const prevArt = prev[stageId]?.artifact || {};
              const prevShots = prevArt.shots || [];
              return {
                ...prev,
                [stageId]: {
                  ...prev[stageId],
                  artifact: {
                    ...prevArt,
                    phase: 'generating',
                    shots: [...prevShots, ...sceneData.shots],
                  },
                },
              };
            });
          }
        } else if (event.type === 'stage_complete') {
          const artResult = await getArtifact(sessionId, stageId);
          toast.success('生成结果已保存，请检查并确认');
          if (isBackgroundItemRegeneration) {
            updateStageState(stageId, { artifact: artResult.artifact });
          } else {
            updateStageState(stageId, { artifact: artResult.artifact, status: 'waiting' });
          }
        } else if (event.type === 'error') {
          operationError=event.content||'任务失败';
          updateStageState(stageId, { status: 'error', error: operationError });
        }
      }
    } catch (error) {
      operationError=error instanceof Error?error.message:'修改失败，请重试';
    } finally {
      await refreshSnapshot(sessionId).catch(() => {});
      if(operationError)updateStageState(stageId,{error:operationError});
      if (!isBackgroundItemRegeneration) {
        setIsRunning(false);
      }
    }
    if(operationError)toast.error(operationError);
    if(operationError&&sessionOverride)throw new Error(operationError);
  };

  // ── 本地更新产物（不触发服务端调用） ──
  const handleUpdateArtifact = (stageId: string, patch: WorkflowArtifact) => {
    const earlier = STAGE_ORDER.slice(0, STAGE_ORDER.indexOf(stageId));
    setConfirmedStages(prev => prev.filter(stage => earlier.includes(stage)));
    setStageStates(prev => ({
      ...prev,
      [stageId]: {
        ...prev[stageId],
        error: null,
        artifact: { ...(prev[stageId]?.artifact || {}), ...patch },
      },
    }));
  };

  // ── 保存用户选择到服务端 ──
  const handleSaveSelections = async (stageId: string, selections: Record<string, unknown>): Promise<void> => {
    if (!sessionId) throw new Error('项目未就绪，请重新打开后保存');
    setPendingSaves(count => count + 1);
    try {
      // 构建更新: 把用户选择写回 artifact 中每个 item 的 selected 字段
      const art = stageStates[stageId]?.artifact;
      if (!art) throw new Error('项目内容尚未加载，请重新打开后保存');

      let patch: WorkflowArtifact = {};
      if (stageId === 'script_generation') {
        // 剧本阶段：直接保存整个 data 覆盖 artifact
        patch = selections;
      } else if (stageId === 'character_design') {
        const { _editDescs, ...restSelections } = selections;
        const descriptions = recordValue(_editDescs);
        const chars = (art.characters || []).map((c) => ({
          ...c,
          selected: stringValue(restSelections[c.id], c.selected) || c.selected,
          description: stringValue(recordValue(descriptions.characters)[c.id], c.description),
        }));
        const sets = (art.settings || []).map((s) => ({
          ...s,
          selected: stringValue(restSelections[s.id], s.selected) || s.selected,
          description: stringValue(recordValue(descriptions.settings)[s.id], s.description),
        }));
        patch = { characters: chars, settings: sets };
      } else if (stageId === 'storyboard') {
        // 分镜阶段：保存 shots 数据（排除 original_shots，只保留 artifact 需要的字段）
        const rest = { ...selections };
        delete rest.original_shots;
        patch = { ...rest, user_modified: true };
      } else if (stageId === 'reference_generation') {
        const { _editDescs, ...restSelections } = selections;
        const descriptions = recordValue(_editDescs);
        const scenes = (art.scenes || []).map((s) => ({
          ...s,
          selected: stringValue(restSelections[s.id], s.selected) || s.selected,
          description: stringValue(descriptions[s.id], s.description),
        }));
        patch = { scenes };
      } else if (stageId === 'video_generation') {
        const { _editDescs, ...restSelections } = selections;
        const descriptions = recordValue(_editDescs);
        const clips = (art.clips || []).map((c) => ({
          ...c,
          selected: stringValue(restSelections[c.id], c.selected) || c.selected,
          description: stringValue(descriptions[c.id], c.description),
        }));
        patch = { clips };
      }

      await saveSelections(sessionId, stageId, patch);
      await refreshSnapshot(sessionId);
      toast.success('修改已保存');
    } catch (error) {
      updateStageState(stageId, { error: error instanceof Error ? error.message : '保存失败，请重试' });
      toast.error(errorMessage(error, '保存失败，请重试'));
      throw error; // 抛出让 StageActions 捕获以恢复按钮状态
    } finally {
      setPendingSaves(count => count - 1);
    }
  };

  // ── 重新生成当前阶段 ──
  const handleRegenerate = async (stageId: string) => {
    if (!sessionId || isRunning) return;
    stoppedRef.current = false;
    setIsRunning(true);

    // 清空当前阶段状态
    updateStageState(stageId, {
      status: 'running',
      progress: 0,
      progressMessage: '重新生成中...',
      artifact: null,
      error: null,
    });

    // 将该阶段之后的所有阶段重置为 pending
    const idx = STAGE_ORDER.indexOf(stageId);
    for (let i = idx + 1; i < STAGE_ORDER.length; i++) {
      updateStageState(STAGE_ORDER[i], {
        status: 'pending',
        progress: 0,
        progressMessage: '',
        artifact: null,
        error: null,
      });
    }

    try {
      const inputData: Record<string, unknown> = {
        session_id: sessionId,
        style: projectParams?.style,
        llm_model: projectParams?.llm_model,
        vlm_model: projectParams?.vlm_model,
        image_t2i_model: projectParams?.image_t2i_model,
        image_it2i_model: projectParams?.image_it2i_model,
        video_generation_mode: projectParams?.video_generation_mode,
        video_first_frame_model: projectParams?.video_first_frame_model,
        video_start_end_model: projectParams?.video_start_end_model,
        video_reference_model: projectParams?.video_reference_model,
        video_model: projectParams?.video_model,
        video_ratio: projectParams?.video_ratio,
        video_resolution: projectParams?.video_resolution,
      };

      // 尝试获取场景数
      try {
        const scriptArt = await getArtifact(sessionId, 'script_generation');
        if (scriptArt?.artifact?.scenes?.length) {
          inputData.scene_number = scriptArt.artifact.scenes.length;
        }
      } catch { /* ignore */ }

      await runStage(sessionId, stageId, inputData);

      const status = await getProjectStatus(sessionId);
      if (status?.status?.[stageId] === 'waiting') {
        updateStageState(stageId, { status: 'waiting' });
        setActiveStage(stageId);
      }
    } catch (error) {
      if (!stoppedRef.current) console.error('Regenerate error:', error);
    } finally {
      setIsRunning(false);
    }
  };

  // ── 恢复历史项目 ──
  const loadProject = useCallback(async (sid: string, targetStage: string | null, status: ProjectStatus, isCurrent: () => boolean) => {
    try {
      const newStates = initStageStates();
      const stMap = status.status || {};
      const allStatusStages = Object.keys(stMap);
      const currentStage = status.current_stage;
      
      const completedStages = allStatusStages.filter(k => ["completed", "session_completed"].includes(stMap[k]));

      for (const sName of allStatusStages) {
        if (!STAGE_ORDER.includes(sName)) continue;
        const cStatus = stMap[sName];
        if (cStatus === 'pending' || cStatus === 'idle') {
          // 即使是 pending，如果 artifacts 中有数据，也尝试恢复数据（用于初始占位显示）
          if (status.artifacts?.[sName]) {
            newStates[sName].artifact = status.artifacts[sName];
          }
          continue;
        }

        const isCompleted = ["completed", "session_completed"].includes(cStatus);
        const isError = cStatus === 'error' || status.stage_progress?.[sName]?.step === '执行失败';
        const isWaiting = cStatus === 'waiting';
        const isStopped = cStatus === 'stopped';
        const isRunningStatus = cStatus === 'running';
        const progressSnapshot = getStageProgressSnapshot(status, sName);

        newStates[sName] = {
          status: isError ? 'error' : (isWaiting ? 'waiting' : (isStopped ? 'stopped' : (isCompleted ? 'completed' : 'running'))),
          progress: isCompleted ? 100 : (typeof progressSnapshot.progress === 'number' ? progressSnapshot.progress : 0),
          progressMessage: isError
            ? '执行失败'
            : (isWaiting
              ? '等待确认'
              : (isStopped
                ? '已停止'
                : (isCompleted
                  ? '已完成'
                  : (progressSnapshot.progressMessage || (isRunningStatus ? '执行中...' : '进行中'))))),
          artifact: status.artifacts?.[sName] || null,
          error: isError ? (status.error || '执行出错') : (isStopped ? '手动停止' : null),
        };
        
        // 如果内存中没有，尝试异步拉取
        if (!newStates[sName].artifact) {
          try {
            const artResult = await getArtifact(sid, sName);
            newStates[sName].artifact = artResult.artifact;
          } catch { /* ignore */ }
        }
      }

      if (!isCurrent()) return;
      setLoadError('');
      setSessionId(sid);
      setGlobalStatusMap(status.status || {});
      setConfirmedStages(status.confirmed_stages || []);

      // 恢复项目参数：会话级生成配置统一从 meta 中读取，兼容少量旧扁平字段。
      const meta = status.meta || {};
      const s = Object.keys(meta).length > 0 ? meta : status;
      {
        // Legacy session compatibility: old sessions only stored one video_model, so hydrate it as the first-frame model.
        const restoredFirstFrameVideoModel = s.video_first_frame_model || s.video_model || '';
        setProjectParams({
          ...meta,
          title: s.title, source_type: s.source_type, project_revision: s.project_revision,
          target_duration: s.target_duration, episodes: s.episodes, user_textbox_input: s.user_textbox_input,
          idea: s.idea || s.user_textbox_input || '',
          style: s.style || '',
          video_ratio: s.video_ratio || '16:9',
          video_resolution: s.video_resolution || '720P',
          llm_model: s.llm_model || '',
          vlm_model: s.vlm_model || '',
          image_t2i_model: s.image_t2i_model || '',
          image_it2i_model: s.image_it2i_model || '',
          video_generation_mode: s.video_generation_mode || 'first_frame',
          video_first_frame_model: restoredFirstFrameVideoModel,
          video_start_end_model: s.video_start_end_model || 'wan2.7-i2v',
          video_reference_model: s.video_reference_model || 'wan2.7-r2v',
          video_text_model: s.video_text_model || s.video_model || '',
          video_model: s.video_model || '',
          expand_idea: s.expand_idea || false,
          enable_concurrency: s.enable_concurrency || false,
          web_search: s.web_search || false,
        });
      }

      // 确定要显示的阶段（优先使用 URL 中的目标阶段）
      let finalStage = targetStage;
      if (!finalStage || !STAGE_ORDER.includes(finalStage)) {
        finalStage = STAGE_ORDER.includes(currentStage) ? currentStage : (completedStages.filter(s => STAGE_ORDER.includes(s)).pop() || STAGE_ORDER[0]);
      }
      setActiveStage(finalStage);
      setStageStates(newStates);

      // 轮询正在执行的阶段；后台单卡重生成时阶段可能不是 running，但 item 会是 running。
      for (const stageId of STAGE_ORDER) {
        if (stMap[stageId] === 'running' || hasRunningArtifactItems(status.artifacts?.[stageId])) {
          pollForCompletion(sid, stageId);
        }
      }
    } catch (error) {
      if (isCurrent()) setLoadError(errorMessage(error, '项目加载失败，请重试'));
    }
  }, [pollForCompletion]);

  const handleResumeProject = (sid: string) => {router.push(`/projects/${encodeURIComponent(sid)}`);};

  // 检查 URL 参数，加载指定的 session 和阶段
  useEffect(() => {
    let cancelled = false;
    const polls = pollRef.current;
    const legacySession = !sessionOverride ? searchParams.get('session') : null;
    if (legacySession) {
      router.replace(projectHref(legacySession, searchParams.get('stage') || undefined));
      return;
    }
    const sessionParam = sessionOverride;
    const stepParam = searchParams.get('step') as StudioStep;
    const stageParam = searchParams.get('stage') || (STUDIO_STEPS.some(item=>item.id===stepParam) ? stageForStep(stepParam, []) : null);
    if (sessionParam) {
      // 保存目标阶段，等会话加载完成后再设置
      const targetStage = stageParam && STAGE_ORDER.includes(stageParam) ? stageParam : null;
      getProjectStatus(sessionParam)
        .catch(() => getProjectStatusFromDisk(sessionParam))
        .then(status => {
          if (!cancelled) return loadProject(sessionParam, targetStage, status, () => !cancelled);
        })
        .catch(() => { if (!cancelled) setLoadError('项目不存在或暂时无法读取，请返回项目中心或重试。'); });
    }
    return () => { cancelled = true; polls.clear(); };
  }, [searchParams, loadProject, loadRetry, sessionOverride, router]);

  // ── 返回首页 ──
  // 处理阶段点击，更新 URL
  const handleStageClick = (stage: string) => {
    if (!canLeaveEditor()) return;
    setActiveStage(stage);
    if (sessionId) {
      router.push(projectHref(sessionId, stage));
    }
  };

  const handleGoHome = () => {
    if (!canLeaveEditor()) return;
    setActiveStage(null);
    router.push('/');
  };

  // ── 删除历史记录 ──
  const handleDeleteSession = async (sid: string) => {
    await deleteSession(sid);
    setHistory(prev => prev.filter(h => h.id !== sid));
  };

  // ── 模型配置变更处理 ──
  const handleModelConfigChange = (config: ModelConfig) => {
    setProjectParams(prev => prev ? { ...prev, ...config } : null);
    // 同步到后端会话元数据
    if (sessionId) {
      const payload = { ...config };
      updateModels(sessionId, payload).catch(console.error);
    }
  };

  // 构建 modelConfig for TopBar
  const modelConfig: ModelConfig | undefined = projectParams
    ? {
        llm_model: projectParams.llm_model,
        vlm_model: projectParams.vlm_model,
        image_t2i_model: projectParams.image_t2i_model,
        image_it2i_model: projectParams.image_it2i_model,
        // Legacy session compatibility: projectParams may come from an old session with only video_model.
        video_generation_mode: projectParams.video_generation_mode || 'first_frame',
        video_first_frame_model: projectParams.video_first_frame_model || projectParams.video_model,
        video_start_end_model: projectParams.video_start_end_model || 'wan2.7-i2v',
        video_reference_model: projectParams.video_reference_model || 'wan2.7-r2v',
        video_model: projectParams.video_model,
        video_text_model: projectParams.video_text_model,
        video_ratio: projectParams.video_ratio,
        video_resolution: projectParams.video_resolution || '720P',
        enable_concurrency: projectParams.enable_concurrency || false,
        // web_search: projectParams.web_search || false,
        // expand_idea: projectParams.expand_idea || false,
      }
    : undefined;

  // ── 计算 stageStatuses for TopBar ──
  // 直接根据后端返回的 status 数据计算图标状态

  
  const stageStatuses: Record<string, StageStatus> = {};

  for (let i = 0; i < STAGE_ORDER.length; i++) {
    const s = STAGE_ORDER[i];
    const backendStatus = globalStatusMap[s]; // get status from global mapping updated from backend

    if (backendStatus === 'completed') {
      stageStatuses[s] = 'completed';
    } else if (backendStatus === 'waiting') {
      stageStatuses[s] = 'waiting';
    } else if (backendStatus === 'running') {
      stageStatuses[s] = 'running';
    } else if (backendStatus === 'error') {
      stageStatuses[s] = 'error';
    } else {
      stageStatuses[s] = 'pending';
    }
  }

  // ── 计算项目状态 ──
  // 后端阶段状态: pending, running, waiting, completed, stopped, error
  // 前端 StageStatus: pending, running, waiting, completed, error
  const hasRunning = Object.values(stageStates).some(s => s.status === 'running');
  const hasWaiting = Object.values(stageStates).some(s => s.status === 'waiting');
  const hasError = Object.values(stageStates).some(s => s.status === 'error');
  const allCompleted = STAGE_ORDER.every(stage => confirmedStages.includes(stage));
  const effectiveIsRunning = isRunning || hasRunning || pendingSaves > 0;

  let computedStatus: string;
  if (hasRunning) computedStatus = 'running';
  else if (hasWaiting) computedStatus = 'waiting';
  else if (hasError) computedStatus = 'error';
  // Check if stopped in stageStates
  else if (Object.values(stageStates).some(s => s.status === 'stopped')) computedStatus = 'stopped';
  else if (allCompleted && stageStates[STAGE_ORDER[STAGE_ORDER.length - 1]]?.status === 'completed') {
    computedStatus = 'completed';
  } else if (allCompleted) {
    computedStatus = 'completed';
  } else {
    computedStatus = Object.values(stageStates).some(s => s.status === 'stopped') ? 'stopped' : 'pending';
  }

  const projectStatus = sessionId ? computedStatus : undefined;

  // ── 渲染阶段内容 ──
  let stageContent: React.ReactNode = null;
  if (activeStage && STAGE_COMPONENTS[activeStage]) {
    const Component = STAGE_COMPONENTS[activeStage];

    const state = stageStates[activeStage];

    // 判断后续阶段是否已执行过：如有，则隐藏"确认并继续"
    const idx = STAGE_ORDER.indexOf(activeStage);
    const hasSubsequentExecution = STAGE_ORDER.slice(idx + 1).some(
      s => stageStates[s]?.status && stageStates[s].status !== 'pending'
    );
    const showConfirm = !confirmedStages.includes(activeStage);

    // 计算是否有待生成的项（阶段2、4、5）
    let hasPendingItems = false;
    if (activeStage === 'character_design') {
      const chars = state?.artifact?.characters || [];
      const sets = state?.artifact?.settings || [];
      hasPendingItems = chars.some((c) => !c.selected) || sets.some((s) => !s.selected);
    } else if (activeStage === 'reference_generation') {
      const scenes = state?.artifact?.scenes || [];
      hasPendingItems = scenes.some((s) => !s.selected);
    } else if (activeStage === 'video_generation') {
      const clips = state?.artifact?.clips || [];
      // 检查是否有未选中的 clips
      const hasUnselected = clips.some((c) => !c.selected);
      // 检查参考图阶段的 scenes 数量是否大于 video clips 数量
      // 如果是，说明有新分镜需要生成
      const refScenes = stageStates['reference_generation']?.artifact?.scenes || [];
      const hasNewShots = refScenes.length > clips.length;
      hasPendingItems = hasUnselected || hasNewShots;
    }

    // 计算后续阶段是否已开始（阶段1、3）
    const hasNextStageStarted = hasSubsequentExecution;

    // 传递第4阶段的 artifact 到第5阶段用于依赖检查
    const referenceArtifact = activeStage === 'video_generation'
      ? stageStates['reference_generation']?.artifact
      : undefined;

    // 传递第1阶段的 artifact 到第4、5、6阶段用于获取剧集标题
    const scriptArtifact = (activeStage === 'reference_generation' || activeStage === 'video_generation' || activeStage === 'post_production')
      ? stageStates['script_generation']?.artifact
      : undefined;

    stageContent = (
      <Component
        key={`${sessionId}-${activeStage}`}
        state={state}
        previousConfirmed={idx === 0 || confirmedStages.includes(STAGE_ORDER[idx - 1])}
        sessionId={sessionId || ''}
        onConfirm={() => handleConfirmStage(activeStage)}
        onIntervene={(mods: Record<string, unknown>) => {void handleIntervene(activeStage, mods).catch(()=>{});}}
        onRegenerate={() => handleRegenerate(activeStage)}
        onUpdateArtifact={(patch: WorkflowArtifact) => handleUpdateArtifact(activeStage, patch)}
        onSaveSelections={(selections: Record<string, unknown>) => handleSaveSelections(activeStage, selections)}
        showConfirm={showConfirm}
        isRunning={effectiveIsRunning}
        hasPendingItems={hasPendingItems}
        hasNextStageStarted={hasNextStageStarted}
        referenceArtifact={referenceArtifact || undefined}
        scriptArtifact={scriptArtifact || undefined}
        projectMeta={projectParams || undefined}
        artifacts={Object.keys(stageStates).reduce<Record<string, WorkflowArtifact>>((acc, key) => {
          acc[key] = stageStates[key].artifact || {};
          return acc;
        }, {})}
      />
    );
  };

  const requestedSession = (sessionOverride || searchParams.get('session'));
  const showHome = !requestedSession && activeStage === null;
  const loadingProject = Boolean(requestedSession && (sessionId !== requestedSession || !projectParams));
  if (sessionId && projectParams && activeStage === 'script_generation' && !stageStates.script_generation.artifact && !effectiveIsRunning) {
    stageContent = <div className="flex h-full flex-col min-h-0">
      {stageStates.script_generation.error && <p role="alert" className="yc-project-error">{stageStates.script_generation.error}</p>}
      <ProjectDraftEditor key={sessionId} sessionId={sessionId} meta={projectParams} busy={effectiveIsRunning}
        onSaved={meta => setProjectParams(prev => prev ? {...prev, ...meta} : prev)}
        onGenerate={async () => {
          setIsRunning(true); stoppedRef.current = false;
          try {await runStage(sessionId, 'script_generation', {});} finally {setIsRunning(false);}
        }}/>
    </div>;
  }

  if (sessionOverride) {
    if (loadError) return <div className="yc-project-empty" role="alert"><p>{loadError}</p><button className="yc-secondary-button" onClick={() => setLoadRetry(v=>v+1)}>重试</button><Link href="/projects">返回项目</Link></div>;
    if (loadingProject || !projectParams || !sessionId) return <div className="yc-project-empty" role="status">正在打开剧集…</div>;
    const stepParam=searchParams.get('step') as StudioStep;
    const step=STUDIO_STEPS.some(item=>item.id===stepParam) ? stepParam : activeStage==='script_generation'&&!stageStates.script_generation.artifact ? 'source' : stepForStage(activeStage||'script_generation');
    const goStep=(next:StudioStep)=>{if(!canLeaveEditor())return;setStructuredScript(false);const stage=stageForStep(next,confirmedStages);setActiveStage(stage);router.push(`/studio/${sessionId}?step=${next}&stage=${stage}`);};
    let content=stageContent;
    if (step==='source'||(step==='rewrite'&&!structuredScript)) content=<StudioScriptEditor
      key={`${sessionId}-${step}`} id={sessionId} meta={projectParams}
      artifact={stageStates.script_generation.artifact} source={step==='source'} busy={effectiveIsRunning}
      progress={stageStates.script_generation.progressMessage} error={stageStates.script_generation.error}
      onSaved={()=>refreshSnapshot(sessionId)} onStructured={()=>{if(canLeaveEditor())setStructuredScript(true);}}
      onNext={()=>{if(step==='source')goStep('rewrite');else void handleConfirmStage('script_generation');}}
      onGenerate={async()=>{setIsRunning(true);stoppedRef.current=false;try{await runStage(sessionId,'script_generation',{});}finally{setIsRunning(false);}}}/>;
    if(step==='assets') content=<StudioAssets projectId={projectParams.parent_project_id||sessionId} id={sessionId} artifact={stageStates.character_design.artifact} script={stageStates.script_generation.artifact}
      confirmed={confirmedStages.includes('script_generation')} busy={effectiveIsRunning}
      error={stageStates.character_design.error||stageStates.script_generation.error} progress={stageStates.script_generation.status==='running'?stageStates.script_generation.progressMessage:stageStates.character_design.progressMessage}
      onRefresh={()=>refreshSnapshot(sessionId)} onExtract={kind=>handleIntervene('script_generation',{action:'extract_assets',kind})}
      onAccept={async()=>{await continueWorkflow(sessionId,'script_generation');await refreshSnapshot(sessionId);}}
      onGenerate={(kind,ids)=>handleIntervene('character_design',{[`regenerate_${kind}`]:ids})}
      onContinue={()=>void handleConfirmStage('character_design')}/>;
    if(step==='videos') content=<StudioVideoWorkbench id={sessionId} meta={projectParams} states={stageStates}
      confirmed={confirmedStages} busy={effectiveIsRunning} onRefresh={()=>refreshSnapshot(sessionId)}
      onGenerate={(stage,mods)=>handleIntervene(stage,mods)} onAssets={()=>goStep('assets')} onNext={()=>goStep('export')}/>;
    return <ProjectStudio id={sessionId} meta={projectParams} step={step} stage={activeStage||'script_generation'}
      confirmed={confirmedStages} busy={effectiveIsRunning} onStep={goStep} onStage={handleStageClick}
      onRefresh={()=>{if(canLeaveEditor())void refreshSnapshot(sessionId).then(()=>toast.success('本集数据已刷新')).catch(e=>toast.error(errorMessage(e,'刷新失败，请重试')));}}
        controls={modelConfig&&<StudioModelControls config={modelConfig} disabled={effectiveIsRunning} duration={{sessionId,value:projectParams.target_duration||60,revision:projectParams.project_revision||0,hasStoryboard:Boolean(stageStates.storyboard.artifact?.episodes?.length),onSaved:meta=>setProjectParams(prev=>prev?{...prev,...meta}:prev)}} onChange={async config=>{await updateModels(sessionId,{...config});await refreshSnapshot(sessionId);}}/>}>
      {structuredScript&&step==='rewrite'&&<button className="yc-structured-back" onClick={()=>{if(canLeaveEditor())setStructuredScript(false);}}>← 返回改写正文</button>}{content}
    </ProjectStudio>;
  }

  return (
    <div className="yc-workspace-root flex h-screen w-full min-w-0 flex-col bg-gray-50/50">
      <TopBar
        activeStage={activeStage}
        stageStatuses={stageStatuses}
        onStageClick={handleStageClick}
        onHomeClick={handleGoHome}
        hasSession={!showHome && sessionId !== null}
        isRunning={effectiveIsRunning}
        onStop={handleStop}
        autoMode={autoMode}
        onAutoModeChange={handleAutoModeChange}
        modelConfig={activeStage === 'script_generation' && !stageStates.script_generation.artifact && projectParams?.source_type === 'original' ? undefined : modelConfig}
        onModelConfigChange={handleModelConfigChange}
        projectStatus={projectStatus}
      />

      {!showHome && !loadingProject && !loadError && <div className="yc-workspace-context">
        <Link href="/projects">项目</Link><span>/</span><strong>{projectParams?.title || stageStates.script_generation.artifact?.title || '历史项目'}</strong>
        <span className="yc-workspace-source">{projectParams?.source_type === 'original' ? '一句话原创' : projectParams?.source_type === 'ip' ? 'IP 改编' : '历史项目'}</span>
        {['storyboard','reference_generation'].includes(activeStage || '') && <div className="yc-workspace-subtabs" role="group" aria-label="分镜制作步骤">
          <button aria-pressed={activeStage === 'storyboard'} onClick={() => handleStageClick('storyboard')}>1. 分镜文本</button>
          <button aria-pressed={activeStage === 'reference_generation'} onClick={() => handleStageClick('reference_generation')}>2. 参考图</button>
        </div>}
      </div>}
      <main className="flex-1 min-h-0 min-w-0 overflow-hidden">
        {requestedSession && loadError ? <div role="alert" className="yc-project-empty"><p>{loadError}</p><button className="yc-secondary-button" onClick={() => setLoadRetry(v => v + 1)}>重试</button><Link href="/projects">返回项目中心</Link></div> : loadingProject ? <div role="status" className="yc-project-empty">正在打开项目…</div> :
        showHome ? (
          <HomePage
            onStartProject={handleStartProject}
            onResumeProject={handleResumeProject}
            onDeleteSession={handleDeleteSession}
            history={history}
          />
        ) : (
          stageContent
        )}
      </main>
    </div>
  );
}
