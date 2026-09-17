import { useSyncExternalStore } from 'react';
import english from './locales/en-US.json';

export type Locale = 'zh-CN' | 'en-US';
const key = 'draftloop.locale.v1';
const dictionary: Record<string, string> = english;
const reverse = Object.fromEntries(Object.entries(dictionary).map(([zh, en]) => [en.trim(), zh]));
let temporaryLocale: Locale | null = null;
export function currentLocale(): Locale {
  if (temporaryLocale) return temporaryLocale;
  try { return localStorage.getItem(key) === 'en-US' ? 'en-US' : 'zh-CN'; } catch { return 'zh-CN'; }
}
export function t(value: string): string {
  const normalized = value.trim();
  const translated = (currentLocale() === 'en-US' ? dictionary : reverse)[normalized];
  return translated === undefined ? value : value.replace(normalized, translated);
}
function subscribe(listener: () => void) {
  window.addEventListener('draftloop-locale', listener); window.addEventListener('storage', listener);
  return () => { window.removeEventListener('draftloop-locale', listener); window.removeEventListener('storage', listener); };
}
export function useLocale(): [Locale, (locale: Locale) => void] {
  const locale = useSyncExternalStore(subscribe, currentLocale, (): Locale => 'zh-CN');
  return [locale, (next) => { try { localStorage.setItem(key, next); temporaryLocale = null; } catch { temporaryLocale = next; } document.documentElement.lang = next; window.dispatchEvent(new Event('draftloop-locale')); }];
}
