const KNOWN_NAMES: Record<string, string> = {
  'deepseek-flash': 'DeepSeek V4.1 Flash', 'deepseek4.1flash': 'DeepSeek V4.1 Flash',
  'deepseek-chat': 'DeepSeek Chat', 'deepseek-reasoner': 'DeepSeek Reasoner',
  'gpt-image-2': 'GPT Image 2', 'nano_banana': 'Nano Banana',
  'grok-video-1.5': 'Grok Video 1.5', 'minimax-h3-max-turbo': 'MiniMax H3 Max Turbo',
};

/** Presentation only: never change a saved model id or silently select a fallback. */
export function modelDisplayName(value: string, label?: string): string {
  if (!value) return label || '未配置模型';
  const raw = value.startsWith('svc/') ? value.split('/').slice(2).join('/') : value;
  const short = raw.split('/').at(-1) || raw;
  const known = KNOWN_NAMES[short.toLowerCase()];
  if (known) return known;
  if (label && !label.includes('svc/') && label !== value && label !== raw) return label;
  return short.replace(/[_-]+/g, ' ').replace(/\b(gpt|vlm|llm)\b/gi, word => word.toUpperCase()) || '自定义模型';
}
