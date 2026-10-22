import {test} from 'node:test';
import assert from 'node:assert/strict';
import {stepComplete,stageForStep,stepForStage} from '../lib/studio.ts';
test('five user steps retain three independent video confirmation gates',()=>{
 assert.equal(stepForStage('reference_generation'),'videos');
 assert.equal(stageForStep('videos',[]),'storyboard');
 assert.equal(stageForStep('videos',['storyboard']),'reference_generation');
 assert.equal(stageForStep('videos',['storyboard','reference_generation']),'video_generation');
 assert.equal(stepComplete('videos',{},['storyboard','video_generation']),false);
 assert.equal(stepComplete('videos',{},['storyboard','reference_generation','video_generation']),true);
});
test('source edits invalidate rewrite completion',()=>{
 assert.equal(stepComplete('source',{idea:'source'},[]),true);
 assert.equal(stepComplete('source',{},[]),false);
 assert.equal(stepComplete('rewrite',{source_changed:true},['script_generation']),false);
 assert.equal(stepComplete('rewrite',{},['script_generation']),true);
});

import {applySegmentDuration,boundAssets,exportOrder,videoItemStatus} from '../lib/studioVideo.ts';
import type {AssetItem} from '../lib/workflowTypes.ts';
const person:AssetItem={id:'c1',name:'甲',description:'',selected:'person.png',versions:['person.png']};
const prop:AssetItem={id:'p1',name:'表',description:'',selected:'prop.png',versions:['prop.png']};
test('explicit reference selection can exclude auto matched character and persist an empty selection',()=>{
 const segment={segment_id:'s',characters:['甲'],shots:[]};const assets={characters:[person],props:[prop]};
 assert.deepEqual(boundAssets(segment,assets).map(a=>a.id),['c1']);
 assert.deepEqual(boundAssets({...segment,reference_asset_ids:['p1']},assets).map(a=>a.id),['p1']);
 assert.deepEqual(boundAssets({...segment,reference_asset_ids:[]},assets),[]);
});
test('export restores saved order while retaining newly available clips and excluding deleted ones',()=>{
 assert.deepEqual(exportOrder([person,prop],['gone','p1']),['p1','c1']);
});
test('segment duration edits keep a positive whole-second total and matching shot lengths',()=>{
 const segment={segment_id:'s',total_duration:12,shots:[{duration:6,content:'a'},{duration:6,content:'b'}]};
 const longer=applySegmentDuration(segment,15);
 assert.equal(longer.total_duration,15);
 assert.deepEqual(longer.shots?.map(shot=>shot.duration),[6,9]);
 const shorter=applySegmentDuration(segment,5);
 assert.equal(shorter.total_duration,5);
 assert.deepEqual(shorter.shots?.map(shot=>shot.duration),[4,1]);
 assert.equal(applySegmentDuration(segment,0).total_duration,2);
 assert.equal(applySegmentDuration(segment,900).total_duration,600);
 assert.equal(applySegmentDuration(segment,3,{min:5,max:15}).total_duration,5);
 assert.equal(applySegmentDuration(segment,20,{min:5,max:15}).total_duration,15);
 const timed=applySegmentDuration({...segment,shot_control:{version:1,beats:[{unit_id:'U001',kind:'dialogue',start:0,end:12,text:'一句',speaker:'甲',listeners:[]}]}},8);
 assert.equal(timed.shot_control?.beats[0].end,8);
});
test('failed regeneration remains retryable even when a prior usable version exists',()=>{
 assert.equal(videoItemStatus({...person,status:'failed'}),'failed');
 assert.equal(videoItemStatus({...person,status:'running'}),'running');
 assert.equal(videoItemStatus({...person,status:'done',generation_status:'failed'}),'failed');
 assert.equal(videoItemStatus(person),'done');assert.equal(videoItemStatus(),'pending');
});
