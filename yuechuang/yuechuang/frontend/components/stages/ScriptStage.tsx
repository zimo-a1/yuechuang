'use client';

import React, { useState, useCallback, useRef } from 'react';
import {useUnsavedChanges, canLeaveEditor} from '@/lib/unsavedChanges';
import {errorMessage} from '@/lib/workflowTypes';
import {useEditorRecovery} from '@/lib/editorRecovery';
import { Save, X, Code, LayoutList, Users, MapPin, Film, Sparkles, BookOpen, Lightbulb, Target, User, Crosshair, RefreshCw, Palette } from 'lucide-react';
import type { StageViewProps } from './types';
import StageActions from './StageActions';
import StageProgress from './StageProgress';

/* ─── 类型 ─── */

import type { ScriptData, ScriptCharacter, ScriptSetting, ScriptScene, LoglineData, ActCompleteData } from '@/lib/workflowTypes';
import {nextAssetId} from '@/lib/projects';

/* ─── 角色色彩 ─── */
const ROLE_COLORS: Record<string, string> = {
  '主角': 'bg-amber-100 text-amber-700',
  'protagonist': 'bg-amber-100 text-amber-700',
  '配角': 'bg-sky-100 text-sky-700',
  'supporting': 'bg-sky-100 text-sky-700',
  '背景': 'bg-gray-100 text-gray-500',
  'background': 'bg-gray-100 text-gray-500',
};

