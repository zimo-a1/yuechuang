import type {IPItem} from '@/lib/ipApi';
import StatusTag from './ui/StatusTag';

export const IP_CONTENT_STATES = {
  ready: {label: '可改编', description: '正文与使用依据齐备，可以创建改编项目。', tone: 'done'},
  missing: {label: '待导入正文', description: '先导入本地 TXT / DOCX 正文，再开始改编。', tone: 'review'},
  restricted: {label: '资料待完善', description: '请补充正文范围与使用依据，再创建改编项目。', tone: 'draft'},
} as const;

export default function IPAvailability({item}: {item: IPItem}) {
  const status = IP_CONTENT_STATES[item.availability];
  return <StatusTag label={status.label} tone={status.tone} description={item.restriction || status.description}/>;
}
