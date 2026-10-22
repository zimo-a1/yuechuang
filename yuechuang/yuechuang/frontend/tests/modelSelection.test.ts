import assert from 'node:assert/strict';
import test from 'node:test';
import {compatibleVideoFormat, videoConfigChangeLines} from '../lib/modelSelection.ts';
test('Grok to MiniMax switches incompatible resolution in the same update',()=>{
  assert.deepEqual(compatibleVideoFormat({video_resolution:'720P',video_ratio:'9:16'},{resolutions:['768P','480P'],ratios:['16:9','9:16']}),{video_resolution:'768P',video_ratio:'9:16'});
});
test('switching back to Grok repairs resolution and unsupported ratio',()=>{
  assert.deepEqual(compatibleVideoFormat({video_resolution:'768P',video_ratio:'21:9'},{resolutions:['720P','480P'],ratios:['16:9','9:16']}),{video_resolution:'720P',video_ratio:'16:9'});
});
test('compatible low resolution and unknown legacy capabilities are preserved',()=>{
  const current={video_resolution:'480P',video_ratio:'9:16'};
  assert.deepEqual(compatibleVideoFormat(current,{resolutions:['768P','480P'],ratios:['16:9','9:16']}),current);
  assert.deepEqual(compatibleVideoFormat(current),current);
});
test('a video mode switch lists mode, model and resolution together', () => {
  const lines = videoConfigChangeLines(
    {video_generation_mode:'reference', video_model:'grok-video-1.5', video_resolution:'720P'},
    {video_generation_mode:'start_end_frame', video_model:'MiniMax-H3-Max-Turbo', video_resolution:'768P'},
    id => id,
  );
  assert.deepEqual(lines, ['视频方式：参考图 → 首尾帧', '模型：grok-video-1.5 → MiniMax-H3-Max-Turbo', '分辨率：720P → 768P']);
});