/* ─── Logline 六要素展示卡 ─── */
function LoglineSummaryBar({ logline }: { logline: LoglineData }) {
  const items = [
    { icon: Lightbulb, label: 'Logline', value: logline.logline, color: 'text-amber-600' },
    { icon: User, label: '主角', value: logline.who, color: 'text-blue-600' },
    { icon: Target, label: '目标', value: logline.goal, color: 'text-green-600' },
    { icon: Crosshair, label: '障碍', value: logline.conflict, color: 'text-red-500' },
    { icon: RefreshCw, label: '反转', value: logline.twist, color: 'text-blue-600' },
    { icon: Palette, label: '主题', value: logline.theme, color: 'text-blue-600' },
  ];
  return (
    <div className="bg-gradient-to-r from-amber-50 to-orange-50 border border-amber-200 rounded-xl p-4">
      <div className="flex items-center gap-2 mb-3">
        <Lightbulb className="w-4 h-4 text-amber-500" />
        <span className="text-xs font-semibold text-amber-700">Logline 核心</span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6 gap-3">
        {items.map(({ icon: Icon, label, value, color }) => (
          <div key={label} className="min-w-0">
            <div className={`flex items-center gap-1 mb-1 ${color}`}>
              <Icon className="w-3 h-3 flex-shrink-0" />
              <span className="text-[10px] font-semibold">{label}</span>
            </div>
            <p className="text-xs text-gray-600 leading-relaxed break-words">{value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ScriptStage({ state, sessionId, onConfirm, onIntervene, onRegenerate, onSaveSelections, showConfirm, isRunning, hasPendingItems, hasNextStageStarted }: StageViewProps) {
  const data = React.useMemo(() => (state.artifact || {}) as ScriptData, [state.artifact]);

  const isLoglinePhase = data.phase === 'logline_selection' || data.phase === 'logline_confirm' || data.phase === 'mode_selection';

  const [isEditing, setIsEditing] = useState(false);
  const [editMode, setEditMode] = useState<'structured' | 'raw'>('structured');
  const [editData, setEditData] = useState<ScriptData>({});
  const [rawText, setRawText] = useState('');
  const [saveError, setSaveError] = useState('');
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const [original, setOriginal] = useState('');
  const dirty = isEditing && (editMode === 'raw' ? rawText !== original : JSON.stringify(editData, null, 2) !== original);
  useUnsavedChanges(dirty);
  const {recovery, clearRecovery} = useEditorRecovery(`yc-script-${sessionId}`, JSON.stringify({editMode, rawText, editData}), dirty);

  const [showSmartContinueDialog, setShowSmartContinueDialog] = useState(false);
  const [smartContinueEpisodes, setSmartContinueEpisodes] = useState<number>(1);
  const [smartContinueIdea, setSmartContinueIdea] = useState<string>('');

  const handleSmartContinueConfirm = useCallback(() => {
    onIntervene({
      action: 'smart_continue',
      episodes_to_add: smartContinueEpisodes,
      sequel_idea: smartContinueIdea
    });
    setShowSmartContinueDialog(false);
    setSmartContinueIdea('');
    setSmartContinueEpisodes(1);
  }, [smartContinueEpisodes, smartContinueIdea, onIntervene]);

  const hasContent = Boolean(data.title || data.characters?.length || data.scenes?.length || data.episodes?.length);

  const startEdit = useCallback(() => {
    setOriginal(JSON.stringify(data, null, 2));
    setSaveError('');
    setEditData(JSON.parse(JSON.stringify(data)));
    setRawText(JSON.stringify(data, null, 2));
    setIsEditing(true);
    setEditMode('structured');
  }, [data]);

  const switchEditMode = useCallback((mode: 'structured' | 'raw') => {
    if (mode === 'raw') {
      setRawText(JSON.stringify(editData, null, 2));
    } else {
      try {const parsed = JSON.parse(rawText); if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error(); setEditData(parsed);}
      catch {setSaveError('JSON 格式无效，请修正后再切换'); return;}
    }
    setEditMode(mode);
  }, [editData, rawText]);

  const handleSave = useCallback(async () => {
    if (savingRef.current || !onSaveSelections) return;
    setSaveError('');
    let finalData: ScriptData;
    try {
      finalData = editMode === 'raw' ? JSON.parse(rawText) : editData;
      if (!finalData || typeof finalData !== 'object' || Array.isArray(finalData)) throw new Error();
    } catch {setSaveError('JSON 格式无效，修改尚未保存'); return;}
    savingRef.current = true; setSaving(true);
    try {
      await onSaveSelections({...finalData});
      clearRecovery();
      setIsEditing(false);
    } catch (error) {setSaveError(errorMessage(error, '保存失败，编辑内容已保留，请重试'));}
    finally {savingRef.current = false; setSaving(false);}
  }, [editMode, rawText, editData, onSaveSelections, clearRecovery]);

  const cancelEdit = useCallback(() => {if (!savingRef.current && canLeaveEditor()) {setIsEditing(false); setSaveError(''); clearRecovery();}}, [clearRecovery]);

  /* ─── 编辑辅助 ─── */
  const updateField = <K extends keyof ScriptData,>(field: K, value: ScriptData[K]) => setEditData(prev => ({ ...prev, [field]: value }));

  const updateCharacter = (idx: number, patch: Partial<ScriptCharacter>) => {
    setEditData(prev => ({
      ...prev,
      characters: prev.characters?.map((c, i) => i === idx ? { ...c, ...patch } : c),
    }));
  };

  const addCharacter = () => setEditData(prev => ({...prev, characters: [...(prev.characters || []), {name: '', description: '', role: '配角', personality: [], character_id: nextAssetId('char_', (prev.characters || []).map(item => item.character_id || ''))}]}));
  const addSetting = () => setEditData(prev => ({...prev, settings: [...(prev.settings || []), {name: '', description: '', setting_id: nextAssetId('set_', (prev.settings || []).map(item => item.setting_id || ''))}]}));

  const updateSetting = (idx: number, patch: Partial<ScriptSetting>) => {
    setEditData(prev => ({
      ...prev,
      settings: prev.settings?.map((s, i) => i === idx ? { ...s, ...patch } : s),
    }));
  };

  const updateScene = (idx: number, patch: Partial<ScriptScene>) => {
    setEditData(prev => ({
      ...prev,
      scenes: prev.scenes?.map((s, i) => i === idx ? { ...s, ...patch } : s),
    }));
  };

  return (
    <div className="flex h-full min-w-0 flex-col">
      <div className="flex-1 min-w-0 overflow-y-auto p-4 sm:p-6">

        {/* 标题栏 */}
        <div className="flex flex-wrap items-center justify-between gap-3 mb-1">
          <h2 className="text-lg font-semibold text-gray-800">剧本生成</h2>
          {isEditing && (
            <div className="flex gap-1 bg-gray-100 rounded-lg p-1 text-xs">
              <button onClick={() => switchEditMode('structured')}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md transition-colors ${editMode === 'structured' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}>
                <LayoutList className="w-3.5 h-3.5" />结构编辑
              </button>
              <button onClick={() => switchEditMode('raw')}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md transition-colors ${editMode === 'raw' ? 'bg-white text-blue-600 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}>
                <Code className="w-3.5 h-3.5" />JSON编辑
              </button>
            </div>
          )}
        </div>
        <p className="text-sm text-gray-500 mb-6">检查分集正文、角色和场景，保存后再进入素材制作</p>

        {/* 运行中 - 进度条 & 已选 Logline & 增量生成结果 */}
        {state.status === 'running' && (
          <>
            {data.selected_logline && (
              <div className="mb-4">
                <LoglineSummaryBar logline={data.selected_logline as LoglineData} />
              </div>
            )}

            {/* 节拍表展示 */}
            {data.beat_sheet && (
              <div className="mb-4">
                <div className="flex items-center gap-2 mb-2">
                  <BookOpen className="w-4 h-4 text-orange-500" />
                  <h3 className="text-sm font-semibold text-gray-700">节拍表 (Beat Sheet)</h3>
                </div>
                <div className="bg-white border border-gray-200 rounded-xl p-4">
                  <pre className="text-sm text-gray-600 leading-relaxed whitespace-pre-wrap font-sans">{data.beat_sheet as string}</pre>
                </div>
              </div>
            )}

            {/* 逐幕完成的分场结果 */}
            {data.completed_acts && (data.completed_acts as ActCompleteData[]).length > 0 && (
              <div className="mb-4 space-y-4">
                {(data.completed_acts as ActCompleteData[]).map((actData) => (
                  <div key={actData.act}>
                    {/* 幕分隔线 */}
                    <div className="flex items-center gap-3 mb-3">
                      <div className="flex-1 h-px bg-gradient-to-r from-blue-200 to-transparent" />
                      <span className="px-3 py-1 bg-blue-50 text-blue-600 text-xs font-semibold rounded-full whitespace-nowrap">
                        第{actData.act}幕 — {actData.act_name}
                      </span>
                      <div className="flex-1 h-px bg-gradient-to-l from-blue-200 to-transparent" />
                    </div>

                    {/* 本幕场景 */}
                    <div className="space-y-2">
                      {actData.scenes.map((sc, i) => (
                        <div key={i} className="bg-white border border-gray-200 rounded-xl p-4 hover:shadow-sm transition-shadow">
                          <div className="flex items-center gap-3 mb-2">
                            <span className="flex items-center justify-center w-7 h-7 rounded-full bg-blue-100 text-blue-700 text-xs font-bold flex-shrink-0">{sc.scene_number}</span>
                            <span className="px-2 py-0.5 bg-green-50 text-green-600 text-xs rounded-full">{sc.location}</span>
                            <div className="flex flex-wrap gap-1">
                              {(sc.characters || []).map((c, ci) => (
                                <span key={ci} className="px-2 py-0.5 bg-blue-50 text-blue-600 text-xs rounded-full">{typeof c === 'string' ? c : c.name}</span>
                              ))}
                            </div>
                          </div>
                          <p className="text-sm text-gray-600 leading-relaxed pl-10">{sc.plot}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}

            <StageProgress message={state.progressMessage} fallback="正在生成剧本..." progress={state.progress} color="blue" />
          </>
        )}

        {/* 错误 */}
        {state.error && (
          <div className="text-sm text-red-600 bg-red-50 border border-red-200 p-4 rounded-xl mb-4">{state.error}</div>
        )}

        {/* ===== Logline 选择/确认阶段 ===== */}
        {isLoglinePhase && state.status === 'waiting' && (
          <div className="space-y-4">
            {/* 3 个 Logline 选项卡 */}
            {data.phase === 'logline_selection' && data.logline_options && (
              <>
                <div className="flex items-center gap-2 mb-2">
                  <Lightbulb className="w-4 h-4 text-amber-500" />
                  <h3 className="text-sm font-semibold text-gray-700">选择一个 Logline 方案</h3>
                  <span className="text-xs text-gray-400">点击卡片以选择</span>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                  {(data.logline_options as LoglineData[]).map((opt, i) => (
                    <button
                      key={i}
                      onClick={() => onIntervene({ selected_logline: opt })}
                      className="text-left p-4 bg-white border border-gray-200 rounded-xl hover:border-blue-400 hover:shadow-md transition-all group cursor-pointer"
                    >
                      <p className="text-sm font-medium text-gray-800 group-hover:text-blue-600 mb-3 leading-relaxed">
                        {opt.logline}
                      </p>
                      <div className="space-y-1.5 text-xs text-gray-500">
                        <p><span className="text-gray-600 font-medium">主角:</span> {opt.who}</p>
                        <p><span className="text-gray-600 font-medium">目标:</span> {opt.goal}</p>
                        <p><span className="text-gray-600 font-medium">障碍:</span> {opt.conflict}</p>
                        <p><span className="text-gray-600 font-medium">反转:</span> {opt.twist}</p>
                        <p><span className="text-gray-600 font-medium">主题:</span> {opt.theme}</p>
                      </div>
                    </button>
                  ))}
                </div>
              </>
            )}

            {/* 单个 Logline 确认 */}
            {data.phase === 'logline_confirm' && data.logline_summary && (
              <>
                <div className="flex items-center gap-2 mb-2">
                  <Lightbulb className="w-4 h-4 text-amber-500" />
                  <h3 className="text-sm font-semibold text-gray-700">Logline 提取结果</h3>
                </div>
                <LoglineSummaryBar logline={data.logline_summary as LoglineData} />
                <div className="flex justify-center pt-2">
                  <button
                    onClick={() => onIntervene({ selected_logline: data.logline_summary })}
                    className="flex items-center gap-2 px-5 py-2.5 bg-blue-500 text-white rounded-lg text-sm font-medium hover:bg-blue-600 transition-colors"
                  >
                    <Sparkles className="w-4 h-4" />
                    确认 Logline 并生成剧本
                  </button>
                </div>
              </>
            )}

            {/* 创作模式选择 */}
            {data.phase === 'mode_selection' && (
              <>
                {data.selected_logline && (
                  <div className="mb-4">
                    <LoglineSummaryBar logline={data.selected_logline as LoglineData} />
                  </div>
                )}
                <div className="flex items-center gap-2 mb-3">
                  <Film className="w-4 h-4 text-blue-500" />
                  <h3 className="text-sm font-semibold text-gray-700">选择创作模式</h3>
                </div>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                  <button
                    onClick={() => onIntervene({ selected_mode: 'movie' })}
                    className="text-left p-5 bg-white border-2 border-gray-200 rounded-xl hover:border-blue-400 hover:shadow-lg transition-all group cursor-pointer"
                  >
                    <div className="flex items-center gap-2 mb-3">
                      <span className="flex items-center justify-center w-10 h-10 rounded-xl bg-blue-100 text-blue-600 text-lg">🎬</span>
                      <span className="text-base font-semibold text-gray-800 group-hover:text-blue-600">电影模式</span>
                    </div>
                    <p className="text-sm text-gray-600 leading-relaxed mb-3">
                      按照四幕结构生成完整情节，叙事连贯丰富，有完整的起承转合。
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      <span className="px-2 py-0.5 bg-blue-50 text-blue-500 text-xs rounded-full">四幕结构</span>
                      <span className="px-2 py-0.5 bg-blue-50 text-blue-500 text-xs rounded-full">叙事完整</span>
                      <span className="px-2 py-0.5 bg-blue-50 text-blue-500 text-xs rounded-full">情节丰富</span>
                    </div>
                  </button>
                  <button
                    onClick={() => onIntervene({ selected_mode: 'micro' })}
                    className="text-left p-5 bg-white border-2 border-gray-200 rounded-xl hover:border-blue-400 hover:shadow-lg transition-all group cursor-pointer"
                  >
                    <div className="flex items-center gap-2 mb-3">
                      <span className="flex items-center justify-center w-10 h-10 rounded-xl bg-blue-100 text-blue-600 text-lg">🎞️</span>
                      <span className="text-base font-semibold text-gray-800 group-hover:text-blue-600">微电影模式</span>
                    </div>
                    <p className="text-sm text-gray-600 leading-relaxed mb-3">
                      所有内容生成在一幕内，叙事节奏快，情节紧凑，适合短片创作。
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      <span className="px-2 py-0.5 bg-blue-50 text-blue-500 text-xs rounded-full">单幕结构</span>
                      <span className="px-2 py-0.5 bg-blue-50 text-blue-500 text-xs rounded-full">节奏紧凑</span>
                      <span className="px-2 py-0.5 bg-blue-50 text-blue-500 text-xs rounded-full">3-6场景</span>
                    </div>
                  </button>
                </div>
              </>
            )}
          </div>
        )}

        {/* ===== 查看模式 ===== */}
        {hasContent && !isEditing && (
          <div className="space-y-8">

            {/* Logline 六要素摘要 */}
            {data.logline_data && (
              <LoglineSummaryBar logline={data.logline_data as LoglineData} />
            )}

            {/* 标题 / Logline / 标签 */}
            {data.title && (
              <section className="bg-white border border-gray-200 rounded-xl p-5">
                <h3 className="text-xl font-bold text-gray-800 mb-2">{data.title}</h3>
                {data.logline && <p className="text-sm text-gray-500 mb-3">{data.logline}</p>}
                <div className="flex flex-wrap gap-1.5">
                  {data.genre?.map((g, i) => (
                    <span key={i} className="px-2.5 py-0.5 bg-blue-50 text-blue-600 text-xs rounded-full font-medium">{g}</span>
                  ))}
                  {data.mood && <span className="px-2.5 py-0.5 bg-blue-50 text-blue-600 text-xs rounded-full font-medium">{data.mood}</span>}
                  {data.overall_style && <span className="px-2.5 py-0.5 bg-blue-50 text-blue-600 text-xs rounded-full font-medium">{data.overall_style}</span>}
                </div>
              </section>
            )}

            {/* 故事梗概 */}
            {data.logline && (
              <section>
                <div className="flex items-center gap-2 mb-3">
                  <BookOpen className="w-4 h-4 text-orange-500" />
                  <h3 className="text-sm font-semibold text-gray-700">故事梗概</h3>
                </div>
                <div className="bg-white border border-gray-200 rounded-xl p-5">
                  <p className="text-sm text-gray-600 leading-relaxed">{data.logline}</p>
                </div>
              </section>
            )}

            {/* 角色 */}
            {data.characters && data.characters.length > 0 && (
              <section>
                <div className="flex items-center gap-2 mb-3">
                  <Users className="w-4 h-4 text-blue-500" />
                  <h3 className="text-sm font-semibold text-gray-700">角色 ({data.characters.length})</h3>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {data.characters.map((c, i) => (
                    <div key={i} className="bg-white border border-gray-200 rounded-xl p-4 hover:shadow-sm transition-shadow">
                      <div className="flex items-center gap-2 mb-2">
                        <span className="font-medium text-gray-800">{c.name}</span>
                        <span className={`px-1.5 py-0.5 text-[10px] rounded ${ROLE_COLORS[c.role] || 'bg-gray-100 text-gray-500'}`}>{c.role}</span>
                        {c.species && c.species !== '人类' && c.species !== 'human' && (
                          <span className="px-1.5 py-0.5 bg-emerald-50 text-emerald-600 text-[10px] rounded">{c.species}</span>
                        )}
                      </div>
                      <p className="text-sm text-gray-500 leading-relaxed mb-2">{c.description}</p>
                      {c.personality && (
                        <div className="flex flex-wrap gap-1 mb-2">
                          {(Array.isArray(c.personality) ? c.personality : String(c.personality).split(/[,，]/)).map((p: string, pi: number) => (
                            <span key={pi} className="px-1.5 py-0.5 bg-blue-50 text-blue-500 text-[10px] rounded">{p.trim()}</span>
                          ))}
                        </div>
                      )}
                      {c.motivation && <p className="text-xs text-gray-400"><span className="text-gray-500 font-medium">动机:</span> {c.motivation}</p>}
                      {c.arc_description && <p className="text-xs text-gray-400"><span className="text-gray-500 font-medium">成长:</span> {c.arc_description}</p>}
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* 场景设置 */}
            {data.settings && data.settings.length > 0 && (
              <section>
                <div className="flex items-center gap-2 mb-3">
                  <MapPin className="w-4 h-4 text-green-500" />
                  <h3 className="text-sm font-semibold text-gray-700">场景 ({data.settings.length})</h3>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {data.settings.map((s, i) => (
                    <div key={i} className="bg-white border border-gray-200 rounded-xl p-4 hover:shadow-sm transition-shadow">
                      <div className="font-medium text-gray-800 mb-1">{s.name}</div>
                      <p className="text-sm text-gray-500 leading-relaxed">{s.description}</p>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* 故事线 */}
            {data.episodes && data.episodes.length > 0 ? (
          <section className="bg-gray-50 p-4 rounded-xl border border-gray-100">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Film className="w-5 h-5 text-blue-500" />
                <h3 className="text-sm font-bold text-gray-800">分集剧本</h3>
              </div>
            </div>
            <div className="space-y-6">
              {data.episodes.map((ep, i) => (
                <div key={i} className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm hover:shadow-md transition-shadow">
                  <div className="bg-gradient-to-r from-blue-50 to-white px-4 py-3 border-b border-blue-100 flex items-center justify-between">
                    <h4 className="font-bold text-blue-800">第 {ep.act_number || (i + 1)} 集：{ep.act_title}</h4>
                    <span className="text-[10px] font-bold text-blue-300 bg-blue-50 px-2 py-0.5 rounded uppercase tracking-wider">Episode {ep.act_number || (i + 1)}</span>
                  </div>
                  <div className="p-5 text-gray-700 whitespace-pre-wrap leading-relaxed text-[15px]">
                    {ep.content}
                  </div>
                </div>
              ))}
            </div>
          </section>
        ) : data.scenes && data.scenes.length > 0 && (
              <section>
                <div className="flex items-center gap-2 mb-3">
                  <Film className="w-4 h-4 text-blue-500" />
                  <h3 className="text-sm font-semibold text-gray-700">故事线 ({data.scenes.length} 场)</h3>
                </div>
                <div className="space-y-3">
                  {data.scenes.map((sc, i) => {
                    // 幕分隔线：当场景有 act 字段，且是第一场或与上一场不同幕时显示
                    const showActSep = sc.act != null && (i === 0 || data.scenes![i - 1].act !== sc.act);
                    const actNames: Record<number, string> = { 1: '激励事件', 2: '进入新世界', 3: '灵魂黑夜', 4: '高潮决战' };
                    return (
                      <React.Fragment key={i}>
                        {showActSep && (
                          <div className="flex items-center gap-3 pt-2">
                            <div className="flex-1 h-px bg-gradient-to-r from-blue-200 to-transparent" />
                            <span className="px-3 py-1 bg-blue-50 text-blue-600 text-xs font-semibold rounded-full whitespace-nowrap">
                              第{sc.act}幕 — {actNames[sc.act!] || ''}
                            </span>
                            <div className="flex-1 h-px bg-gradient-to-l from-blue-200 to-transparent" />
                          </div>
                        )}
                        <div className="bg-white border border-gray-200 rounded-xl p-4 hover:shadow-sm transition-shadow">
                          <div className="flex items-center gap-3 mb-2">
                            <span className="flex items-center justify-center w-7 h-7 rounded-full bg-blue-100 text-blue-700 text-xs font-bold flex-shrink-0">{sc.scene_number}</span>
                            <span className="px-2 py-0.5 bg-green-50 text-green-600 text-xs rounded-full">{sc.location}</span>
                            <div className="flex flex-wrap gap-1">
                              {(sc.characters || []).map((c, ci) => (
                                <span key={ci} className="px-2 py-0.5 bg-blue-50 text-blue-600 text-xs rounded-full">{typeof c === 'string' ? c : c.name}</span>
                              ))}
                            </div>
                          </div>
                          <p className="text-sm text-gray-600 leading-relaxed pl-10">{sc.plot}</p>
                        </div>
                      </React.Fragment>
                    );
                  })}
                </div>
              </section>
            )}

            {/* ===== 智能续写 UI ===== */}
            {!data.new_episodes || data.new_episodes.length === 0 ? (
              data.episodes && data.episodes.length > 0 && state.status !== 'running' && (
                <div className="mt-8 flex justify-center">
                  <button
                    onClick={() => setShowSmartContinueDialog(true)}
                    className="flex items-center gap-2 px-6 py-2.5 bg-gradient-to-r from-blue-500 to-blue-600 text-white rounded-full shadow-md hover:shadow-lg hover:from-blue-600 hover:to-blue-700 transition-all font-medium text-sm"
                  >
                    <Sparkles className="w-4 h-4" />
                    智能续写
                  </button>
                </div>
              )
            ) : (
              <div className="mt-8 space-y-6">
                <div className="relative flex py-5 items-center">
                  <div className="flex-grow border-t border-blue-300 border-dashed"></div>
                  <span className="flex-shrink-0 mx-4 text-blue-600 font-bold text-sm bg-blue-50 px-4 py-1 rounded-full shadow-sm">续集</span>
                  <div className="flex-grow border-t border-blue-300 border-dashed"></div>
                </div>
                
                {data.new_characters && data.new_characters.length > 0 && (
                  <div className="bg-amber-50 p-4 rounded-xl border border-amber-200 shadow-sm flex flex-col gap-3">
                    <h4 className="text-sm font-bold text-amber-800 flex items-center gap-2">
                      <Users className="w-4 h-4" /> 新增角色
                    </h4>
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                      {data.new_characters.map((c, i) => (
                        <div key={i} className="bg-white p-3 rounded-lg border border-amber-100 shadow-sm flex flex-col gap-1">
                          <div className="font-bold text-amber-900 text-sm">{c.name} <span className="text-[10px] text-amber-600 font-normal bg-amber-100 px-1.5 py-0.5 rounded ml-1">{c.role || '配角'}</span></div>
                          <p className="text-[11px] text-gray-600 line-clamp-2 md:line-clamp-none">{c.description}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                
                {data.new_settings && data.new_settings.length > 0 && (
                  <div className="bg-emerald-50 p-4 rounded-xl border border-emerald-200 shadow-sm flex flex-col gap-3">
                    <h4 className="text-sm font-bold text-emerald-800 flex items-center gap-2">
                      <MapPin className="w-4 h-4" /> 新增场景
                    </h4>
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                      {data.new_settings.map((s, i) => (
                        <div key={i} className="bg-white p-3 rounded-lg border border-emerald-100 shadow-sm flex flex-col gap-1">
                          <div className="font-bold text-emerald-900 text-sm">{s.name}</div>
                          <p className="text-[11px] text-gray-600 line-clamp-2 md:line-clamp-none">{s.description}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <div className="space-y-6">
                  {data.new_episodes.map((ep, i) => (
                    <div key={`new-${i}`} className="bg-blue-50 border-2 border-blue-300 rounded-xl overflow-hidden shadow-md">
                      <div className="bg-gradient-to-r from-blue-200 to-blue-100 px-4 py-3 flex items-center justify-between border-b border-blue-200/50">
                        <h4 className="font-bold text-blue-900">第 {ep.episode_number || (data.episodes ? data.episodes.length + i + 1 : i + 1)} 集：{ep.act_title}</h4>
                        <span className="text-[10px] font-bold text-blue-700 bg-blue-200/50 px-2 py-0.5 rounded shadow-sm tracking-wider">NEW EPISODE</span>
                      </div>
                      <div className="p-5 text-gray-800 whitespace-pre-wrap leading-relaxed text-[15px] bg-white/80">
                        {ep.content}
                      </div>
                    </div>
                  ))}
                </div>

                <div className="flex justify-end gap-3 mt-4">
                  <button
                    onClick={() => onIntervene({ action: 'delete_continue' })}
                    className="px-5 py-2 text-red-600 bg-red-50 hover:bg-red-100 rounded-lg text-sm font-medium transition-colors border border-red-200"
                  >
                    删除新剧集
                  </button>
                  <button
                    onClick={() => onIntervene({ action: 'confirm_continue' })}
                    className="flex items-center gap-2 px-5 py-2 bg-gradient-to-r from-blue-600 to-blue-700 text-white rounded-lg text-sm font-medium hover:from-blue-700 hover:to-blue-800 transition-colors shadow-sm"
                  >
                    <Save className="w-4 h-4" />
                    保存新剧集
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {isEditing && <p className="mb-4 text-xs text-gray-500 leading-6">保存不会调用模型。保存后，剧本、素材、分镜、参考图、视频和成片需重新确认；已有产物保留供复核。</p>}
        {/* ===== 结构编辑模式 ===== */}
        {isEditing && editMode === 'structured' && (
          <div className="space-y-6">

            {/* 基础信息 */}
            <section className="bg-white border border-gray-200 rounded-xl p-4 space-y-3">
              <h4 className="text-sm font-semibold text-gray-700 flex items-center gap-2"><Sparkles className="w-3.5 h-3.5 text-blue-500" />基本信息</h4>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                <label className="flex flex-col gap-1 text-xs">
                  <span className="text-gray-500 font-medium">标题</span>
                  <input type="text" value={editData.title || ''} onChange={e => updateField('title', e.target.value)}
                    className="border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:ring-2 focus:ring-blue-500/30 outline-none" />
                </label>
                <label className="flex flex-col gap-1 text-xs">
                  <span className="text-gray-500 font-medium">情绪基调</span>
                  <input type="text" value={editData.mood || ''} onChange={e => updateField('mood', e.target.value)}
                    className="border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:ring-2 focus:ring-blue-500/30 outline-none" />
                </label>
              </div>
              <label className="flex flex-col gap-1 text-xs">
                <span className="text-gray-500 font-medium">Logline (故事大纲)</span>
                <textarea value={editData.logline || ''} onChange={e => updateField('logline', e.target.value)} rows={4}
                  className="border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:ring-2 focus:ring-blue-500/30 outline-none resize-none" />
              </label>
            </section>

            {/* 角色编辑 */}
            <section>
                <div className="flex items-center gap-2 mb-3"><Users className="w-4 h-4 text-blue-500" /><h4 className="text-sm font-semibold text-gray-700">角色</h4><button type="button" className="ml-auto text-xs text-blue-600" onClick={addCharacter}>新增角色</button></div>
                <div className="space-y-3">
                  {(editData.characters || []).map((c, i) => (
                    <div key={i} className="bg-white border border-gray-200 rounded-xl p-4 space-y-2">
                      <div className="flex gap-3">
                        <label className="flex flex-col gap-1 text-xs flex-1"><span className="text-gray-500 font-medium">名字</span>
                          <input type="text" value={c.name} onChange={e => updateCharacter(i, { name: e.target.value })}
                            className="border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:ring-2 focus:ring-blue-500/30 outline-none" />
                          {c.character_id && <small className="text-gray-400">{c.character_id}</small>}</label>
                        <label className="flex flex-col gap-1 text-xs w-24"><span className="text-gray-500 font-medium">角色</span>
                          <select value={c.role} onChange={e => updateCharacter(i, { role: e.target.value })}
                            className="border border-gray-200 rounded-lg px-2 py-2 text-sm text-gray-700 outline-none">
                            <option value="主角">主角</option><option value="配角">配角</option><option value="背景">背景</option>
                          </select></label>
                      </div>
                      <label className="flex flex-col gap-1 text-xs"><span className="text-gray-500 font-medium">外貌描述</span>
                        <textarea value={c.description} onChange={e => updateCharacter(i, { description: e.target.value })} rows={2}
                          className="border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:ring-2 focus:ring-blue-500/30 outline-none resize-none" /></label>
                      <label className="flex flex-col gap-1 text-xs"><span className="text-gray-500 font-medium">动机</span>
                        <input type="text" value={c.motivation || ''} onChange={e => updateCharacter(i, { motivation: e.target.value })}
                          className="border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:ring-2 focus:ring-blue-500/30 outline-none" /></label>
                    </div>
                  ))}
                </div>
              </section>

            <section>
                <div className="flex items-center gap-2 mb-3"><MapPin className="w-4 h-4 text-green-500" /><h4 className="text-sm font-semibold text-gray-700">场景</h4><button type="button" className="ml-auto text-xs text-blue-600" onClick={addSetting}>新增场景</button></div>
                <div className="space-y-3">
                  {(editData.settings || []).map((s, i) => (
                    <div key={i} className="bg-white border border-gray-200 rounded-xl p-4">
                      <label className="block text-xs text-gray-500 font-medium mb-1.5">名称<input className="mt-1 w-full border border-gray-200 rounded-lg px-3 py-2 text-sm" value={s.name} onChange={e => updateSetting(i, {name: e.target.value})} />{s.setting_id && <small className="text-gray-400">{s.setting_id}</small>}</label>
                      <textarea value={s.description} onChange={e => updateSetting(i, { description: e.target.value })} rows={2}
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:ring-2 focus:ring-blue-500/30 outline-none resize-none" />
                    </div>
                  ))}
                </div>
              </section>

            {editData.episodes?.length ? <section className="space-y-4"><h4 className="text-sm font-semibold">分集正文</h4>
              {editData.episodes.map((episode, index) => <label key={episode.episode_number || index} className="flex flex-col gap-2 text-sm">
                <span>第 {episode.episode_number || index + 1} 集 · {episode.act_title || episode.title || '正文'}</span>
                <textarea aria-label={`第${episode.episode_number || index + 1}集正文`} rows={14} value={episode.content || ''}
                  onChange={e => updateField('episodes', editData.episodes?.map((ep, i) => i === index ? {...ep, content: e.target.value} : ep))}
                  className="w-full border border-gray-200 rounded-xl p-4 text-sm leading-7 resize-y outline-none focus:ring-2 focus:ring-blue-500/30"/>
              </label>)}
            </section> : null}
            {/* 故事线编辑 */}
            {editData.scenes && editData.scenes.length > 0 && (
              <section>
                <div className="flex items-center gap-2 mb-3"><Film className="w-4 h-4 text-blue-500" /><h4 className="text-sm font-semibold text-gray-700">故事线</h4></div>
                <div className="space-y-3">
                  {editData.scenes.map((sc, i) => (
                    <div key={i} className="bg-white border border-gray-200 rounded-xl p-4">
                      <div className="flex items-center gap-3 mb-2">
                        <span className="flex items-center justify-center w-7 h-7 rounded-full bg-blue-100 text-blue-700 text-xs font-bold flex-shrink-0">{sc.scene_number}</span>
                        <span className="text-xs text-green-600">{sc.location}</span>
                        <div className="flex flex-wrap gap-1">
                          {sc.characters.map((c, ci) => <span key={ci} className="px-2 py-0.5 bg-blue-50 text-blue-600 text-[10px] rounded-full">{typeof c === 'string' ? c : c.name}</span>)}
                        </div>
                      </div>
                      <textarea value={sc.plot} onChange={e => updateScene(i, { plot: e.target.value })} rows={3}
                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-700 focus:ring-2 focus:ring-blue-500/30 outline-none resize-none" />
                    </div>
                  ))}
                </div>
              </section>
            )}

            <div className="flex gap-2 pt-2">
              <button onClick={handleSave} disabled={saving || isRunning} className="flex items-center gap-2 px-4 py-2 bg-blue-500 text-white rounded-lg text-sm font-medium hover:bg-blue-600 transition-colors">
                <Save className="w-4 h-4" />{saving ? '保存中…' : '保存修改'}</button>
              <button onClick={cancelEdit} className="flex items-center gap-2 px-4 py-2 bg-gray-100 text-gray-600 rounded-lg text-sm hover:bg-gray-200 transition-colors">
                <X className="w-4 h-4" />取消</button>
            </div>
          </div>
        )}

        {/* ===== JSON编辑模式 ===== */}
        {isEditing && editMode === 'raw' && (
          <div className="space-y-3">
            <textarea aria-label="剧本 JSON" value={rawText} onChange={e => setRawText(e.target.value)}
              className="w-full bg-white border border-gray-200 rounded-xl p-4 text-sm text-gray-700 font-mono leading-relaxed resize-none outline-none min-h-[400px] focus:ring-2 focus:ring-blue-500/30" />
            <div className="flex gap-2">
              <button onClick={handleSave} disabled={saving || isRunning} className="flex items-center gap-2 px-4 py-2 bg-blue-500 text-white rounded-lg text-sm font-medium hover:bg-blue-600 transition-colors">
                <Save className="w-4 h-4" />{saving ? '保存中…' : '保存修改'}</button>
              <button onClick={cancelEdit} className="flex items-center gap-2 px-4 py-2 bg-gray-100 text-gray-600 rounded-lg text-sm hover:bg-gray-200 transition-colors">
                <X className="w-4 h-4" />取消</button>
            </div>
          </div>
        )}

        {/* 等待状态 */}
        {state.status === 'pending' && (
          <div className="text-center text-gray-400 text-sm py-20">等待生成...</div>
        )}
      </div>

      {recovery && !isEditing && <div className="yc-recovery-notice">有未保存的剧本修改。
        <button disabled={isRunning} onClick={() => {try {const backup = JSON.parse(recovery); setEditData(backup.editData); setRawText(backup.rawText); setEditMode(backup.editMode); setOriginal(JSON.stringify(data, null, 2)); setIsEditing(true);} catch {setSaveError('本地草稿无法读取');}}}>恢复编辑</button>
        <button onClick={clearRecovery}>放弃备份</button>
      </div>}
      {saveError && <p role="alert" className="yc-project-error">{saveError}</p>}
      {!isEditing && !isLoglinePhase && (
        <StageActions
          status={state.status}
          onConfirm={onConfirm}
          showConfirm={showConfirm}
          onEdit={startEdit}
          onRegenerate={() => {if (window.confirm('重新生成会产生文本模型费用并替换当前剧本，已有素材需重新核对。确定开始吗？')) onRegenerate();}}
          stageId="script_generation"
          hasPendingItems={hasPendingItems}
          hasNextStageStarted={hasNextStageStarted}
          isRunning={isRunning}
        />
      )}
      {isEditing && (
        <StageActions
          status={state.status}
          onConfirm={onConfirm}
          showConfirm={showConfirm}
          onSave={handleSave}
          onRegenerate={() => {if (window.confirm('重新生成会产生文本模型费用并替换当前剧本，已有素材需重新核对。确定开始吗？')) onRegenerate();}}
          stageId="script_generation"
          hasPendingItems={hasPendingItems}
          hasNextStageStarted={hasNextStageStarted}
          isRunning={isRunning}
        />
      )}

      {/* ===== 智能续写弹窗 ===== */}
      {showSmartContinueDialog && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 backdrop-blur-sm shadow-2xl transition-opacity duration-200">
          <div className="bg-white rounded-2xl w-[min(480px,calc(100vw-2rem))] p-6 shadow-2xl animate-in zoom-in-95 duration-200">
            <h3 className="text-lg font-bold text-gray-800 mb-5 flex items-center gap-2 border-b border-gray-100 pb-3">
              <Sparkles className="w-5 h-5 text-blue-500" />
              智能续写设置
            </h3>
            
            <div className="space-y-5">
              <div>
                <label className="block text-[13px] font-bold text-gray-600 mb-2 uppercase tracking-wide">一次续写剧集数</label>
                <div className="flex gap-3">
                  {[1, 2, 3].map((num) => (
                    <button
                      key={num}
                      onClick={() => setSmartContinueEpisodes(num)}
                      className={`flex-1 py-2.5 px-4 rounded-xl font-bold text-sm transition-all border outline-none ${
                        smartContinueEpisodes === num 
                          ? 'bg-blue-50 border-blue-500 text-blue-700 shadow-[0_0_0_2px_rgba(168,85,247,0.1)]'
                          : 'bg-white border-gray-200 text-gray-500 hover:bg-gray-50 hover:border-gray-300'
                      }`}
                    >
                      {num} 集
                    </button>
                  ))}
                </div>
              </div>

              <div>
                <label className="block text-[13px] font-bold text-gray-600 mb-2 flex items-center gap-2">
                  后续剧情想法主线 <span className="text-[10px] font-medium text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded">(可选)</span>
                </label>
                <textarea 
                  value={smartContinueIdea}
                  onChange={(e) => setSmartContinueIdea(e.target.value)}
                  placeholder="如果留空，AI 将自动为您生成后续的续写灵感主线。"
                  className="w-full h-32 border border-gray-200 rounded-xl p-3 text-[14px] text-gray-700 focus:outline-none focus:border-blue-400 focus:ring-2 focus:ring-blue-500/20 resize-none transition-all placeholder:text-gray-400 bg-gray-50/50 focus:bg-white"
                ></textarea>
              </div>
            </div>

            <div className="flex justify-end gap-3 mt-8">
              <button 
                onClick={() => setShowSmartContinueDialog(false)}
                className="px-5 py-2.5 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-xl text-sm font-bold transition-colors outline-none"
              >
                取消
              </button>
              <button 
                onClick={handleSmartContinueConfirm}
                className="px-6 py-2.5 bg-gradient-to-r from-blue-500 to-blue-600 text-white rounded-xl text-sm font-bold hover:from-blue-600 hover:to-blue-700 transition-all shadow-md group flex items-center justify-center gap-2 outline-none hover:shadow-lg"
              >
                <Sparkles className="w-4 h-4 opacity-70" />
                确认生成
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
