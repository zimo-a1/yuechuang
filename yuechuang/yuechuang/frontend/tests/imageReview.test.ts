import {test} from 'node:test';
import assert from 'node:assert/strict';
import {continuationSkip, imageReviewView, stageWarnings, REVIEW_UNAVAILABLE_TEXT} from '../lib/imageReview.ts';

test('missing and unknown reviews stay hidden', () => {
  assert.equal(imageReviewView(undefined, 'a.png'), null);
  assert.equal(imageReviewView({status: 'maybe', score: 5}, 'a.png'), null);
  assert.equal(imageReviewView({status: 'passed', image: 'old.png', score: 9}, 'new.png'), null);
});

test('review states keep score and reason only when they belong to the selected image', () => {
  assert.equal(imageReviewView({status: 'unavailable', image: 'a.png'}, 'a.png')?.headline, REVIEW_UNAVAILABLE_TEXT);
  assert.equal(imageReviewView({status: 'skipped', image: 'a.png'}, 'a.png')?.headline, '受控试跑未做自动审核');
  assert.deepEqual(imageReviewView({status: 'passed', score: 8, reason: '审核通过', image: 'a.png'}, 'a.png'), {status: 'passed', headline: '审核通过 8 分', detail: ''});
  assert.equal(imageReviewView({status: 'failed', score: 4, reason: '脸不一致', image: 'a.png'}, 'a.png')?.detail, '脸不一致');
  assert.equal(imageReviewView({status: 'passed', score: 8}, '')?.headline, '审核通过 8 分');
  assert.equal(imageReviewView({status: 'passed', score: 8, image: 'a.png'}, ''), null);
});

test('stage warnings ignore malformed entries', () => {
  assert.deepEqual(stageWarnings(undefined), []);
  assert.deepEqual(stageWarnings([{code: 'VLM_REVIEW_UNAVAILABLE', message: '图片已生成，但自动审核未完成，请人工检查后再继续'}, {code: 1}, null]), [{code: 'VLM_REVIEW_UNAVAILABLE', message: '图片已生成，但自动审核未完成，请人工检查后再继续'}]);
});

test('only a skipped continuation gets the unsent label', () => {
  assert.deepEqual(continuationSkip({code: 'CONTINUATION_PREVIOUS_FAILED', previous_segment_id: 'seg_01'}), {message: '上一段失败，本段未提交'});
  assert.deepEqual(continuationSkip({skipped: true}), {message: '上一段失败，本段未提交'});
  assert.deepEqual(continuationSkip({generation_error: {code: 'CONTINUATION_PREVIOUS_FAILED'}}), {message: '上一段失败，本段未提交'});
  assert.equal(continuationSkip({code: 'OTHER'}), null);
  assert.equal(continuationSkip(undefined), null);
});
