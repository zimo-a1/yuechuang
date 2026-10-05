import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readStoryboard, storyboardIssues, nextBeat, speechSeconds, type StoryboardEpisode } from '../lib/storyboard.ts';

const sample = (): StoryboardEpisode[] => [{episode_number: 1, episode_title: '对白测试', segments: [{
  segment_id: 'one', segment_number: 1, location: '室内', characters: ['青年', '同伴'], total_duration: 6,
  shots: [{shot_number: 1, shot_type: '中景', duration: 6, content: '两人面对面交谈。'}],
  shot_control: {version: 1, beats: [{unit_id: 'U001', kind: 'dialogue', start: 0.5, end: 3.5,
    speaker: '青年', listeners: ['同伴'], text: '我决定明天出发。'}]},
}]}];

test('valid API structure round-trips the dialogue timeline', () => {
  const value = sample();
  assert.deepEqual(readStoryboard(JSON.parse(JSON.stringify(value))), value);
  assert.deepEqual(storyboardIssues(value), []);
});
test('legacy segments without a timeline remain editable', () => {
  const value = sample(); delete value[0].segments[0].shot_control;
  assert.deepEqual(storyboardIssues(readStoryboard(value)), []);
});
test('malformed JSON shapes fail instead of rendering or falling back silently', () => {
  for (const value of [{}, [null], [{episode_number: 1, segments: [{}]}], [{...sample()[0], episode_title: {bad: true}}]]) assert.throws(() => readStoryboard(value));
});
test('overlapping speech is rejected', () => {
  const value = sample(), beats = value[0].segments[0].shot_control!.beats;
  beats.push({...beats[0], unit_id: 'U002', start: 3, end: 6});
  assert.ok(storyboardIssues(value).some(issue => issue.message.includes('重叠')));
});
test('unknown speaker, self listener and oversized dialogue fail', () => {
  const value = sample(), beat = value[0].segments[0].shot_control!.beats[0];
  beat.speaker = '陌生人'; beat.listeners = ['陌生人']; beat.text = '非常长的台词'.repeat(10);
  const errors = storyboardIssues(value).map(issue => issue.message).join();
  assert.match(errors, /说话人/); assert.match(errors, /同时/); assert.match(errors, /至少需要/);
});
test('NaN, out-of-range and reversed times fail before JSON serialization', () => {
  for (const [start, end] of [[NaN, 3], [0, Infinity], [-1, 3], [4, 3], [1, 8]]) {
    const value = sample(); Object.assign(value[0].segments[0].shot_control!.beats[0], {start, end});
    assert.ok(storyboardIssues(value).some(issue => issue.message.includes('起止时间')));
  }
});
test('editing shot duration cannot leave a timeline outside the clip', () => {
  const value = sample(), seg = value[0].segments[0]; seg.total_duration = 2; seg.shots[0].duration = 2;
  assert.ok(storyboardIssues(value).some(issue => issue.message.includes('超出片段')));
});
test('raw total and shot sum must agree', () => {
  const value = sample(); value[0].segments[0].total_duration = 7;
  assert.ok(storyboardIssues(value).some(issue => issue.message.includes('时长之和')));
});
test('duplicate identifiers and empty arrays are not saved', () => {
  const value = sample(); value[0].segments.push(structuredClone(value[0].segments[0]));
  assert.ok(storyboardIssues(value).some(issue => issue.message.includes('重复')));
  assert.ok(storyboardIssues([]).length);
});
test('new beats have unique ids and never copy spoken text', () => {
  const seg = sample()[0].segments[0], beat = nextBeat(seg, 'dialogue');
  assert.equal(beat.unit_id, 'U002'); assert.equal(beat.start, 4); assert.equal(beat.end, 6);
  assert.equal(beat.text, ''); assert.deepEqual(beat.listeners, ['同伴']);
});
test('speech timing counts Unicode characters consistently with backend', () => {
  assert.equal(speechSeconds('一 二\n三'), 2); assert.equal(speechSeconds('😀'.repeat(9)), 3);
});
