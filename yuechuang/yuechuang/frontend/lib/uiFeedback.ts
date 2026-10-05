export type ToastTone = 'success' | 'error' | 'info';
export type ToastMessage = {id: string; tone: ToastTone; message: string};
export const TOAST_EVENT = 'yuechuang:toast';
function emit(tone: ToastTone, message: string) {
  if (typeof window === 'undefined' || !message.trim()) return;
  window.dispatchEvent(new CustomEvent<ToastMessage>(TOAST_EVENT, {detail: {id: crypto.randomUUID(), tone, message}}));
}
export const toast = {
  success: (message: string) => emit('success', message),
  error: (message: string) => emit('error', message),
  info: (message: string) => emit('info', message),
};
