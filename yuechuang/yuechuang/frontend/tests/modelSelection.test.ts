import assert from 'node:assert/strict';
import test from 'node:test';
import {compatibleVideoFormat} from '../lib/modelSelection.ts';
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
