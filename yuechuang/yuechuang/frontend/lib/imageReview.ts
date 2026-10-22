export const REVIEW_UNAVAILABLE_TEXT = '图片已生成，但自动审核未完成，请人工检查后再继续';
const REVIEW_STATUS = new Set(['passed', 'failed', 'unavailable', 'skipped']);

export interface ImageReviewView {
  status: 'passed' | 'failed' | 'unavailable' | 'skipped';
  headline: string;
  detail: string;
}

/** Show a review only for the image it belongs to. Missing or unknown records stay hidden. */
export function imageReviewView(review: unknown, selected?: string): ImageReviewView | null {
  if (!review || typeof review !== 'object') return null;
  const record = review as {status?: unknown; score?: unknown; reason?: unknown; image?: unknown};
  if (typeof record.status !== 'string' || !REVIEW_STATUS.has(record.status)) return null;
  const image = typeof record.image === 'string' ? record.image : '';
  if (image && image !== selected) return null;
  const score = typeof record.score === 'number' && Number.isFinite(record.score) ? record.score : null;
  const reason = typeof record.reason === 'string' ? record.reason.trim() : '';
  if (record.status === 'unavailable') return {status: 'unavailable', headline: REVIEW_UNAVAILABLE_TEXT, detail: ''};
  if (record.status === 'skipped') return {status: 'skipped', headline: '受控试跑未做自动审核', detail: ''};
  if (record.status === 'passed') return {status: 'passed', headline: score == null ? '审核通过' : `审核通过 ${score} 分`, detail: reason && reason !== '审核通过' ? reason : ''};
  return {status: 'failed', headline: score == null ? '审核未通过' : `审核未通过 ${score} 分`, detail: reason};
}

export function stageWarnings(value: unknown): {code: string; message: string}[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap(item => {
    if (!item || typeof item !== 'object') return [];
    const code = (item as {code?: unknown}).code;
    const message = (item as {message?: unknown}).message;
    if (typeof code !== 'string' || typeof message !== 'string' || !message.trim()) return [];
    return [{code, message}];
  });
}

function isContinuationError(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false;
  const record = value as {code?: unknown; skipped?: unknown};
  return record.code === 'CONTINUATION_PREVIOUS_FAILED' || record.skipped === true;
}

/** A skipped continuation is a failed card that was never submitted. Accepts the error object or the clip. */
export function continuationSkip(source: unknown): {message: string} | null {
  if (!isContinuationError(source)) {
    if (!source || typeof source !== 'object') return null;
    const record = source as {generation_error?: unknown; error?: unknown};
    if (!isContinuationError(record.generation_error) && !isContinuationError(record.error)) return null;
  }
  return {message: '上一段失败，本段未提交'};
}
