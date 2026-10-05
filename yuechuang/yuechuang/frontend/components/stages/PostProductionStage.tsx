'use client';

import React, {useState} from 'react';
import { Download, Film } from 'lucide-react';
import type { StageViewProps } from './types';
import { assetUrl } from './utils';
import StageProgress from './StageProgress';
import VideoPreview from './VideoPreview';
import StageActions from './StageActions';
import {exportOrder} from '@/lib/studioVideo';

export default function PostProductionStage({ previousConfirmed, state, sessionId, onConfirm, onRegenerate, onIntervene, showConfirm, isRunning, hasPendingItems, hasNextStageStarted, artifacts, scriptArtifact }: StageViewProps) {
  // 提取最终视频列表
  const finalVideos = React.useMemo(() => state.artifact?.final_videos || [], [state.artifact]);
  
  // 兼容旧格式及其变形
  const legacyVideo = state.artifact?.final_video;
  
  // 从剧本或分镜数据中提取剧集名称映射
  const episodeTitleMap = React.useMemo(() => {
    // 优先从 scriptArtifact 获取
    const episodes = scriptArtifact?.episodes || artifacts?.storyboard?.episodes || artifacts?.script?.episodes || [];
    const map: Record<number, string> = {};
    episodes.forEach((ep) => {
      const epNum = ep.episode_number || ep.episode;
      if (epNum) {
        map[Number(epNum)] = ep.act_title || ep.title || '';
      }
    });
    return map;
  }, [artifacts, scriptArtifact]);
  
  // 确保能拿到展示数据
  const videosToDisplay = React.useMemo(() => {
    if (finalVideos && finalVideos.length > 0) return finalVideos;
    if (legacyVideo) return [{ name: '最终成片', path: legacyVideo, episode: 1 }];
    return [];
  }, [finalVideos, legacyVideo]);

  const clips=artifacts?.video_generation?.clips||[];
  const [selection,setSelection]=useState<string[]|null>(null);
  const [order,setOrder]=useState<string[]|null>(null);
  const orderedIds=exportOrder(clips,order||state.artifact?.clip_ids);
  const selected=selection||state.artifact?.clip_ids||clips.filter(c=>c.selected).map(c=>c.id);
  const move=(id:string,delta:number)=>{const next=[...orderedIds];const index=next.indexOf(id);const target=index+delta;if(target<0||target>=next.length)return;[next[index],next[target]]=[next[target],next[index]];setOrder(next);};
  const history=state.artifact?.export_history||[];
  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 min-w-0 overflow-y-auto p-4 sm:p-6">
        <h2 className="text-lg font-semibold text-gray-800 mb-1">拼接导出</h2>
        <p className="text-sm text-gray-500 mb-6">按剧集拼接视频，生成各集独立成片。下载前请检查场景衔接、对白和字幕；生成完成不代表内容已通过审核。</p>

        {/* 运行中 */}
        {state.status === 'running' && (
          <StageProgress message={state.progressMessage} fallback="正在合成视频..." progress={state.progress} color="cyan" />
        )}

        {state.error && (
          <div className="text-sm text-red-600 bg-red-50 border border-red-200 p-4 rounded-xl mb-4">{state.error}</div>
        )}

        {/* 最终视频列表 */}
        {videosToDisplay.length > 0 && (
          <div className="space-y-10 pb-10">
            {videosToDisplay.map((video, idx) => {
              const epNum = video.episode || (idx + 1);
              const scriptTitle = episodeTitleMap[epNum];
              const epTitle = scriptTitle ? `第 ${epNum} 集：${scriptTitle}` : (video.name || `第 ${epNum} 集`);
              
              return (
                <div key={idx} className="space-y-4">
                  {/* 剧集分割行 - 完全同步 S4/S5 格式 */}
                  <div className="flex flex-wrap items-center justify-between gap-3 py-2 px-1 border-b border-gray-100">
                    <div className="flex min-w-0 items-center gap-3">
                      <div className="w-1.5 h-6 bg-blue-500 rounded-full" />
                      <h3 className="min-w-0 text-base font-bold text-gray-800">{epTitle}</h3>
                    </div>
                  </div>
                  
                  <div className="bg-black rounded-xl overflow-hidden shadow-lg border border-gray-800">
                    <VideoPreview
                      src={assetUrl(video.path)} 
                      controls 
                      className="w-full max-h-[60vh] object-contain" 
                    />
                  </div>
                  
                  <div className="flex items-center justify-end">
                    <a
                      href={`/api/project/${sessionId}/export/${epNum}`}
                      download={`${epTitle}.mp4`}
                      className="flex items-center gap-2 px-4 py-2 bg-gray-800 text-white rounded-lg text-xs font-medium hover:bg-black transition-colors"
                    >
                      <Download className="w-3.5 h-3.5" />
                      下载本集
                    </a>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {history.length>0&&<section className="yc-export-selection"><h3>历史导出</h3><div className="yc-export-history-grid">{history.map((video,index)=><article key={video.path}><VideoPreview src={assetUrl(video.path)} controls/><a href={assetUrl(video.path)} download>第 {video.episode} 集 · 版本 {index+1} · 下载</a></article>)}</div></section>}
        {clips.length>0&&<section className="yc-export-selection"><div className="yc-export-selection-heading"><h3>选择视频片段 · {selected.filter(id=>orderedIds.includes(id)).length} / {clips.length}</h3><button disabled={isRunning} onClick={()=>setSelection(clips.filter(c=>c.selected).map(c=>c.id))}>全选可用片段</button><button disabled={isRunning} onClick={()=>setSelection([])}>清空选择</button></div>
          <p>按下方顺序拼接，同一集内可调整顺序。未选中的片段不会导出。</p>
          {orderedIds.map((id,index)=>{const clip=clips.find(c=>c.id===id)!;return <div className="yc-export-clip" key={id}><label><input type="checkbox" disabled={isRunning||!clip.selected} checked={selected.includes(id)} onChange={e=>setSelection(e.target.checked?[...selected,id]:selected.filter(item=>item!==id))}/><span>{index+1}. {clip.name||clip.title||id}{!clip.selected?' · 尚未生成':''}</span></label><div><button aria-label={`上移片段${index+1}`} disabled={isRunning||index===0} onClick={()=>move(id,-1)}>↑</button><button aria-label={`下移片段${index+1}`} disabled={isRunning||index===orderedIds.length-1} onClick={()=>move(id,1)}>↓</button>{clip.selected&&<a href={assetUrl(clip.selected)} target="_blank" rel="noreferrer">预览</a>}</div></div>;})}
          <button className="yc-primary-button" disabled={isRunning||!previousConfirmed||!selected.some(id=>orderedIds.includes(id))} onClick={()=>onIntervene({clip_ids:orderedIds.filter(id=>selected.includes(id))})}>拼接所选片段</button>
        </section>}
        {state.status === 'completed' && videosToDisplay.length === 0 && (
          <div className="flex flex-col items-center justify-center py-20 text-gray-400">
            <Film className="w-12 h-12 mb-3" />
            <div className="text-sm">未找到成片文件，请重新打开项目检查，或重新合成。</div>
          </div>
        )}

        {state.status === 'pending' && !clips.length && (
          <div className="text-center text-gray-400 text-sm py-20">{previousConfirmed ? '上一步已确认，点击下方按钮开始本阶段。' : '请先完成并确认上一阶段。'}</div>
        )}
      </div>

      {clips.length>0 ? <div className="yc-export-confirm"><span>{videosToDisplay.length?'检查成片后确认导出。':'选择片段并点击“拼接所选片段”。'}</span>{showConfirm&&<button className="yc-primary-button" onClick={onConfirm} disabled={isRunning||!videosToDisplay.length||Boolean(state.error)}>确认导出</button>}</div> : <StageActions
        status={state.status}
        onConfirm={onConfirm}
        showConfirm={showConfirm}
        onRegenerate={onRegenerate}
        stageId="post_production"
        hasPendingItems={hasPendingItems}
        hasNextStageStarted={hasNextStageStarted}
        isRunning={isRunning}
      />}
    </div>
  );
}
