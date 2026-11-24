// Loads the operating-manual chapters (./operate/*.json) in the order set in operateMeta.js.
import { OPERATE_INTRO, OPERATE_ORDER } from './operateMeta';

const files = import.meta.glob('./operate/*.json', { eager: true, import: 'default' });
const byKey = Object.fromEntries(Object.values(files).map((c) => [c.key, c]));

export { OPERATE_INTRO };
export const OPERATE_CHAPTERS = OPERATE_ORDER.map((k) => byKey[k]).filter(Boolean);
export const chapterFor = (key) => OPERATE_CHAPTERS.find((c) => c.key === key) || null;
