import {test} from 'node:test';
import assert from 'node:assert/strict';
import {PROJECT_STAGES, confirmationFollowUp, nextAssetId, projectProgress, projectHref} from '../lib/projects.ts';

test('init completion does not make a new draft completed', () => {
  const progress = projectProgress({status: {init:'completed'}, current_stage:'init'});
  assert.equal(progress.state, 'draft'); assert.equal(progress.completed, 0);
  assert.equal(progress.stage, 'script_generation');
});
test('legacy generation results await human confirmation', () => {
  assert.equal(projectProgress({status:{script_generation:'completed'}}).state, 'review');
});
test('all six gates are necessary despite five visual workspace tabs', () => {
  const project = {status:Object.fromEntries(PROJECT_STAGES.map(s => [s,'completed'])), confirmed_stages:PROJECT_STAGES.filter(s => s !== 'reference_generation')};
  assert.equal(projectProgress(project).state, 'review');
  assert.equal(projectProgress(project).stage, 'reference_generation');
  assert.equal(projectProgress({...project, confirmed_stages:PROJECT_STAGES}).state, 'completed');
});
test('running and failed work takes precedence over existing review artifacts', () => {
  assert.equal(projectProgress({status:{script_generation:'waiting',character_design:'running'}}).stage, 'character_design');
  assert.equal(projectProgress({status:{script_generation:'completed',video_generation:'error'}}).state, 'attention');
});
test('confirm does not trigger execute and old session links open the studio', () => {
  const follow = confirmationFollowUp('storyboard', 'ip-project');
  assert.equal(follow.execute, false);
  assert.equal(follow.href, '/studio/ip-project?step=videos&stage=storyboard');
  assert.equal(confirmationFollowUp(undefined, 'ip-project').href, null);
  assert.equal(projectHref('old-session', 'init'), '/studio/old-session');
  assert.equal(projectHref('a/b', 'storyboard'), '/studio/a%2Fb?step=videos&stage=storyboard');
});
test('new structure ids keep the upload prefixes', () => {
  assert.equal(nextAssetId('char_', []), 'char_001');
  assert.equal(nextAssetId('set_', ['set_001', 'set_002']), 'set_003');
});
