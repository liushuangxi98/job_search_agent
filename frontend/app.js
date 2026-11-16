import {api, esc, field, formData, empty, money, score, when} from './api.js';

const $ = selector => document.querySelector(selector);
const pages = {
  resume: ['简历画像', '01 / 个人经历与求职条件'],
  jobs: ['岗位池', '我的机会 / 逐个了解，认真选择'],
};
const labels = {
  name: '姓名', education: '学历', major: '大学专业', experience_years: '工作年限', skills: '技能',
  summary: '个人简介', commute_mode: '出行偏好',
  experience: '工作经历', projects: '项目经历', direction: '求职方向', city: '城市',
  commute_origin: '通勤起点', salary_min: '月薪下限', salary_max: '月薪上限',
  company: '公司', title: '职位', address: '办公地址', job_description: '岗位描述和职责', job_requirement: '任职要求', salary: '薪资',
};
const requiredProfileKeys = [
  'name', 'education', 'major', 'experience_years', 'skills', 'experience', 'projects',
  'direction', 'city', 'commute_origin', 'salary_min', 'salary_max', 'commute_mode', 'summary',
];
const editableProfileKeys = requiredProfileKeys;
const commuteModes = {'': '请选择', '公共交通': '公共交通', '驾车': '驾车'};
const jobTextKeys = ['company', 'title', 'city', 'address', 'job_description', 'job_requirement', 'education'];
const actionNames = {parse_resume: '简历整理', extract_jobs: '岗位提取', evaluate: '岗位评估', interview: '模拟面试', chat: '聊天'};
const state = {
  page: 'jobs', params: new URLSearchParams(), health: null, generation: 0,
  candidate: null, profileDraft: null, drafts: {},
  jobs: [], reports: [], documents: [], query: '', offset: 0, expanded: new Set(),
  selectedReports: {}, routeHash: null, run: null, runs: new Map(), timer: null,
  starting: false, responding: new Set(), processed: new Set(), pollError: false,
  batch: null, runBatches: new Map(), uploadKind: null, modal: null,
  chatOpen: false, messages: [], chatDraft: '',
  sessionId: crypto.randomUUID(), scenario: 'success',
};
let toastTimer;
const button = (text, action, attrs = '', cls = '') => `<button type="button" data-action="${action}" class="${cls}" ${attrs}>${text}</button>`;
const card = (title, subtitle, body, footer = '') => `<article class="card"><header class="card-head"><div><h2>${esc(title)}</h2><p>${esc(subtitle)}</p></div></header><div class="card-body">${body}</div>${footer ? `<footer class="card-footer">${footer}</footer>` : ''}</article>`;
const isDemo = () => state.health?.mode === 'demo';
const active = () => state.run && ['running', 'needs_input'].includes(state.run.status);
const occupied = () => state.starting || active() || !!state.batch;
const splitWords = value => value.split(/[,，\n]/).map(x => x.trim()).filter(Boolean);
const runJob = run => ['evaluate', 'interview'].includes(run?.action)
  ? run.subject_job_id || (run.context?.type === 'job' ? run.context.id : null) : null;
const reportJobs = report => [...new Set([...(report.snapshot?.jobs || []).map(x => x.id), ...(report.data?.jobs || []).map(x => x.job_id)].filter(Boolean))];
const jobReports = id => state.reports.filter(report => reportJobs(report).includes(id));
const idAttr = id => `data-id="${esc(id)}"`;

