export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
export const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const paths = {
  'search':'<circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.5 4.5"/>',
  'check-circle':'<circle cx="12" cy="12" r="9"/><path d="m8 12 2.6 2.6L16 9"/>',
  'check':'<path d="m5 12 4 4L19 6"/>',
  'message':'<path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-1 1V12.5A8.5 8.5 0 0 1 11.5 4h1A8.5 8.5 0 0 1 21 11.5Z"/><path d="M7 10h10M7 14h6"/>',
  'filter':'<path d="M4 5h16l-6 7v7l-4 2V12Z"/>',
  'user':'<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
  'settings':'<path d="m10 3-1 3-3 1-3 3v4l3 3 3 1 1 3h4l1-3 3-1 3-3v-4l-3-3-3-1-1-3Z"/><circle cx="12" cy="12" r="3"/>',
  'arrow-right':'<path d="M4 12h15m-5-5 5 5-5 5"/>',
  'refresh':'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-2l2 3M4 16l2 3a7 7 0 0 0 12-2"/>',
  'leaf':'<path d="M20 3c0 9-1 15-9 15a7 7 0 0 1-7-7c0-8 7-8 16-8Z"/><path d="m3 21 12-12"/>',
  'sparkles':'<path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4Z"/><path d="m20 2 1 2.5L23.5 5 21 6l-1 2.5L19 6l-2.5-1L19 4.5Z"/>',
  'clock':'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  'calendar':'<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 3v4M17 3v4M3 11h18M8 15h1M15 15h1"/>',
  'flag':'<path d="M5 21V3m0 1h7l1 3h6v10h-6l-1-3H5"/>',
  'chevron':'<path d="m9 5 7 7-7 7"/>',
  'down':'<path d="m6 9 6 6 6-6"/>',
  'close':'<path d="m6 6 12 12M18 6 6 18"/>',
  'plus':'<path d="M12 4v16M4 12h16"/>',
  'download':'<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  'archive':'<path d="M4 7v14h16V7M3 3h18v4H3ZM9 11h6"/>',
  'edit':'<path d="m15 4 5 5M3 21l5-1L21 7a3.5 3.5 0 0 0-5-5L3 15Z"/>',
  'trash':'<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
  'bolt':'<path d="m13 2-9 12h7l-1 8 10-13h-7Z"/>',
  'info':'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
  'link':'<path d="m10 14 4-4M8 16l-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m2 2 2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0"/>',
  'external':'<path d="M14 3h7v7m0-7L10 14M10 3H3v18h18v-7"/>',
  'shield':'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
  'inbox':'<path d="m3 14 3-9h12l3 9v7H3Z"/><path d="M3 14h5l2 3h4l2-3h5"/>',
  'pause':'<path d="M8 4v16M16 4v16"/>',
  'file':'<path d="M14 2H4v20h16V8Zm0 0v6h6M8 12h8M8 16h6"/>',
  'image':'<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8" cy="8" r="1.5"/><path d="m21 16-5-5-9 10"/>',
  'undo':'<path d="M3 10V4m0 6h6M3 10a9 9 0 1 1 2 9"/>',
  'menu':'<path d="M4 6h16M4 12h16M4 18h16"/>',
};
export const icon = (name, cls = '') => `<svg class="icon ${cls}" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.info}</svg>`;
export function hydrateIcons(root = document) { $$('[data-icon]', root).forEach(el => {el.innerHTML = icon(el.dataset.icon); delete el.dataset.icon;}); }
export function date(value, full = false) {
  if (!value) return '未设置'; const d = new Date(value); if (Number.isNaN(d.valueOf())) return '未设置';
  return new Intl.DateTimeFormat('zh-CN', full ? {timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false} : {timeZone:'Asia/Shanghai',month:'numeric',day:'numeric'}).format(d);
}
export function relative(value) {
  if (!value) return '尚未同步'; const d = new Date(value); const delta = Date.now() - d.valueOf();
  if (!Number.isFinite(delta)) return '尚未同步'; if (delta < 60000) return '刚刚'; if (delta < 3600000) return `${Math.floor(delta / 60000)} 分钟前`;
  return date(value, true);
}
export const priorities = {high:'高优先级',medium:'普通',low:'低优先级'};
export const statuses = {pending:'进行中',completed:'已完成',cancelled:'已忽略'};
export function dueText(value) {
  if (!value) return ''; const d = new Date(value); if (!Number.isFinite(d.valueOf())) return '';
  const today = new Date(); const civilDay = value => {const p=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(value).map(x=>[x.type,x.value]));return Date.UTC(Number(p.year),Number(p.month)-1,Number(p.day));};const day=civilDay(d);const nowDay=civilDay(today);
  if (d.valueOf() < today.valueOf()) return `逾期 · ${date(value)}`; if (day === nowDay) return '今天截止'; if (day - nowDay === 86400000) return '明天截止'; return `${date(value)} 截止`;
}
export function localDateTime(value) {
  if (!value) return '';const d = new Date(value); if (!Number.isFinite(d.valueOf())) return '';
  const p=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(d).map(x=>[x.type,x.value]));return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
}
export function initials(value) {return String(value || '微信').trim().slice(0,2);}
export const options = (items, value) => items.map(([v,l])=>`<option value="${escape(v)}" ${String(v) === String(value) ? 'selected' : ''}>${escape(l)}</option>`).join('');
export async function api(path, {method='GET', body, signal, timeoutMs=30000} = {}) {
  const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), timeoutMs);
  const abort = () => controller.abort();signal?.addEventListener('abort',abort,{once:true});
  try {
    const response = await fetch(path, {method,headers:body === undefined ? {} : {'Content-Type':'application/json'},body:body === undefined ? undefined : JSON.stringify(body),signal:controller.signal});
    const contentType = response.headers.get('content-type') || ''; const data = contentType.includes('json') ? await response.json() : await response.text();
    if (!response.ok) {const message = data?.error || data?.detail || `请求失败 (${response.status})`;const labels={title:'事项标题',name:'规则名称',description:'说明',owner:'负责人',pattern:'匹配表达式',due_at:'截止时间',ai_base_url:'服务地址',ai_model:'模型名称',batch_interval_seconds:'提炼间隔',history_limit:'历史导入上限'};const friendly = item => {const key=item.loc?.at(-1);const prefix=labels[key] || '';const type=item.type || '';const text=type==='string_too_long' ? `最多 ${item.ctx?.max_length ?? ''} 个字符` : type==='string_too_short' ? '不能为空' : type==='greater_than_equal' ? `不能小于 ${item.ctx?.ge ?? ''}` : type==='less_than_equal' ? `不能大于 ${item.ctx?.le ?? ''}` : (item.msg || '请检查输入内容').replace(/^Value error, /,'');return `${prefix}${prefix ? '：' : ''}${text}`;};const err = new Error(typeof message === 'string' ? message : Array.isArray(message) ? message.map(friendly).join('；') : JSON.stringify(message));err.status=response.status;throw err;}
    return data;
  } catch(error) {if(error.name === 'AbortError') throw new Error('请求超时，请稍后重试'); throw error;}
  finally {clearTimeout(timeout);signal?.removeEventListener('abort',abort);}
}
export function safeMedia(value) {
  if (!value) return ''; const raw = String(value);
  const relative = raw.startsWith('/media/') ? raw.slice('/media/'.length) : raw;
  if (/^(?:[a-z]:|[\\/]{2})/i.test(relative) || relative.split(/[\\/]/).some(segment=>!segment || segment==='.' || segment==='..')) return '';
  return `/media/${relative.replace(/\\/g,'/').split('/').map(encodeURIComponent).join('/')}`;
}
export function messageMarkup(message) {
  const attachment = message.metadata?.attachment; const localFile = message.metadata?.local_file || attachment?.local_file || attachment?.path;
  const media = localFile ? safeMedia(localFile) : '';
  const picture = attachment?.kind === 'image' || ['image','图片'].includes(message.message_type);
  return `<article class="source-message ${message.is_self ? 'self' : ''}"><div class="message-meta"><strong>${escape(message.sender_name || (message.is_self ? '我' : '微信消息'))}</strong><time>${escape(date(message.created_at,true))}</time></div><div class="message-content">${escape(message.content || '')}</div>${picture && media ? `<a href="${escape(media)}" target="_blank" rel="noopener"><img class="message-image" src="${escape(media)}" alt="微信图片附件" loading="lazy"></a>` : attachment ? `<div class="attachment">${icon('file')}<span>${escape(attachment.name || attachment.kind || '消息附件')}</span>${media ? `<a href="${escape(media)}" target="_blank" rel="noopener">查看${icon('external')}</a>` : ''}</div>` : ''}</article>`;
}
