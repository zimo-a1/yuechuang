import {imageReviewView, stageWarnings} from '@/lib/imageReview';

export default function ImageReviewNote({review, selected}: {review?: unknown; selected?: string}) {
  const view = imageReviewView(review, selected);
  if (!view) return null;
  return <p className={`yc-review yc-review-${view.status}`} role="status">{view.headline}{view.detail ? ` · ${view.detail}` : ''}</p>;
}

export function StageWarningList({warnings}: {warnings: unknown}) {
  const items = stageWarnings(warnings);
  if (!items.length) return null;
  return <div className="yc-warning-list" role="status">{items.map(item => <p key={`${item.code}-${item.message}`} className="yc-review-unavailable">{item.message}</p>)}</div>;
}
