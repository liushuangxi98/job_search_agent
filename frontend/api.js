export async function api(path, options = {}) {
  const headers = {...options.headers};
  let body = options.body;
  if (body && !(body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(body);
  }
  let response;
  try { response = await fetch('/api' + path, {...options, headers, body}); }
  catch { throw new Error('无法连接本机服务，请检查启动窗口。'); }
  if (response.status === 204) return null;
  const value = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = value.error || {};
    const fields = (error.fields || []).map(x => `${x.field}：${x.message}`).join('；');
    const failure = new Error(fields || error.message || `请求失败（${response.status}）`);
    failure.status = response.status;
    failure.code = error.code;
    throw failure;
  }
  return value;
}

export const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export const money = value => value == null ? '待补充' : `${Number(value) / 1000}K`;
export const score = value => value == null ? '—' : Number(value).toFixed(1).replace(/\.0$/, '');
export const when = value => value ? new Date(value).toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}) : '';

export function field(label, name, value = '', options = {}) {
  const common = `name="${esc(name)}" ${options.required ? 'required' : ''} ${options.maxLength ? `maxlength="${options.maxLength}"` : ''} ${options.placeholder ? `placeholder="${esc(options.placeholder)}"` : ''}`;
  const control = options.area
    ? `<textarea ${common} rows="${options.rows || 4}">${esc(value)}</textarea>`
    : `<input ${common} type="${options.type || 'text'}" value="${esc(value)}" ${options.type === 'number' ? 'min="1" step="1"' : ''}>`;
  return `<label class="field ${options.full ? 'full' : ''}"><span>${esc(label)}</span>${control}${options.hint ? `<small>${esc(options.hint)}</small>` : ''}</label>`;
}

export function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

export function empty(title, description, action = '') {
  return `<div class="empty"><div class="empty-icon" aria-hidden="true">＋</div><h3>${esc(title)}</h3><p>${esc(description)}</p>${action}</div>`;
}