function toast(message, error = false) {
  $('#toast').textContent = message;
  $('#toast').hidden = false;
  $('#toast').classList.toggle('error', error);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, error ? 9000 : 4500);
}
function showError(error) {
  toast(error.message || '操作失败', true);
  if (error.status === 413 || error.code === 'DATABASE_UNAVAILABLE') {
    $('#global-error').textContent = error.message;
    $('#global-error').hidden = false;
  }
}
function draftKey(form) {
  return form.dataset.inputId ? 'input:' + form.dataset.inputId : form.id === 'profile-form' ? 'candidate' : null;
}
function rememberDraft(form, dirty = false) {
  const key = draftKey(form);
  if (!key) return;
  if (dirty) form.dataset.dirty = 'true';
  if (form.dataset.dirty !== 'true') return;
  state.drafts[key] = {
    values: formData(form),
    revision: state.drafts[key]?.revision ?? state.candidate?.revision,
  };
}
function restoreDrafts(root = document) {
  for (const form of root.querySelectorAll('form')) {
    const draft = state.drafts[draftKey(form)];
    if (!draft) continue;
    form.dataset.dirty = 'true';
    for (const [name, value] of Object.entries(draft.values)) {
      if (form.elements[name]) form.elements[name].value = value;
    }
  }
}
function go(page, params = {}) {
  const query = new URLSearchParams(params).toString();
  const hash = `#/${page}${query ? '?' + query : ''}`;
  if (location.hash === hash) return render();
  location.hash = hash;
}
function context(jobId = null) {
  const report = state.params.get('report');
  if (jobId) return {type: 'job', id: jobId, session_id: state.sessionId};
  if (report) return {type: 'report', id: report, session_id: state.sessionId};
  const id = state.params.get('job');
  return id ? {type: 'job', id, session_id: state.sessionId} : {type: state.page, session_id: state.sessionId};
}
function contextTitle() {
  const job = state.jobs.find(x => x.id === state.params.get('job'));
  return job ? `${job.data.company || '未披露公司'} · ${job.data.title || '未披露职位'}` : pages[state.page][0];
}
function updateControls() {
  for (const node of document.querySelectorAll('[data-start]')) {
    node.disabled = !!occupied() || node.dataset.blocked === 'true';
  }
  $('#active-activity').hidden = !active();
  $('#active-activity').textContent = active() ? `返回${actionNames[state.run.action]}` : '返回当前活动';
  $('#scenario').disabled = !!occupied();
  document.querySelectorAll('#chat-form textarea, #chat-form button').forEach(node => {
    node.disabled = !!occupied();
  });
}
function fileRows(items, action) {
  if (!items.length) return '<p class="caption">还没有上传资料。</p>';
  return items.map(x => `<div class="file-row"><span class="file-icon">${esc(x.filename.split('.').pop().toUpperCase())}</span><div class="file-info"><a href="/api/documents/${encodeURIComponent(x.id)}/content" target="_blank" rel="noopener">${esc(x.filename)}</a><small>${(x.size / 1024).toFixed(1)} KB · ${when(x.created_at)}</small></div>${button('处理', action, `${idAttr(x.id)} data-start`, 'ghost small')}${button('移除', 'delete-document', idAttr(x.id), 'ghost small')}</div>`).join('');
}
function uploadBox(kind, title, text) {
  return `<div class="upload-zone"><div class="upload-symbol" aria-hidden="true">↥</div><h3>${esc(title)}</h3><p>${esc(text)}</p>${button('选择文件', 'upload', `data-kind="${kind}" data-start`, 'secondary')}</div>`;
}
function profileEvidence(items) {
  const entries = items.map(item => {
    const source = [item.document_id, item.locator].filter(Boolean).join(' · ');
    return `<li class="prose">${esc(item.text ?? (source ? '' : JSON.stringify(item)))} ${esc(source)}</li>`;
  });
  return `<details class="detail-section"><summary>提取依据</summary>${entries.length ? `<ul>${entries.join('')}</ul>` : '<p class="caption">暂无提取依据。</p>'}</details>`;
}
function candidateFields(profile) {
  const required = {required: true};
  const salary = {required: true, type: 'number', max: 10000000};
  return `<div class="form-grid">
    ${field('姓名', 'name', profile.name, {...required, maxLength: 100})}
    ${field('学历', 'education', profile.education, {...required, maxLength: 2000})}
    ${field('大学专业', 'major', profile.major, {...required, maxLength: 200})}
    ${field('个人简介', 'summary', profile.summary, {...required, area: true, full: true, maxLength: 10000})}
    ${field('工作年限', 'experience_years', profile.experience_years ?? '', {...required, type: 'number', min: 0, max: 80, step: 'any', hint: '应届或暂无工作经验填 0'})}
    ${field('技能', 'skills', (profile.skills || []).join('，'), {...required, hint: '用逗号或换行分隔'})}
    ${field('工作经历', 'experience', profile.experience, {...required, area: true, full: true, maxLength: 20000, hint: '暂无工作经历请如实填写'})}
    ${field('项目经历', 'projects', profile.projects, {...required, area: true, full: true, maxLength: 20000})}
  </div><h3 class="form-section">求职条件</h3><div class="form-grid">
    ${field('求职方向', 'direction', profile.direction, {...required, maxLength: 200})}
    ${field('期望城市', 'city', profile.city, {...required, maxLength: 100})}
    ${field('通勤起点', 'commute_origin', profile.commute_origin, {...required, full: true, maxLength: 300, hint: '城市及具体地址，用于查询公交与驾车路线'})}
    <label class="field"><span>出行偏好 <em>*</em></span><select name="commute_mode" required>${Object.entries(commuteModes).map(([key, text]) => `<option value="${key}" ${key === (profile.commute_mode ?? '') ? 'selected' : ''}>${text}</option>`).join('')}</select></label>
    ${field('期望月薪下限（元）', 'salary_min', profile.salary_min ?? '', salary)}
    ${field('期望月薪上限（元）', 'salary_max', profile.salary_max ?? '', salary)}
  </div>${profileEvidence(profile.evidence)}`;
}
async function resumePage() {
  const [candidate, docs, memories] = await Promise.all([api('/candidate'), api('/documents?kind=resume'), api('/memories')]);
  state.candidate = candidate;
  const profile = state.profileDraft || candidate.profile;
  const draft = state.drafts.candidate;
  const memoryBody = memories.items.map(x => `<div class="memory-row"><p>${esc(x.data.content)}<small class="prose">${esc(x.data.evidence)}</small></p>${button('编辑', 'edit-memory', idAttr(x.id), 'ghost small')}${button('移除', 'delete-memory', idAttr(x.id), 'ghost small')}</div>`).join('');
  return `<div class="page-intro"><div><h2>让经历与期待，都有清晰的表达</h2><p>整理简历和求职条件，核对后一起确认。所有标 * 项均为必填。</p></div><span class="badge ${candidate.confirmed && !draft && !state.profileDraft ? 'green' : 'amber'}">${draft || state.profileDraft ? '草稿待确认' : candidate.confirmed ? '已确认' : '待完善'}</span></div>
    <div data-page-run="resume"></div><div class="split"><div class="stack">
      ${card('简历资料', 'PDF / DOCX · 单文件不超过 16 MiB', uploadBox('resume', '上传简历', '原件会保留，提取结果需要人工确认') + fileRows(docs.items, 'parse-resume') + (isDemo() ? button('查看示例画像', 'sample-profile', 'data-start', 'ghost small') : ''))}
      ${card('已确认的记忆', '你可以查看、修改或移除', memoryBody || '<p class="caption">暂无已确认记忆。</p>', button('补充一条记忆', 'add-memory', '', 'ghost small'))}
    </div><article class="card"><header class="card-head"><div><h2>我的简历画像</h2><p>人工核对 · 薪资仅作资料展示</p></div><span class="badge">修订 ${candidate.revision}</span></header><div class="card-body">
      ${draft ? `<div class="tip">${draft.revision !== candidate.revision ? '已保存资料有更新；未保存输入已保留，直接保存会提示版本冲突。' : '未保存的输入已保留。'} ${button('读取已保存资料', 'discard-candidate', '', 'ghost small')}</div>` : ''}
      <form id="profile-form"><div id="candidate-errors" class="error-banner" role="alert" hidden></div>${candidateFields(profile)}<div class="form-actions"><button>保存并确认画像与条件</button>${button('删除画像', 'delete-candidate', '', 'ghost')}</div></form>
      ${(profile.evidence || []).length ? `<details class="detail-section"><summary>原始提取依据</summary><pre class="prose">${esc(JSON.stringify(profile.evidence, null, 2))}</pre></details>` : ''}
    </div></article></div>`;
}
function commuteHTML(commutes = {}, origin, destination) {
  // 已保存的真实报告在两种方式的首行都记录了用户最终确认的起终点。
  const transit = commutes.public_transit?.route_summary || '';
  const driving = commutes.driving?.route_summary || '';
  const firstLine = transit.split('\n', 1)[0];
  const shared = firstLine.includes(' → ') && firstLine === driving.split('\n', 1)[0];
  const endpoints = shared ? firstLine : [origin || '起点未知', destination || '终点未知'].join(' → ');
  return `<h3>单程通勤</h3><p class="prose">${esc(endpoints)}</p><div class="commutes">${[['public_transit', '公交'], ['driving', '驾车']].map(([key, label]) => {
    const route = commutes[key] || {};
    const duration = route.duration_minutes == null ? '时间未知' : `${esc(route.duration_minutes)} 分钟`;
    const distance = route.distance_km == null ? '距离未知' : `${esc(route.distance_km)} 公里`;
    const summary = shared ? route.route_summary.slice(firstLine.length).trimStart() : route.route_summary;
    return `<div class="commute"><span class="caption">${label} · 单程</span><strong>${duration} / ${distance}</strong><p>${esc(summary || '路线未知')}</p></div>`;
  }).join('')}</div>`;
}
function reportSection(title, value) {
  if (!value?.length) return '';
  const body = Array.isArray(value)
    ? `<ul>${value.map(x => `<li class="prose">${esc(x)}</li>`).join('')}</ul>`
    : `<p class="prose">${esc(value)}</p>`;
  return `<h3>${esc(title)}</h3>${body}`;
}
function reportContent(report, jobId) {
  const data = report.data;
  const header = `<div class="report-head"><div><h2>${esc(data.title || '报告')}</h2><small>${when(report.created_at)} · ${report.is_demo ? '固定模拟样例' : '已保存快照'}</small></div><div class="actions"><a href="/api/reports/${encodeURIComponent(report.id)}/export?format=md">导出 MD ↗</a><a href="/api/reports/${encodeURIComponent(report.id)}/export?format=json">JSON ↗</a>${report.kind === 'evaluation' ? button('删除本次评估', 'delete-evaluation', `${idAttr(report.id)} data-job="${esc(jobId)}"`, 'ghost small') : ''}</div></div>`;
  if (report.kind === 'evaluation') {
    const row = (data.jobs || []).find(x => x.job_id === jobId);
    if (!row) return header + '<p class="caption">该历史报告没有对应岗位的匹配结果。</p>';
    const job = report.snapshot?.jobs?.find(item => item.id === jobId)?.data || row;
    const legacyAssessment = {matched: '符合', partial: '部分符合', gap: '不符合', unknown: '资料不足'};
    return header + `<h3>${esc(job.company || '未披露公司')} · ${esc(job.title || '未披露职位')}</h3><div class="report-score">${score(row.match_score)}<small>匹配分 / 100</small></div>
      ${row.business_analysis ? reportSection('业务分析', row.business_analysis) : ''}
      ${(row.business_sources || []).length ? `<details class="detail-section"><summary>联网检索来源</summary><ol>${row.business_sources.map((s, i) => `<li>[${i + 1}] ${/^https?:\/\//.test(s.url) ? `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a>` : esc(s.title)}</li>`).join('')}</ol></details>` : ''}
      ${reportSection('求职建议', row.recommendation || '暂无建议。')}
      ${reportSection('最有利的经历', row.supporting_experiences || row.strengths || '本次未列出。')}
      ${reportSection('最不利的关键要求', row.key_requirement || row.gaps || '本次未列出。')}
      <h3>逐项匹配证据</h3>${(row.matches || []).length ? `<div class="table-scroll"><table><thead><tr><th>岗位要求</th><th>个人证据</th><th>判断</th></tr></thead><tbody>${row.matches.map(x => `<tr><td>${esc(x.requirement)}</td><td>${esc(x.evidence || '未知')}</td><td>${esc(legacyAssessment[x.assessment] || x.assessment || '资料不足')}</td></tr>`).join('')}</tbody></table></div>` : '<p class="caption">本次报告未提供逐项匹配依据。</p>'}
      ${commuteHTML(row.commutes, report.snapshot?.profile?.data?.commute_origin, job.address)}
      ${reportSection('评估说明', data.summary)}${reportSection('信息缺口', row.missing)}
      ${(row.evidence || []).length ? `<details class="detail-section"><summary>原文证据与引用</summary><pre class="prose">${esc(JSON.stringify(row.evidence, null, 2))}</pre></details>` : ''}
      <details class="detail-section"><summary>本次使用的资料快照</summary><pre class="prose">${esc(JSON.stringify(report.snapshot || {}, null, 2))}</pre></details>`;
  }
  const dimensions = (data.dimensions || []).length
    ? `<h3>面试维度反馈</h3><dl class="job-meta interview-dimensions">${data.dimensions.map(x => `<dt>${esc(x.name)}</dt><dd>${x.score == null ? '未考察' : score(x.score)}</dd>`).join('')}</dl>` : '';
  // topics 用于展示每个考点的结论。rounds 是支撑结论的作答轮号，空数组表示没有已评回答。
  const topics = data.topics?.length ? '<h3>考点反馈</h3><ul>' + data.topics.map(t =>
    `<li><strong>${esc(t.name)} · ${esc(t.grade ?? (t.rounds?.length ? '证据不足' : '未考察'))}</strong> ${esc(t.summary)}${t.rounds?.length ? ` <small>第 ${esc(t.rounds.join('、'))} 轮</small>` : ''}</li>`).join('') + '</ul>' : '';
  if (data.messages?.length) {
    return header + reportSection('面试概述', data.summary) + topics + dimensions +
      reportSection('练习重点', data.weaknesses) + reportSection('推荐练习', data.practice) +
      `<details class="detail-section" open><summary>回看本次对话</summary>${interviewChatHTML(data.messages)}</details>` +
      `<details class="detail-section"><summary>逐题点评</summary><ol class="answers">${(data.answers || []).filter(x => x.feedback).map(x => `<li><strong>${esc(x.question)}</strong><p>${esc(x.feedback)}</p></li>`).join('')}</ol></details>`;
  }
  return header + reportSection('面试概述', data.summary) + topics + dimensions + reportSection('练习重点', data.weaknesses) + reportSection('推荐练习', data.practice) +
    `<details class="detail-section" open><summary>回看本次问答（${(data.answers || []).length} 轮）</summary><ol class="answers">${(data.answers || []).map(x => `<li><strong>${esc(x.question)}</strong>${(x.clarifications || []).map(c => `<p><small>用户提问</small> ${esc(c.user)}<br><small>面试官说明</small> ${esc(c.assistant)}</p>`).join('')}${x.answer ? `<p>${esc(x.answer)}</p>` : ''}${x.feedback ? `<p><strong>面试点评</strong> ${esc(x.feedback)}</p>` : ''}<small>${esc(x.source || '来源未知')}</small></li>`).join('')}</ol></details>`;
}
function jobActivity(jobId, action, reports) {
  const key = jobId + '/' + action;
  const current = reports.find(x => x.id === state.selectedReports[key]) || reports[0];
  const title = actionNames[action];
  return `<details class="job-details" data-job-action="${action}" data-section-key="${esc(key)}" ${state.expanded.has(key) ? 'open' : ''}><summary>${title}${reports.length ? ' · ' + reports.length + ' 次' : ''}</summary>
    <div data-job-run="${esc(jobId)}" data-action-type="${action}"></div>
    ${current ? `<label class="history-controls"><span class="caption">查看记录</span><select data-report-select="${esc(jobId)}" aria-label="选择${title}报告">${reports.map(x => `<option value="${esc(x.id)}" ${x.id === current.id ? 'selected' : ''}>${when(x.created_at)} · ${esc(x.data.title || '报告')}</option>`).join('')}</select></label>
      <article class="report">${reportContent(current, jobId)}${reportTrace(current)}</article>` : `<p class="caption">暂无已保存的${title}报告。</p>`}</details>`;
}
function jobEvidence(items) {
  const documentId = items.find(item => item.document_id)?.document_id;
  const document = state.documents.find(item => item.id === documentId);
  const link = document ? `<p><a href="/api/documents/${encodeURIComponent(document.id)}/content" target="_blank" rel="noopener">${esc(document.filename)} ↗</a></p>` : '';
  const quotes = items.map(item => `<li><small>${esc(item.locator)}</small><p class="prose">${esc(item.text || '未记录原文片段')}</p></li>`).join('');
  return `<details class="detail-section"><summary>提取依据</summary>${link}${quotes ? `<ul>${quotes}</ul>` : '<p class="caption">暂无提取依据。</p>'}</details>`;
}
function jobRow(item) {
  const data = item.data;
  const reports = jobReports(item.id);
  const latest = reports.find(x => x.kind === 'evaluation');
  const result = latest?.data.jobs?.find(x => x.job_id === item.id);
  const blocked = !state.candidate?.confirmed;
  return `<article class="job-row" data-job-row="${esc(item.id)}"><div class="job-overview">
    <div><h3>${button(esc(data.title || '未披露职位'), 'open-job', idAttr(item.id), 'job-title')}</h3><small>${esc(data.company || '未披露公司')} · ${esc(data.city || '未披露')}</small></div>
    <div>${data.salary_min == null ? '未披露' : money(data.salary_min)} — ${data.salary_max == null ? '未披露' : money(data.salary_max)}<small>月薪</small></div>
    <div>${result ? `<span class="job-score">${score(result.match_score)}<small>最近匹配分${latest.is_demo ? ' · 模拟' : ''}</small></span>` : `<span class="badge">待评估</span><small>尚无匹配结果</small>`}</div>
    <div class="actions">${button('评估', 'evaluate-job', `${idAttr(item.id)} data-start data-blocked="${blocked}"`, 'secondary small')}${button('模拟面试', 'interview-job', `${idAttr(item.id)} data-start data-blocked="${blocked}"`, 'small')}${button('删除岗位', 'delete-job', idAttr(item.id), 'ghost small')}</div>
    </div><details class="job-details" data-job-details="${esc(item.id)}" data-section-key="${esc(item.id)}" ${state.expanded.has(item.id) ? 'open' : ''}><summary>岗位详情</summary>
      <dl class="job-meta"><dt>公司名称</dt><dd>${esc(data.company || '未披露')}</dd><dt>岗位名称</dt><dd>${esc(data.title || '未披露')}</dd><dt>办公地址</dt><dd>${esc(data.address || '未披露')}</dd><dt>最低工作年限</dt><dd>${data.experience_years == null ? '未披露' : esc(data.experience_years) + ' 年'}</dd><dt>学历要求</dt><dd>${esc(data.education || '未披露')}</dd></dl>
      <h3>岗位描述和职责</h3><p class="prose">${esc(data.job_description || '未披露')}</p>
      <h3>任职要求</h3><p class="prose">${esc(data.job_requirement || '未披露')}</p>
      ${jobEvidence(data.evidence || [])}
      <div class="actions detail-section">${button('编辑岗位', 'edit-job', idAttr(item.id), 'secondary small')}</div>
    </details>${jobActivity(item.id, 'evaluate', reports.filter(x => x.kind === 'evaluation'))}
    ${jobActivity(item.id, 'interview', reports.filter(x => x.kind === 'interview'))}</article>`;
}
async function jobsPage() {
  const [jobs, candidate, reports, docs] = await Promise.all([
    api(`/jobs?q=${encodeURIComponent(state.query)}&offset=${state.offset}&limit=20`),
    api('/candidate'), api('/reports'), api('/documents?kind=image'),
  ]);
  state.candidate = candidate;
  state.jobs = jobs.items;
  state.reports = reports.items;
  state.documents = docs.items;
  const selected = state.params.get('job');
  let missingJob = false;
  if (selected && !state.jobs.some(x => x.id === selected)) {
    try { state.jobs = [await api('/jobs/' + encodeURIComponent(selected)), ...state.jobs]; }
    catch (error) { if (error.status !== 404) throw error; missingJob = true; }
  }
  const linkedReport = state.reports.find(x => x.id === state.params.get('report'));
  return `<div class="page-intro"><div><h2>值得了解的下一站</h2><p>每个岗位独立查看匹配依据、通勤路线与面试记录。</p></div></div>
    ${!candidate.confirmed ? '<div class="tip">请先完善并确认个人资料，才能评估或面试。<a href="#/resume">前往简历画像 →</a></div>' : ''}
    <div class="toolbar"><form id="job-search" class="search"><input name="q" aria-label="搜索岗位" placeholder="搜索公司、职位或城市" value="${esc(state.query)}"><button class="secondary">搜索</button></form><div class="actions">${button('导入截图', 'upload', 'data-kind="image" data-start', 'secondary')}${button('粘贴招聘原文', 'paste-jd', 'data-start', 'secondary')}</div></div>
    ${missingJob ? `<div class="tip">关联岗位已移除或不存在。${linkedReport ? '下方保留当时的报告与资料快照。' : '请从岗位池重新选择。'}</div>${linkedReport ? `<article class="report">${reportContent(linkedReport, selected)}${reportTrace(linkedReport)}</article>` : ''}` : ''}
    <div data-page-run="jobs"></div><div class="job-list">${state.jobs.length ? state.jobs.map(jobRow).join('') : card('岗位池', '从一个感兴趣的岗位开始', empty('还没有导入岗位', '通过上方的“导入截图”或“粘贴招聘原文”开始。'))}</div>
    <div class="table-footer"><span>共 ${jobs.total} 个岗位</span><div class="actions">${button('上一页', 'jobs-prev', state.offset === 0 ? 'disabled' : '', 'ghost small')}<span>${Math.floor(state.offset / 20) + 1}</span>${button('下一页', 'jobs-next', state.offset + 20 >= jobs.total ? 'disabled' : '', 'ghost small')}</div></div>`;
}
function placeField(side, label, names) {
  const options = names.map((name, i) => `<option value="${i + 1}">${esc(name)}</option>`).join('');
  return `<label class="field"><span>${label}</span><select name="${side}" required>
    <option value="">请选择地点</option>${options}</select></label>`;
}
function interviewChatHTML(messages) {
  if (!messages?.length) return '';
  return `<div class="interview-chat" aria-label="面试聊天记录">${messages.map(item =>
    `<div class="bubble ${item.role === 'user' ? 'user' : ''}"><small>${item.role === 'user' ? '我' : '面试官'}</small><div class="prose">${esc(item.content)}</div>${item.source ? `<small>来源：${esc(item.source)}</small>` : ''}${item.hint ? `<details><summary>查看提示</summary><div class="prose">${esc(item.hint)}</div></details>` : ''}</div>`).join('')}</div>`;
}
function pendingHTML(run) {
  const pending = run.pending_input;
  if (!pending) return '';
  if (pending.kind === 'proposal') {
    return `<section class="pending"><h3>请确认这次修改</h3><p>${esc(pending.prompt)}</p><pre class="prose">${esc(JSON.stringify(pending.proposal?.changes || {}, null, 2))}</pre><div class="actions">${button('拒绝修改', 'run-decision', `${idAttr(run.id)} data-decision="reject"`, 'secondary')}${button('确认并保存', 'run-decision', `${idAttr(run.id)} data-decision="confirm"`)}</div></section>`;
  }
  const interview = run.action === 'interview';
  const data = run.result?.data || {};
  if (run.action === 'evaluate' && data.place_choices) {
    return `<section class="pending"><h3>确认通勤地点</h3><p>${esc(pending.prompt)}</p>
      <form data-run-answer="${esc(run.id)}" data-input-id="${esc(pending.id)}" data-places>
        <div class="form-grid">${placeField('start', '通勤起点', data.place_choices.start)}
        ${placeField('end', '办公地点', data.place_choices.end)}</div>
        <div class="actions">${button('结束本次', 'run-decision', `${idAttr(run.id)} data-decision="finish"`, 'secondary')}<button>确认并继续</button></div></form></section>`;
  }
  // 面试消息在上方聊天记录中展示，这里只保留发送输入的表单。
  return `<section class="pending">${interview ? '' : `<h3>需要你补充一点信息</h3><p>${esc(pending.prompt)}</p>`}
    <form data-run-answer="${esc(run.id)}" data-input-id="${esc(pending.id)}"><textarea name="text" required maxlength="20000" rows="4" aria-label="${interview ? '面试消息' : '补充信息'}" placeholder="${interview ? '输入你的回答或问题…' : '填写补充内容'}"></textarea><div class="actions">${button(interview ? '结束面试并生成报告' : '结束本次', 'run-decision', `${idAttr(run.id)} data-decision="finish"`, 'secondary')}<button>${interview ? '发送' : '提交并继续'}</button></div></form></section>`;
}
function runHTML(run) {
  const status = {running: '运行中', needs_input: '等待补充', completed: '已完成', partial: '部分结果', failed: '失败'}[run.status];
  const reviewable = ['profile_draft', 'job_drafts'].includes(run.result?.kind);
  return `<section class="run-panel"><div class="run-head"><h3>${esc(actionNames[run.action] || '当前活动')}</h3><span class="badge ${run.status === 'failed' ? 'red' : run.status === 'needs_input' ? 'amber' : 'green'}">${status}</span></div>
    ${run.is_demo ? '<p class="caption">固定模拟样例 · 未调用模型或真实工具</p>' : ''}
    ${run.action === 'interview' ? interviewChatHTML(run.messages) : ''}
    ${run.status === 'running' ? '<p class="caption">正在处理，请稍候。当前活动结束前不能开始另一项活动。</p>' : ''}
    ${run.status === 'failed' ? `<div class="error-banner">${esc(run.result?.data?.message || '处理失败，请人工整理资料。')}</div>` : pendingHTML(run)}
    ${reviewable && !['running', 'needs_input', 'failed'].includes(run.status) ? button('核对处理结果', 'review-result', idAttr(run.id), 'secondary small') : ''}
    ${traceHTML(run.events || [])}</section>`;
}
function traceHTML(events) {
  return `<details class="trace"><summary>执行记录（${events.length}）</summary>${traceEntries(events)}</details>`;
}
function traceEntries(events) {
  return events.map(x => `<div class="trace-item"><small>${when(x.at)}</small><div>${esc(x.message)}${Object.keys(x.data || {}).length ? `<pre>${esc(JSON.stringify(x.data, null, 2))}</pre>` : ''}</div></div>`).join('') || '<p class="caption">暂无执行记录。</p>';
}
function reportTrace(report) {
  const run = state.runs.get(report.run_id);
  if (run?.report_id === report.id) return traceHTML(run.events);
  return `<details class="trace" data-report-trace="${esc(report.id)}"><summary>执行记录</summary><div></div></details>`;
}
function runPage(run) {
  return {parse_resume: 'resume', extract_jobs: 'jobs', evaluate: 'jobs', interview: 'jobs'}[run.action];
}
function updateRunPanels() {
  // 轮询只更新所属流程；输入通过 pending_input.id 保存，历史运行不会抢占活动。
  const hosts = [...document.querySelectorAll('[data-job-run], [data-page-run]')];
  for (const host of hosts) {
    const id = host.dataset.jobRun;
    const candidates = [...state.runs.values()].filter(run => id
      ? runJob(run) === id && run.action === host.dataset.actionType
      : !runJob(run) && runPage(run) === host.dataset.pageRun);
    const latest = candidates.find(x => x.id === state.run?.id && active()) || candidates.at(-1);
    const run = id && latest?.report_id ? null : latest;
    const signature = run ? JSON.stringify([run.id, run.status, run.next_seq, run.pending_input, run.result, run.messages]) : '';
    if (host.dataset.signature === signature) continue;
    host.querySelectorAll('form').forEach(form => rememberDraft(form));
    const traceOpen = host.dataset.runId === run?.id && host.querySelector('.trace')?.open;
    const chat = host.querySelector('.interview-chat');
    const chatTop = chat?.scrollTop || 0;
    const followChat = !chat || chat.scrollHeight - chat.scrollTop - chat.clientHeight < 40;
    host.innerHTML = run ? runHTML(run) : '';
    const newChat = host.querySelector('.interview-chat');
    if (newChat) newChat.scrollTop = followChat ? newChat.scrollHeight : chatTop;
    host.dataset.runId = run?.id || '';
    if (traceOpen && run) host.querySelector('.trace').open = true;
    host.dataset.signature = signature;
    restoreDrafts(host);
  }
  updateControls();
}
async function resolveRoute() {
  const entering = location.hash !== state.routeHash;
  const [path, query = ''] = (location.hash.slice(1) || '/jobs').split('?');
  const original = path.replace(/^\//, '');
  state.page = ({preferences: 'resume', evaluate: 'jobs', reports: 'jobs', interview: 'jobs'})[original] || original;
  if (!pages[state.page]) state.page = 'jobs';
  state.params = new URLSearchParams(query);
  if (original === 'reports' && state.params.get('id')) {
    state.params.set('report', state.params.get('id'));
    state.params.delete('id');
  }
  const runId = state.params.get('run');
  if (runId) {
    let run = state.runs.get(runId);
    if (!run) {
      try { run = setRun(await api('/runs/' + encodeURIComponent(runId)), false); }
      catch (error) { if (error.status !== 404) throw error; toast(error.message, true); state.params.delete('run'); }
    }
    if (runJob(run)) {
      state.params.set('job', runJob(run));
      if (entering) state.expanded.add(runJob(run) + '/' + run.action);
    }
    if (run?.report_id && !state.params.get('report')) state.params.set('report', run.report_id);
  }
  const reportId = state.params.get('report');
  if (reportId) {
    try {
      const report = await api('/reports/' + encodeURIComponent(reportId));
      const jobId = state.params.get('job') || reportJobs(report)[0];
      if (jobId) {
        state.params.set('job', jobId);
        const key = jobId + '/' + (report.kind === 'evaluation' ? 'evaluate' : 'interview');
        state.selectedReports[key] = reportId;
        if (entering) state.expanded.add(key);
      }
    } catch (error) { if (error.status !== 404) throw error; toast(error.message, true); state.params.delete('report'); }
  }
  if (entering && state.params.get('job') && !runId && !reportId) state.expanded.add(state.params.get('job'));
  const suffix = state.params.toString();
  const hash = `#/${state.page}${suffix ? '?' + suffix : ''}`;
  if (location.hash !== hash) history.replaceState(null, '', hash);
  state.routeHash = hash;
}
async function render() {
  const generation = ++state.generation;
  await resolveRoute();
  const renderer = {resume: resumePage, jobs: jobsPage}[state.page];
  const html = await renderer();
  if (generation !== state.generation) return;
  // 请求期间仍可能发生键入，因此在替换 DOM 前再次保存。
  $('#view').querySelectorAll('form').forEach(form => rememberDraft(form));
  $('#view').innerHTML = html;
  $('#page-title').textContent = pages[state.page][0];
  $('#eyebrow').textContent = pages[state.page][1];
  document.querySelectorAll('nav a').forEach(x => x.classList.toggle('active', x.dataset.page === state.page));
  restoreDrafts($('#view'));
  updateRunPanels();
  renderChat();
}
function setRun(run, makeActive = true) {
  const previous = state.runs.get(run.id);
  const events = new Map((previous?.events || []).map(x => [x.seq, x]));
  (run.events || []).forEach(x => events.set(x.seq, x));
  run.events = [...events.values()].sort((a, b) => a.seq - b.seq);
  run.next_seq = Math.max(previous?.next_seq || 0, run.next_seq || 0);
  state.runs.set(run.id, run);
  if (makeActive) state.run = run;
  return run;
}
async function begin(action, input = {}, batch = null, jobId = null) {
  if (state.starting || active() || (state.batch && state.batch !== batch)) throw new Error('请先完成或结束当前活动。');
  state.starting = true;
  updateControls();
  try {
    if (isDemo()) input.demo_scenario = state.scenario;
    const run = await api('/runs', {method: 'POST', body: {action, context: context(jobId), input}});
    setRun(run);
    if (batch) state.runBatches.set(run.id, batch);
    if (jobId) {
      state.expanded.add(jobId + '/' + action);
      await go('jobs', {job: jobId, run: run.id});
    }
    updateRunPanels();
    renderChat();
  } finally {
    state.starting = false;
    updateControls();
  }
  await poll();
}
async function poll() {
  clearTimeout(state.timer);
  if (!state.run) return;
  const id = state.run.id;
  try {
    const run = await api(`/runs/${encodeURIComponent(id)}?after=${state.run.next_seq || 0}`);
    if (state.run?.id !== id) return;
    state.pollError = false;
    setRun(run);
    updateRunPanels();
    renderChat();
    await settled(run);
    if (state.run?.id === id && run.status === 'running') state.timer = setTimeout(poll, 650);
  } catch (error) {
    if (state.run?.id !== id) return;
    if (!state.pollError) showError(error);
    state.pollError = true;
    if (error.status && error.status < 500) {
      cancelBatch(state.runBatches.get(id));
      setRun({...state.run, status: 'failed', pending_input: null, result: {kind: 'error', data: {message: error.message}}});
      updateRunPanels();
      renderChat();
    } else if (state.run.status === 'running') {
      state.timer = setTimeout(poll, 1000);
    }
  }
}
async function settled(run) {
  if (['running', 'needs_input'].includes(run.status) || state.processed.has(run.id)) return;
  state.processed.add(run.id);
  const result = run.result || {};
  if (run.status === 'failed') {
    const batch = state.runBatches.get(run.id);
    if (run.action === 'extract_jobs' && result.data?.code === 'JOB_TEXT_FOLDED' &&
        batch?.kind === 'image' && batch === state.batch && batch.queue.length) {
      state.modal = {kind: 'folded-image', batch};
      openDialog('当前截图的岗位正文被折叠', `<p>${esc(batch.filename)}：${esc(result.data.message)}</p>`,
        button('跳过并继续下一张', 'continue-images'));
      return;
    }
    cancelBatch(batch);
    showError(new Error(result.data?.message || '运行失败，请手动整理资料。'));
    return;
  }
  if (run.action === 'chat' && result.kind === 'chat') {
    state.messages.push({role: 'assistant', text: result.data.message || '', context: run.is_demo ? '固定模拟回复' : 'Agent 回复'});
    renderChat();
  }
  if (['profile_draft', 'job_drafts'].includes(result.kind) && !$('#editor').open) reviewResult(run);
  if (run.report_id) {
    state.reports = (await api('/reports')).items;
    const jobId = runJob(run) || reportJobs(state.reports.find(x => x.id === run.report_id) || {})[0];
    if (jobId) {
      state.selectedReports[jobId + '/' + run.action] = run.report_id;
    }
    if (state.page === 'jobs') await render();
    toast('本次结果已保存，可在岗位行内回看。');
  }
  updateControls();
}
async function respond(runId, decision, text = '', data = {}) {
  const run = state.runs.get(runId);
  const pending = run?.pending_input;
  if (!pending) throw new Error('该输入已处理，请查看当前状态。');
  if (state.responding.has(pending.id)) return;
  state.responding.add(pending.id);
  document.querySelectorAll(`[data-run-answer="${CSS.escape(runId)}"] button`).forEach(x => { x.disabled = true; });
  if (decision === 'finish') cancelBatch(state.runBatches.get(runId));
  try {
    const response = await api(`/runs/${encodeURIComponent(runId)}/input`, {method: 'POST', body: {input_id: pending.id, decision, text, data}});
    delete state.drafts['input:' + pending.id];
    setRun(response);
    updateRunPanels();
    renderChat();
    if (decision === 'confirm') await render();
    await poll();
  } catch (error) {
    if (error.status === 413) {
      cancelBatch(state.runBatches.get(runId));
      setRun({...run, status: 'failed', pending_input: null, result: {kind: 'error', data: {message: error.message}}});
      updateRunPanels();
    }
    throw error;
  } finally {
    state.responding.delete(pending.id);
    document.querySelectorAll(`[data-run-answer="${CSS.escape(runId)}"] button`).forEach(x => { x.disabled = false; });
  }
}
function renderChat() {
  const drawer = $('#chat-drawer');
  drawer.hidden = !state.chatOpen;
  if (!state.chatOpen) return;
  drawer.querySelectorAll('form[data-run-answer]').forEach(form => rememberDraft(form));
  const pending = active() && state.run.action === 'chat' ? pendingHTML(state.run) : '';
  drawer.innerHTML = `<header class="chat-head"><div><h3>聊一聊</h3><small>当前关联：${esc(contextTitle())} · 同标签页共享完整聊天历史</small></div>${button('✕', 'close-chat', 'aria-label="关闭聊天"', 'ghost')}</header>
    <div id="chat-messages" class="chat-messages">${state.messages.length ? state.messages.map(x => `<div class="bubble ${x.role === 'user' ? 'user' : ''}"><small>${esc(x.context || '')}</small>${esc(x.text)}</div>`).join('') : '<div class="bubble">补充经历，或就当前岗位追问。涉及资料修改时，会先请你确认。</div>'}${pending}${occupied() ? `<p class="caption">当前活动尚未结束。${state.run?.action !== 'chat' ? '请回到所属流程补充信息。' : ''}</p>` : ''}</div>
    <form id="chat-form" class="chat-compose"><textarea name="text" id="chat-text" aria-label="聊天消息" required maxlength="20000" placeholder="写下你的问题…" ${occupied() ? 'disabled' : ''}>${esc(state.chatDraft)}</textarea><footer><small>${isDemo() ? '当前回复为固定模拟样例' : '涉及修改会先确认'}</small><button ${occupied() ? 'disabled' : ''}>发送 ↑</button></footer></form>`;
  restoreDrafts(drawer);
  $('#chat-messages').scrollTop = $('#chat-messages').scrollHeight;
}
function newBatch(kind = 'image', queue = []) {
  const batch = {kind, queue, cancelled: false, saved: 0, saving: false};
  state.batch = batch;
  updateControls();
  return batch;
}
function cancelBatch(batch) {
  if (!batch) return;
  batch.cancelled = true;
  batch.queue.length = 0;
  if (state.batch === batch) state.batch = null;
  updateControls();
}
function openDialog(title, body, footer = '') {
  $('#editor').innerHTML = `<header><h2>${esc(title)}</h2>${button('✕', 'close-modal', 'aria-label="关闭编辑"', 'ghost')}</header><div class="dialog-body">${body}</div><footer>${button('取消', 'close-modal', '', 'secondary')}${footer}</footer>`;
  if (!$('#editor').open) $('#editor').showModal();
}
function closeDialog() {
  $('#editor').close();
  state.modal = null;
}
function dismissDialog() {
  const modal = state.modal;
  if (modal?.dirty && !modal.batch?.saving && !confirm('放弃这次尚未保存的编辑？')) return;
  cancelBatch(modal?.batch);
  closeDialog();
  if (modal?.batch?.saving) toast('已取消，正在停止后续导入；已发送的请求可能已经保存。');
}
function jobFields(data) {
  const required = {required: true};
  return [
    field('公司名称', 'company', data.company, {...required, maxLength: 200}),
    field('岗位名称', 'title', data.title, {...required, maxLength: 200}),
    field('工作城市', 'city', data.city, {...required, maxLength: 100}),
    field('办公地址', 'address', data.address, {...required, maxLength: 300}),
    field('月薪下限（元）', 'salary_min', data.salary_min ?? '', {...required, type: 'number', max: 10000000}),
    field('月薪上限（元）', 'salary_max', data.salary_max ?? '', {...required, type: 'number', max: 10000000}),
    field('最低工作年限', 'experience_years', data.experience_years ?? '', {...required, type: 'number', min: 0, max: 80, step: 'any'}),
    field('学历要求', 'education', data.education, {...required, maxLength: 2000}),
    field('岗位描述和职责', 'job_description', data.job_description, {...required, area: true, full: true, rows: 5, maxLength: 50000}),
    field('任职要求', 'job_requirement', data.job_requirement, {...required, area: true, full: true, rows: 5, maxLength: 50000}),
  ].join('');
}
function readJob(root, original = {}) {
  const values = Object.fromEntries([...root.querySelectorAll('[name]')].map(x => [x.name, x.value.trim()]));
  for (const [key, value] of Object.entries(values)) {
    if (!value || value === '未披露') throw new Error(`请补齐岗位信息：${labels[key] || key}`);
  }
  const data = {...original};
  for (const key of jobTextKeys) data[key] = values[key];
  for (const key of ['salary_min', 'salary_max', 'experience_years']) data[key] = Number(values[key]);
  for (const key of ['salary_min', 'salary_max']) {
    if (!Number.isInteger(data[key]) || data[key] < 1 || data[key] > 10000000) throw new Error('月薪应为 1—10000000 的整数。');
  }
  if (data.salary_min > data.salary_max) throw new Error('薪资下限不能超过上限。');
  if (!Number.isFinite(data.experience_years) || data.experience_years < 0 || data.experience_years > 80) throw new Error('最低工作年限应为 0—80 年。');
  return data;
}
function reviewResult(run) {
  const data = run.result?.data || {};
  const kind = run.result?.kind;
  const notice = data.notice || (run.is_demo ? '固定模拟数据，请核对后保存。' : '请核对后保存。');
  const hint = `<p class="tip">${esc(notice)}</p>`;
  if (kind === 'profile_draft') {
    state.modal = {kind, data, batch: state.runBatches.get(run.id)};
    openDialog('核对简历与条件草稿', hint + `<dl class="job-meta">${editableProfileKeys.map(key => {
      const value = key === 'commute_mode' ? commuteModes[data[key]] : data[key];
      return `<dt>${esc(labels[key])}</dt><dd class="prose">${esc(Array.isArray(value) ? value.join('、') : value ?? '待补充')}</dd>`;
    }).join('')}</dl>` + profileEvidence(data.evidence), button('填入画像表单', 'use-profile-draft'));
  } else if (kind === 'job_drafts') {
    let batch = state.runBatches.get(run.id);
    if (batch?.cancelled) { toast('该批次已结束，请重新提取需要的资料。'); return; }
    batch ||= newBatch();
    state.runBatches.set(run.id, batch);
    state.modal = {kind, data, batch};
    openDialog('确认待导入岗位', hint + `<p class="caption">请补齐所有岗位字段后确认入库。明确不限工作经验时填 0。</p><form id="job-draft-form"><section class="draft-job"><label class="caption"><input type="checkbox" data-skip-draft> 跳过此条</label><fieldset><div class="form-grid">${jobFields(data)}</div>${jobEvidence(data.evidence || [])}</fieldset></section></form>`, '<button form="job-draft-form" type="submit">确认入库</button>');

  }
  updateControls();
}
async function editJob(id) {
  const item = await api('/jobs/' + encodeURIComponent(id));
  state.modal = {kind: 'job', item};
  openDialog('编辑岗位', `<form id="job-form"><div class="tip">请填写完整的岗位信息。提取依据仅供核对。</div><div class="form-grid">${jobFields(item.data)}</div>${jobEvidence(item.data.evidence || [])}</form>`, '<button type="submit" form="job-form">保存岗位</button>');
}
async function editMemory(id = null) {
  const response = id ? await api('/memories') : null;
  const item = id ? response.items.find(x => x.id === id) : {data: {category: 'preference'}};
  if (!item) throw new Error('记忆不存在，请刷新。');
  state.modal = {kind: 'memory', item};
  openDialog(id ? '修改已确认记忆' : '补充已确认记忆', `<form id="memory-form"><div class="form-grid">${field('内容', 'content', item.data.content, {area: true, full: true, required: true, maxLength: 10000})}<label class="field full"><span>类别</span><select name="category">${[['preference', '求职偏好'], ['profile', '个人画像'], ['weakness', '练习薄弱项']].map(([key, title]) => `<option value="${key}" ${item.data.category === key ? 'selected' : ''}>${title}</option>`).join('')}</select></label>${field('确认依据', 'evidence', item.data.evidence, {area: true, full: true, maxLength: 10000})}</div></form>`, '<button type="submit" form="memory-form">确认并保存</button>');
}

function chooseFiles(kind) {
  if (occupied()) throw new Error('请先结束当前活动。');
  state.uploadKind = kind;
  const picker = $('#file-picker');
  picker.accept = {resume: '.pdf,.docx', image: '.png,.jpg,.jpeg,.webp'}[kind];
  picker.multiple = kind !== 'resume';
  picker.value = '';
  picker.click();
}
async function nextFile(batch) {
  if (!batch || batch.cancelled || state.batch !== batch) return;
  const file = batch.queue.shift();
  if (!file) {
    state.batch = null;
    updateControls();
    renderChat();
    return;
  }
  try {
    batch.filename = file.name;
    const limit = (batch.kind === 'image' ? 8 : 16) * 1024 * 1024;
    if (file.size > limit) throw new Error(`${file.name} 超过 ${limit / 1024 / 1024} MiB 上限，已停止本批次。`);
    const body = new FormData();
    body.append('kind', batch.kind);
    body.append('file', file);
    const document = await api('/documents', {method: 'POST', body});
    if (batch.cancelled) return;
    await render();
    const action = {resume: 'parse_resume', image: 'extract_jobs'}[batch.kind];
    await begin(action, {document_id: document.id}, batch);
  } catch (error) {
    cancelBatch(batch);
    throw error;
  }
}
async function uploadFiles(files) {
  if (!files.length) return;
  if (occupied()) throw new Error('请先完成当前活动再上传文件。');
  await nextFile(newBatch(state.uploadKind, files));
}
async function saveJobDraft(form) {
  const operation = state.modal;
  if (operation?.kind !== 'job_drafts' || operation.batch.cancelled || operation.batch.saving) return;
  const batch = operation.batch;
  const fields = form.querySelector('fieldset');
  const job = fields.disabled ? null : readJob(fields, operation.data);
  batch.saving = true;
  form.inert = true;
  try {
    if (job) {
      await api('/jobs', {method: 'POST', body: job});
      batch.saved++;
    }
    if (batch.cancelled) {
      await render();
      toast(`已停止后续导入，本批已保存或复用 ${batch.saved} 条岗位。`);
      return;
    }
    if (state.modal === operation) closeDialog();
    await render();
    toast(job ? '岗位已入库。' : '已跳过当前条目。');
    await nextFile(batch);
  } catch (error) {
    if (error.status === 413) {
      cancelBatch(batch);
      if (state.modal === operation) closeDialog();
    }
    if (batch.saved) error.message += ` 已保存或复用 ${batch.saved} 条；后续条目未发送。`;
    throw error;
  } finally {
    batch.saving = false;
    form.inert = false;
  }
}
function validateCandidate(form) {
  const data = formData(form);
  const missing = requiredProfileKeys.filter(key => !data[key]?.trim() || (key === 'skills' && !splitWords(data[key]).length));
  const error = $('#candidate-errors');
  error.hidden = !missing.length;
  error.textContent = missing.length ? '请补充：' + missing.map(key => labels[key]).join('、') : '';
  if (missing.length) {
    form.elements[missing[0]].focus();
    return false;
  }
  if (splitWords(data.skills).length > 100) {
    error.hidden = false;
    error.textContent = '技能最多填写 100 项。';
    form.elements.skills.focus();
    return false;
  }
  if (Number(data.salary_min) > Number(data.salary_max)) {
    error.hidden = false;
    error.textContent = '期望月薪下限不能超过上限。';
    return false;
  }
  return form.reportValidity();
}
async function saveCandidate(form) {
  if (!validateCandidate(form)) return;
  const values = formData(form);
  const profile = {...(state.profileDraft || state.candidate.profile)};
  for (const key of editableProfileKeys) profile[key] = values[key].trim();
  profile.skills = splitWords(values.skills);
  profile.experience_years = Number(values.experience_years);
  profile.salary_min = Number(values.salary_min);
  profile.salary_max = Number(values.salary_max);
  const revision = state.drafts.candidate?.revision ?? state.candidate.revision;
  state.candidate = await api('/candidate', {method: 'PUT', body: {revision, profile}});
  form.dataset.dirty = 'false';
  delete state.drafts.candidate;
  state.profileDraft = null;
  await render();
  toast('画像与求职条件已一起确认保存。');
}
async function resumeActivity() {
  if (!state.run) return;
  if (state.run.action === 'chat') {
    state.chatOpen = true;
    renderChat();
    return;
  }
  const jobId = runJob(state.run);
  if (jobId) state.expanded.add(jobId + '/' + state.run.action);
  await go(runPage(state.run) || 'jobs', {run: state.run.id, ...(jobId ? {job: jobId} : {})});
}
const actions = {
  'continue-images': async () => {
    const batch = state.modal.batch;
    closeDialog();
    await nextFile(batch);
  },
  'open-chat': () => { state.chatOpen = true; renderChat(); },
  'close-chat': () => { state.chatOpen = false; renderChat(); },
  'close-modal': dismissDialog,
  'resume-activity': resumeActivity,
  'discard-candidate': async () => {
    if (!confirm('放弃未保存的画像与条件，读取已保存资料？')) return;
    const form = $('#profile-form');
    if (form) form.dataset.dirty = 'false';
    delete state.drafts.candidate;
    state.profileDraft = null;
    await render();
  },
  'delete-candidate': async () => {
    if (!confirm('删除当前简历画像和求职条件，并清空本页未保存的输入？简历原件、岗位、记忆和历史报告会保留。')) return;
    // 使用当前页面的版本发起删除，其他页面更新过资料时由后端提示冲突。
    state.candidate = await api('/candidate?revision=' + state.candidate.revision, {method: 'DELETE'});
    // 删除成功后清掉本地草稿，避免 render 再把旧输入填回空画像。
    const form = $('#profile-form');
    if (form) form.dataset.dirty = 'false';
    delete state.drafts.candidate;
    state.profileDraft = null;
    await render();
    toast('画像和求职条件已删除，可以重新填写或从简历提取。');
  },
  'upload': element => chooseFiles(element.dataset.kind),
  'edit-job': element => editJob(element.dataset.id),
  'open-job': element => {
    state.expanded.add(element.dataset.id);
    return go('jobs', {job: element.dataset.id});
  },
  'jobs-prev': () => { state.offset = Math.max(0, state.offset - 20); return render(); },
  'jobs-next': () => { state.offset += 20; return render(); },
  'delete-job': async element => {
    if (active() && runJob(state.run) === element.dataset.id) throw new Error('请先结束这个岗位的当前活动。');
    if (!confirm('删除这个岗位？已保存的评估和面试报告仍会保留。')) return;
    await api('/jobs/' + encodeURIComponent(element.dataset.id), {method: 'DELETE'});
    if (state.jobs.length === 1 && state.offset > 0) state.offset -= 20;
    if (state.params.get('job') === element.dataset.id) await go('jobs');
    else await render();
  },
  'delete-evaluation': async element => {
    const {id, job} = element.dataset;
    if (!confirm('删除选中的这份岗位评估？岗位和其他报告仍会保留。')) return;
    await api('/reports/' + encodeURIComponent(id), {method: 'DELETE'});
    const next = jobReports(job).find(report => report.kind === 'evaluation' && report.id !== id);
    const key = job + '/evaluate';
    if (next) state.selectedReports[key] = next.id;
    else delete state.selectedReports[key];
    // 当前地址可能通过报告或运行 ID 指向刚删除的记录。
    if (state.params.get('report') === id || state.runs.get(state.params.get('run'))?.report_id === id) {
      await go('jobs', next ? {job, report: next.id} : {});
    } else {
      await render();
    }
    toast('已删除本次评估。');
  },
  'delete-document': async element => {
    if (!confirm('移除这份资料？历史报告仍会保留。')) return;
    await api('/documents/' + encodeURIComponent(element.dataset.id), {method: 'DELETE'});
    await render();
  },
  'parse-resume': element => begin('parse_resume', {document_id: element.dataset.id}),
  'sample-profile': () => begin('parse_resume'),
  'use-profile-draft': async () => {
    if (state.drafts.candidate && !confirm('用这份待确认草稿替换当前未保存的画像与条件？')) return;
    const data = state.modal.data;
    const batch = state.modal.batch;
    const form = $('#profile-form');
    if (form) form.dataset.dirty = 'false';
    delete state.drafts.candidate;
    state.profileDraft = data;
    const values = {...data, skills: (data.skills || []).join('，')};
    values.experience_years = data.experience_years ?? '';
    values.salary_min = data.salary_min ?? '';
    values.salary_max = data.salary_max ?? '';
    values.commute_mode = data.commute_mode ?? '';
    state.drafts.candidate = {values, revision: state.candidate?.revision ?? 0};
    cancelBatch(batch);
    closeDialog();
    await go('resume');
  },
  'review-result': element => reviewResult(state.runs.get(element.dataset.id)),
  'paste-jd': () => {
    state.modal = {kind: 'paste'};
    openDialog('粘贴招聘原文', `<form id="paste-form">${field('招聘原文', 'text', '', {area: true, required: true, rows: 9, maxLength: 50000})}</form>`, '<button form="paste-form" type="submit">提取岗位</button>');
  },
  'evaluate-job': element => begin('evaluate', {job_ids: [element.dataset.id]}, null, element.dataset.id),
  'interview-job': element => begin('interview', {job_id: element.dataset.id}, null, element.dataset.id),
  'run-decision': element => respond(element.dataset.id, element.dataset.decision),
  'add-memory': () => editMemory(),
  'edit-memory': element => editMemory(element.dataset.id),
  'delete-memory': async element => {
    if (!confirm('移除这条已确认记忆？')) return;
    await api('/memories/' + encodeURIComponent(element.dataset.id), {method: 'DELETE'});
    await render();
  },
};
document.addEventListener('click', async event => {
  const element = event.target.closest('[data-action]');
  if (!element || element.disabled) return;
  const action = actions[element.dataset.action];
  if (!action) return;
  event.preventDefault();
  element.disabled = true;
  try { await action(element); }
  catch (error) { showError(error); }
  finally { element.disabled = false; updateControls(); }
});
document.addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.target;
  if (form.dataset.submitting === 'true') return;
  form.dataset.submitting = 'true';
  const submitter = event.submitter;
  if (submitter) submitter.disabled = true;
  try {
    const data = formData(form);
    if (form.hasAttribute('data-places')) {
      await respond(form.dataset.runAnswer, 'answer', '', data);
    } else if (form.dataset.runAnswer) {
      if (!data.text?.trim()) throw new Error('请先填写回答。');
      await respond(form.dataset.runAnswer, 'answer', data.text);
    } else if (form.id === 'profile-form') {
      await saveCandidate(form);
    } else if (form.id === 'job-draft-form') {
      await saveJobDraft(form);
    } else if (form.id === 'job-search') {
      state.query = data.q;
      state.offset = 0;
      await go('jobs');
    } else if (form.id === 'job-form') {
      const item = state.modal.item;
      const job = readJob(form, item.data);
      const result = await api('/jobs/' + encodeURIComponent(item.id), {method: 'PUT', body: {revision: item.revision, data: job}});
      closeDialog();
      await go('jobs', {job: result.id});
      toast('岗位已确认保存。');
    } else if (form.id === 'memory-form') {
      const item = state.modal.item;
      await api('/memories' + (item.id ? '/' + encodeURIComponent(item.id) : ''), {method: item.id ? 'PUT' : 'POST', body: data});
      closeDialog();
      await render();
      toast('记忆已保存。');
    } else if (form.id === 'paste-form') {
      closeDialog();
      await begin('extract_jobs', {text: data.text});
    } else if (form.id === 'chat-form') {
      if (occupied()) throw new Error('请先结束当前活动。');
      if (!data.text?.trim()) return;
      state.messages.push({role: 'user', text: data.text, context: contextTitle()});
      state.chatDraft = '';
      renderChat();
      try { await begin('chat', {text: data.text}); }
      catch (error) {
        state.messages.push({role: 'assistant', text: error.message, context: '发送失败'});
        renderChat();
        throw error;
      }
    }
  } catch (error) { showError(error); }
  finally {
    form.dataset.submitting = 'false';
    if (submitter) submitter.disabled = false;
    updateControls();
  }
});
document.addEventListener('input', event => {
  if (event.target.id === 'chat-text') state.chatDraft = event.target.value;
  if (event.target.form) rememberDraft(event.target.form, true);
  if (event.target.closest('#editor') && state.modal) {
    state.modal.dirty = true;
  }
});
document.addEventListener('change', async event => {
  const node = event.target;
  if (node.id === 'scenario') state.scenario = node.value;

  if (node.dataset.skipDraft != null) {
    node.closest('section').querySelector('fieldset').disabled = node.checked;
  }
  if (node.form) rememberDraft(node.form, true);
  if (node.closest('#editor') && state.modal) state.modal.dirty = true;
  if (node.dataset.reportSelect) {
    const job = node.dataset.reportSelect;
    await go('jobs', {job, report: node.value});
  }
});
document.addEventListener('toggle', async event => {
  const node = event.target;
  if (node.dataset.sectionKey) node.open ? state.expanded.add(node.dataset.sectionKey) : state.expanded.delete(node.dataset.sectionKey);
  if (node.open && node.dataset.reportTrace && !node.dataset.loaded) {
    node.dataset.loaded = 'true';
    const body = node.querySelector('div');
    body.textContent = '正在读取执行记录…';
    try {
      const events = await api('/reports/' + encodeURIComponent(node.dataset.reportTrace) + '/events');
      node.querySelector('summary').textContent = `执行记录（${events.length}）`;
      body.innerHTML = traceEntries(events);
    } catch (error) {
      body.textContent = error.message;
      delete node.dataset.loaded;
    }
  }
}, true);
document.addEventListener('invalid', event => {
  if (event.target.form?.id !== 'profile-form') return;
  const missing = [...event.target.form.querySelectorAll(':invalid')].map(x => labels[x.name]).filter(Boolean);
  $('#candidate-errors').hidden = false;
  $('#candidate-errors').textContent = '请补充或修正：' + missing.join('、');
}, true);
$('#file-picker').addEventListener('change', event => { uploadFiles([...event.target.files]).catch(showError); });
$('#editor').addEventListener('cancel', event => { event.preventDefault(); dismissDialog(); });
window.addEventListener('hashchange', () => { render().catch(showError); });
window.addEventListener('beforeunload', event => {
  if (state.modal?.dirty || state.batch || Object.keys(state.drafts).length || state.chatDraft) {
    event.preventDefault();
    event.returnValue = '';
  }
});
async function bootstrap() {
  try {
    state.health = await api('/health');
    $('#mode-badge').textContent = isDemo() ? '模拟数据模式' : '真实接入模式';
    $('#mode-badge').className = 'badge ' + (isDemo() ? 'amber' : 'green');
    $('#mode-banner').hidden = !isDemo();
    $('#connection').className = 'connection ok';
    $('#connection span').textContent = '本机服务 · MySQL 已连接';
    const current = await api('/runs');
    if (current.active) {
      setRun(current.active);
      const jobId = runJob(current.active);
      if (jobId) state.expanded.add(jobId + '/' + current.active.action);
    }
    await render();
    if (state.run?.status === 'running') await poll();
    else if (state.run?.action === 'chat' && active()) { state.chatOpen = true; renderChat(); }
  } catch (error) {
    $('#connection').className = 'connection error';
    $('#connection span').textContent = '连接需要检查';
    $('#view').innerHTML = empty('工作台暂时无法连接', '请确认本机服务与数据库已经启动。');
    showError(error);
  }
}
bootstrap();
