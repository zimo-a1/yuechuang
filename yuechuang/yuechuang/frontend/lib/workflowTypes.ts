import type { VideoGenerationMode } from '@/config/models';

export interface AssetItem {
  id: string;
  name: string;
  description: string;
  selected: string;
  versions: string[];
  generation_status?: 'pending' | 'running' | 'done' | 'failed';
  status?: 'pending' | 'running' | 'done' | 'failed';
  index?: number;
  episode?: number;
  episode_title?: string;
  title?: string;
  duration?: number;
}
export interface WorkflowShot {
  shot_number?: number;
  shot_type?: string;
  duration?: number;
  content?: string;
  plot?: string;
  visual_prompt?: string;
}
export interface ShotBeat {
  unit_id: string;
  kind: 'action' | 'dialogue';
  start: number;
  end: number;
  text: string;
  speaker: string;
  listeners: string[];
}
export interface ShotControl { version: 1; beats: ShotBeat[] }
export interface WorkflowSegment {
  segment_id: string;
  reference_asset_ids?: string[] | null;
  video_prompt?: string;
  visual_prompt?: string;
  segment_number?: number;
  location?: string;
  characters?: string[];
  shot_control?: ShotControl | null;
  shots: WorkflowShot[];
  total_duration?: number;
}
export interface WorkflowEpisode {
  episode_number?: number;
  episode?: number;
  act_number?: number;
  act_title?: string;
  title?: string;
  content?: string;
  segments?: WorkflowSegment[];
}
export interface LoglineData {
  logline: string;
  who: string;
  goal: string;
  conflict: string;
  twist: string;
  theme: string;
}
export interface ScriptCharacter {
  name: string;
  character_id?: string;
  description: string;
  personality: string[];
  motivation?: string;
  arc_description?: string;
  role: string;
  age?: string;
  species?: string;
  occupation?: string;
}
export interface ScriptSetting { name: string; description: string }
export interface ScriptScene {
  scene_number: number;
  act?: number;
  location: string;
  characters: (string | { name: string })[];
  plot: string;
}
export interface ActCompleteData {
  act: number;
  act_name: string;
  characters: ScriptCharacter[];
  settings: ScriptSetting[];
  scenes: ScriptScene[];
}
export interface ScriptEpisode extends WorkflowEpisode { content: string }
export interface ScriptData {
  title?: string;
  logline?: string;
  genre?: string[];
  characters?: ScriptCharacter[];
  settings?: ScriptSetting[];
  scenes?: ScriptScene[];
  episodes?: ScriptEpisode[];
  overall_style?: string;
  mood?: string;
  session_id?: string;
  phase?: string;
  beat_sheet?: string;
  completed_acts?: ActCompleteData[];
  selected_logline?: LoglineData;
  logline_options?: LoglineData[];
  logline_summary?: LoglineData;
  logline_data?: LoglineData;
  new_characters?: ScriptCharacter[];
  new_settings?: ScriptSetting[];
  new_episodes?: ScriptEpisode[];
}

/** Shared artifact envelope. Each stage narrows its own content contract. */
export interface WorkflowArtifact {
  source_map?: {manually_edited:boolean;adaptation_episode:number};
  session_id?: string;
  title?: string;
  phase?: string;
  characters?: AssetItem[];
  settings?: AssetItem[];
  props?: AssetItem[];
  scenes?: AssetItem[];
  clips?: AssetItem[];
  video_clips?: Record<string, string>;
  images?: AssetItem[];
  episodes?: WorkflowEpisode[];
  shots?: WorkflowShot[];
  beat_sheet?: string;
  completed_acts?: ActCompleteData[];
  selected_logline?: LoglineData;
  scene_images?: Record<string, string>;
  export_history?: {episode:number;path:string;name?:string}[];
  clip_ids?: string[];
  final_video?: string;
  final_videos?: { episode: number; path: string; name?: string }[];
  validation_warnings?: {code: string; message: string}[];
  payload?: WorkflowArtifact;
  user_modified?: boolean;
}
export interface ProjectMeta {
  ip_binding?: import('./ipApi').IPBinding;
  project_container?: boolean;
  parent_project_id?: string;
  project_title?: string;
  episode_number?: number;
  source_changed?: boolean;
  title?: string;
  source_type?: 'original' | 'ip' | 'legacy';
  project_revision?: number;
  created_at?: string;
  target_duration?: number;
  idea?: string;
  user_textbox_input?: string;
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
  video_generation_mode?: VideoGenerationMode;
  scene_number?: number;
  enable_concurrency?: boolean;
  web_search?: boolean;
  expand_idea?: boolean;
  episodes?: number;
  pilot_mode?: boolean;
}
export interface SessionSummary {
  novel_task?: {id:string;status:string;action:string;step:string;requests:number;max_requests:number};
  id: string;
  title?: string;
  idea?: string;
  style?: string;
  date?: number;
  status: Record<string, string>;
  current_stage?: string;
  source_type?: 'original' | 'ip' | 'legacy';
  confirmed_stages?: string[];
  meta?: ProjectMeta;
}
export function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}
