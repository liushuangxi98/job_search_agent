import {api, esc, field, formData, empty, money, score, when} from './api.js';

const $ = selector => document.querySelector(selector);
const pages = {
  resume: ['简历画像', '01 / 个人经历与求职条件'],
  knowledge: ['面试题库', '02 / 积累自己的练习资料'],
  jobs: ['岗位池', '我的机会 / 逐个了解，认真选择'],
};
const labels = {
  name: '姓名', education: '学历', experience_years: '工作年限', skills: '技能',
  experience: '工作经历', projects: '项目经历', direction: '求职方向', city: '城市',
  commute_origin: '通勤起点', salary_min: '月薪下限', salary_max: '月薪上限',
  company: '公司', title: '职位', address: '办公地址', jd_text: '岗位描述', salary: '薪资',
};
const profileKeys = ['name', 'education', 'experience_years', 'skills', 'experience', 'projects'];
const preferenceKeys = ['direction', 'city', 'commute_origin', 'salary_min', 'salary_max'];
const jobKeys = ['company', 'title', 'city', 'address', 'jd_text', 'salary'];
const actionNames = {parse_resume: '简历整理', extract_jobs: '岗位提取', index_knowledge: '题库整理', evaluate: '岗位评估', interview: '模拟面试', chat: '聊天'};
const state = {
  page: 'jobs', params: new URLSearchParams(), health: null, generation: 0,
  candidate: null, profileDraft: null, drafts: {},
  jobs: [], reports: [], documents: [], query: '', offset: 0, expanded: new Set(),
  selectedReports: {}, openHistory: new Set(), run: null, runs: new Map(), timer: null,
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
function candidateFields(profile, preferences) {
  const required = {required: true};
  const salary = {required: true, type: 'number', max: 10000000};
  return `<div class="form-grid">
    ${field('姓名', 'name', profile.name, {...required, maxLength: 100})}
    ${field('学历', 'education', profile.education, {...required, maxLength: 2000})}
    ${field('工作年限', 'experience_years', profile.experience_years ?? '', {...required, type: 'number', min: 0, step: 'any', hint: '应届或暂无工作经验填 0'})}
    ${field('技能', 'skills', (profile.skills || []).join('，'), {...required, hint: '用逗号或换行分隔'})}
    ${field('工作经历', 'experience', profile.experience, {...required, area: true, full: true, maxLength: 20000, hint: '暂无工作经历请如实填写'})}
    ${field('项目经历', 'projects', profile.projects, {...required, area: true, full: true, maxLength: 20000})}
  </div><h3 class="form-section">求职条件</h3><div class="form-grid">
    ${field('求职方向', 'direction', preferences.direction, {...required, maxLength: 200})}
    ${field('期望城市', 'city', preferences.city, {...required, maxLength: 100})}
    ${field('通勤起点', 'commute_origin', preferences.commute_origin, {...required, full: true, maxLength: 300, hint: '城市及具体地址，用于查询公交与驾车路线'})}
    ${field('期望月薪下限（元）', 'salary_min', preferences.salary_min ?? '', salary)}
    ${field('期望月薪上限（元）', 'salary_max', preferences.salary_max ?? '', salary)}
  </div>`;
}
function diagnosisHTML(data) {
  return `<div class="tip">${esc(data.notice || '请人工核对；尚未确认保存。')}${(data.diagnosis || []).length ? `<ul>${data.diagnosis.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}</div>`;
}
async function resumePage() {
  const [candidate, docs, memories] = await Promise.all([api('/candidate'), api('/documents?kind=resume'), api('/memories')]);
  state.candidate = candidate;
  const data = state.profileDraft || candidate;
  const draft = state.drafts.candidate;
  const memoryBody = memories.items.map(x => `<div class="memory-row"><p>${esc(x.data.content)}<small class="prose">${esc(x.data.evidence)}</small></p>${button('编辑', 'edit-memory', idAttr(x.id), 'ghost small')}${button('移除', 'delete-memory', idAttr(x.id), 'ghost small')}</div>`).join('');
  return `<div class="page-intro"><div><h2>让经历与期待，都有清晰的表达</h2><p>整理简历和求职条件，核对后一起确认。所有标 * 项均为必填。</p></div><span class="badge ${candidate.confirmed && !draft && !state.profileDraft ? 'green' : 'amber'}">${draft || state.profileDraft ? '草稿待确认' : candidate.confirmed ? '已确认' : '待完善'}</span></div>
    <div data-page-run="resume"></div><div class="split"><div class="stack">
      ${card('简历资料', 'PDF / DOCX · 单文件不超过 16 MiB', uploadBox('resume', '上传简历', '原件会保留，提取结果需要人工确认') + fileRows(docs.items, 'parse-resume') + (isDemo() ? button('查看示例画像', 'sample-profile', 'data-start', 'ghost small') : ''))}
      ${card('已确认的记忆', '你可以查看、修改或移除', memoryBody || '<p class="caption">暂无已确认记忆。</p>', button('补充一条记忆', 'add-memory', '', 'ghost small'))}
    </div><article class="card"><header class="card-head"><div><h2>我的简历画像</h2><p>人工核对 · 薪资仅作资料展示</p></div><span class="badge">修订 ${candidate.revision}</span></header><div class="card-body">
      ${state.profileDraft ? diagnosisHTML(state.profileDraft) : ''}
      ${draft ? `<div class="tip">${draft.revision !== candidate.revision ? '已保存资料有更新；未保存输入已保留，直接保存会提示版本冲突。' : '未保存的输入已保留。'} ${button('读取已保存资料', 'discard-candidate', '', 'ghost small')}</div>` : ''}
      <form id="profile-form"><div id="candidate-errors" class="error-banner" role="alert" hidden></div>${candidateFields(data.profile, data.preferences)}<div class="form-actions"><button>保存并确认画像与条件</button></div></form>
      ${(data.profile.evidence || []).length ? `<details class="detail-section"><summary>原始提取依据</summary><pre class="prose">${esc(JSON.stringify(data.profile.evidence, null, 2))}</pre></details>` : ''}
    </div></article></div>`;
}
function commuteHTML(commutes = {}) {
  return `<div class="commutes">${[['public_transit', '公交'], ['driving', '驾车']].map(([key, label]) => {
    const route = commutes[key] || {};
    const duration = route.duration_minutes == null ? '时间未知' : `${esc(route.duration_minutes)} 分钟`;
    const distance = route.distance_km == null ? '距离未知' : `${esc(route.distance_km)} 公里`;
    return `<div class="commute"><span class="caption">${label} · 单程</span><strong>${duration} / ${distance}</strong><p>${esc(route.route_summary || '路线未知')}</p></div>`;
  }).join('')}</div>`;
}
function textList(title, values) {
  return values?.length ? `<h3>${esc(title)}</h3><ul>${values.map(x => `<li class="prose">${esc(x)}</li>`).join('')}</ul>` : '';
}
function reportContent(report, jobId) {
  const data = report.data;
  const header = `<div class="report-head"><div><h2>${esc(data.title || '报告')}</h2><small>${when(report.created_at)} · ${report.is_demo ? '固定模拟样例' : '已保存快照'}</small></div><div class="actions"><a href="/api/reports/${encodeURIComponent(report.id)}/export?format=md">导出 MD ↗</a><a href="/api/reports/${encodeURIComponent(report.id)}/export?format=json">JSON ↗</a></div></div><p class="prose">${esc(data.summary || '')}</p>`;
  if (report.kind === 'evaluation') {
    const row = (data.jobs || []).find(x => x.job_id === jobId);
    if (!row) return header + '<p class="caption">该历史报告没有对应岗位的匹配结果。</p>';
    const assessment = {matched: '符合', partial: '部分符合', gap: '差距', unknown: '未知'};
    return header + `<h3>${esc(row.company || '未披露公司')} · ${esc(row.title || '未披露职位')}</h3><div class="report-score">${score(row.match_score)}<small>匹配分 / 100</small></div>
      ${commuteHTML(row.commutes)}<p class="prose">${esc(row.recommendation || '暂无建议')}</p>
      ${(row.matches || []).length ? `<h3>逐项匹配依据</h3><div class="table-scroll"><table><thead><tr><th>岗位要求</th><th>个人证据</th><th>判断</th></tr></thead><tbody>${row.matches.map(x => `<tr><td>${esc(x.requirement)}</td><td>${esc(x.evidence || '未知')}</td><td>${esc(assessment[x.assessment] || '未知')}</td></tr>`).join('')}</tbody></table></div>` : '<p class="caption detail-section">本次报告未提供逐项匹配依据。</p>'}
      ${textList('优势', row.strengths)}${textList('差距', row.gaps)}${textList('信息缺口', row.missing)}
      ${(row.evidence || []).length ? `<details class="detail-section"><summary>原文证据与引用</summary><pre class="prose">${esc(JSON.stringify(row.evidence, null, 2))}</pre></details>` : ''}
      <details class="detail-section"><summary>本次使用的资料快照</summary><pre class="prose">${esc(JSON.stringify(report.snapshot || {}, null, 2))}</pre></details>`;
  }
  const dimensions = (data.dimensions || []).length
    ? `<h3>面试维度反馈</h3><dl class="job-meta interview-dimensions">${data.dimensions.map(x => `<dt>${esc(x.name)}</dt><dd>${x.score == null ? '未考察' : score(x.score)}</dd>`).join('')}</dl>` : '';
  return header + dimensions + textList('练习重点', data.weaknesses) + textList('推荐练习', data.practice) +
    `<details class="detail-section" open><summary>回看本次问答（${(data.answers || []).length} 轮）</summary><ol class="answers">${(data.answers || []).map(x => `<li><strong>${esc(x.question)}</strong><p>${esc(x.answer)}</p><small>${esc(x.source || '来源未知')}</small></li>`).join('')}</ol></details>`;
}
function historyHTML(jobId) {
  const reports = jobReports(jobId);
  if (!reports.length) return '<p class="caption detail-section">暂无评估或面试记录。</p>';
  const current = reports.find(x => x.id === state.selectedReports[jobId]) || reports[0];
  return `<details class="history" data-history="${esc(jobId)}" ${state.openHistory.has(jobId) ? 'open' : ''}><summary>评估与面试历史 · ${reports.length} 次</summary>
    <label class="history-controls"><span class="caption">查看记录</span><select data-report-select="${esc(jobId)}" aria-label="选择报告">${reports.map(x => `<option value="${esc(x.id)}" ${x.id === current.id ? 'selected' : ''}>${when(x.created_at)} · ${x.kind === 'evaluation' ? '评估' : '面试'} · ${esc(x.data.title || '报告')}${x.is_demo ? '（模拟）' : ''}</option>`).join('')}</select></label><article class="report" data-report-body="${esc(jobId)}">${reportContent(current, jobId)}</article></details>`;
}
function jobRow(item) {
  const data = item.data;
  const reports = jobReports(item.id);
  const latest = reports.find(x => x.kind === 'evaluation');
  const result = latest?.data.jobs?.find(x => x.job_id === item.id);
  const blocked = !state.candidate?.confirmed || !item.ready;
  const missing = (item.missing || []).map(x => x.label).join('、');
  const sources = state.documents.filter(x => (data.source_ids || []).includes(x.id));
  return `<article class="job-row" data-job-row="${esc(item.id)}"><div class="job-overview">
    <div><h3>${button(esc(data.title || '未披露职位'), 'open-job', idAttr(item.id), 'job-title')}</h3><small>${esc(data.company || '未披露公司')} · ${esc(data.city || '城市未知')}</small></div>
    <div>${money(data.salary_min)} — ${money(data.salary_max)}<small>月薪${data.salary_note ? ' · ' + esc(data.salary_note) : ''}</small></div>
    <div>${result ? `<span class="job-score">${score(result.match_score)}<small>最近匹配分${latest.is_demo ? ' · 模拟' : ''}</small></span>` : `<span class="badge ${item.ready ? '' : 'amber'}">${item.ready ? '待评估' : '待完善'}</span><small>${item.ready ? '尚无匹配结果' : esc(missing)}</small>`}</div>
    <div class="actions">${button('评估', 'evaluate-job', `${idAttr(item.id)} data-start data-blocked="${blocked}"`, 'secondary small')}${button('模拟面试', 'interview-job', `${idAttr(item.id)} data-start data-blocked="${blocked}"`, 'small')}</div>
    </div><details class="job-details" data-job-details="${esc(item.id)}" ${state.expanded.has(item.id) ? 'open' : ''}><summary>岗位详情、评估与面试记录${reports.length ? `（${reports.length}）` : ''}</summary>
      ${!item.ready ? `<div class="tip">待完善：${esc(missing)}。逐项补充或人工确认盲区后才能开始。${button('完善岗位', 'edit-job', idAttr(item.id), 'ghost small')}</div>` : ''}
      <div data-job-run="${esc(item.id)}"></div>
      <dl class="job-meta"><dt>办公地址</dt><dd>${esc(data.address || '未知')}</dd><dt>薪资说明</dt><dd>${esc(data.salary_note || '未提供')}</dd></dl>
      <h3>招聘原文</h3><p class="prose">${esc(data.jd_text || '未知')}</p>
      ${Object.entries(data.blind_spots || {}).map(([key, value]) => `<p class="blind-note">${esc(labels[key] || key)} · 招聘方未披露：${esc(value)}</p>`).join('')}
      ${data.notes ? `<section class="detail-section"><h3>我的备注</h3><p class="prose">${esc(data.notes)}</p></section>` : ''}
      <section class="detail-section"><h3>原始资料</h3>${sources.length ? sources.map(x => `<p><a href="/api/documents/${encodeURIComponent(x.id)}/content" target="_blank" rel="noopener">${esc(x.filename)} ↗</a></p>`).join('') : '<p class="caption">手动记录或固定模拟资料。</p>'}</section>
      <div class="actions detail-section">${button('编辑岗位', 'edit-job', idAttr(item.id), 'secondary small')}${button('移除岗位', 'delete-job', idAttr(item.id), 'ghost small')}</div>
      ${historyHTML(item.id)}</details></article>`;
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
  return `<div class="page-intro"><div><h2>值得了解的下一站</h2><p>每个岗位独立查看匹配依据、通勤路线与面试记录。</p></div>${button('＋ 添加岗位', 'new-job')}</div>
    ${!candidate.confirmed ? '<div class="tip">请先完善并确认个人资料，才能评估或面试。<a href="#/resume">前往简历画像 →</a></div>' : ''}
    <div class="toolbar"><form id="job-search" class="search"><input name="q" aria-label="搜索岗位" placeholder="搜索公司、职位或城市" value="${esc(state.query)}"><button class="secondary">搜索</button></form><div class="actions">${button('导入截图', 'upload', 'data-kind="image" data-start', 'secondary')}${button('粘贴招聘原文', 'paste-jd', 'data-start', 'secondary')}${isDemo() ? button('查看示例岗位', 'sample-jobs', 'data-start', 'ghost') : ''}</div></div>
    ${missingJob ? `<div class="tip">关联岗位已移除或不存在。${linkedReport ? '下方保留当时的报告与资料快照。' : '请从岗位池重新选择。'}</div>${linkedReport ? `<article class="report">${reportContent(linkedReport, selected)}</article>` : ''}` : ''}
    <div data-page-run="jobs"></div><div class="job-list">${state.jobs.length ? state.jobs.map(jobRow).join('') : card('岗位池', '从一个感兴趣的岗位开始', empty('还没有添加岗位', '导入招聘截图、粘贴原文，或手动填写。', button('手动添加岗位', 'new-job', '', 'secondary')))}</div>
    <div class="table-footer"><span>共 ${jobs.total} 个岗位</span><div class="actions">${button('上一页', 'jobs-prev', state.offset === 0 ? 'disabled' : '', 'ghost small')}<span>${Math.floor(state.offset / 20) + 1}</span>${button('下一页', 'jobs-next', state.offset + 20 >= jobs.total ? 'disabled' : '', 'ghost small')}</div></div>
    ${docs.items.length ? `<details class="detail-section"><summary>已上传的招聘截图 · ${docs.items.length} 份</summary>${fileRows(docs.items, 'extract-image')}</details>` : ''}`;
}
async function knowledgePage() {
  const [docs, questions] = await Promise.all([api('/documents?kind=knowledge'), api('/knowledge/questions?limit=100')]);
  return `<div class="page-intro"><div><h2>为下一次交流，积累一点准备</h2><p>上传 Markdown 题库，也可以手动整理题目与答案。</p></div>${button('＋ 手动添加题目', 'new-question', '', 'secondary')}</div><div data-page-run="knowledge"></div>
    <div class="split"><div class="stack">${card('知识资料', 'UTF-8 Markdown · 单文件不超过 16 MiB', uploadBox('knowledge', '导入 MD 题库', '逐份上传、整理和核对') + fileRows(docs.items, 'index-knowledge'))}<div class="tip">原文保存后仍需整理题目。固定模拟样例不解析你的文件，也不建立向量索引。</div></div>
    <article class="card"><header class="card-head"><div><h2>我的题目</h2><p>共 ${questions.total} 道 · 展示最近 100 条</p></div></header>${questions.items.length ? questions.items.map(x => `<section class="question-item"><header><h3>${esc(x.data.question)}</h3><div class="actions">${button('编辑', 'edit-question', idAttr(x.id), 'ghost small')}${button('移除', 'delete-question', idAttr(x.id), 'ghost small')}</div></header><div class="chip-list">${(x.data.tags || []).map(tag => `<span class="chip">${esc(tag)}</span>`).join('')}</div><details><summary>参考答案与来源</summary><p>${esc(x.data.answer || '未提供参考答案')}</p><small>${esc(x.data.locator || '人工录入')}</small>${x.data.document_id ? `<a href="/api/documents/${encodeURIComponent(x.data.document_id)}/content" target="_blank" rel="noopener"> 原文 ↗</a>` : ''}</details></section>`).join('') : empty('题库还是空的', '从一道题开始，留下答案与来源。')}</article></div>`;
}

function pendingHTML(run) {
  const pending = run.pending_input;
  if (!pending) return '';
  if (pending.kind === 'proposal') {
    return `<section class="pending"><h3>请确认这次修改</h3><p>${esc(pending.prompt)}</p><pre class="prose">${esc(JSON.stringify(pending.proposal?.changes || {}, null, 2))}</pre><div class="actions">${button('拒绝修改', 'run-decision', `${idAttr(run.id)} data-decision="reject"`, 'secondary')}${button('确认并保存', 'run-decision', `${idAttr(run.id)} data-decision="confirm"`)}</div></section>`;
  }
  const interview = run.action === 'interview';
  const data = run.result?.data || {};
  return `<section class="pending">${interview ? `<span class="badge green">第 ${esc(data.round || 1)} / 最多 8 轮</span><h3 class="interview-question">${esc(data.question || pending.prompt)}</h3><p class="caption">来源：${esc(data.source || '未知')}</p>` : `<h3>需要你补充一点信息</h3><p>${esc(pending.prompt)}</p>`}
    <form data-run-answer="${esc(run.id)}" data-input-id="${esc(pending.id)}"><textarea name="text" required maxlength="20000" rows="4" aria-label="${interview ? '面试回答' : '补充信息'}" placeholder="${interview ? '写下你的思路，可以结合经历说明。' : '填写补充内容'}"></textarea><div class="actions">${button(interview ? '提前结束并生成报告' : '结束本次', 'run-decision', `${idAttr(run.id)} data-decision="finish"`, 'secondary')}<button>${interview ? '提交回答' : '提交并继续'}</button></div></form></section>`;
}
function runHTML(run) {
  const status = {running: '运行中', needs_input: '等待补充', completed: '已完成', partial: '部分结果', failed: '失败'}[run.status];
  const reviewable = ['profile_draft', 'job_drafts', 'knowledge_result'].includes(run.result?.kind);
  return `<section class="run-panel"><div class="run-head"><h3>${esc(actionNames[run.action] || '当前活动')}</h3><span class="badge ${run.status === 'failed' ? 'red' : run.status === 'needs_input' ? 'amber' : 'green'}">${status}</span></div>
    ${run.is_demo ? '<p class="caption">固定模拟样例 · 未调用模型或真实工具</p>' : ''}
    ${run.status === 'running' ? '<p class="caption">正在处理，请稍候。当前活动结束前不能开始另一项活动。</p>' : ''}
    ${run.status === 'failed' ? `<div class="error-banner">${esc(run.result?.data?.message || '处理失败，请人工整理资料。')}</div>` : pendingHTML(run)}
    ${reviewable && !['running', 'needs_input', 'failed'].includes(run.status) ? button('核对处理结果', 'review-result', idAttr(run.id), 'secondary small') : ''}
    <details class="trace"><summary>执行记录（${(run.events || []).length}）</summary>${(run.events || []).map(x => `<div class="trace-item"><small>${when(x.at)}</small><div>${esc(x.message)}${Object.keys(x.data || {}).length ? `<pre>${esc(JSON.stringify(x.data, null, 2))}</pre>` : ''}</div></div>`).join('')}</details></section>`;
}
function runPage(run) {
  return {parse_resume: 'resume', extract_jobs: 'jobs', index_knowledge: 'knowledge', evaluate: 'jobs', interview: 'jobs'}[run.action];
}
function updateRunPanels() {
  // 轮询只更新所属流程；输入通过 pending_input.id 保存，历史运行不会抢占活动。
  const hosts = [...document.querySelectorAll('[data-job-run], [data-page-run]')];
  for (const host of hosts) {
    const id = host.dataset.jobRun;
    const candidates = [...state.runs.values()].filter(run => id
      ? runJob(run) === id && ['evaluate', 'interview'].includes(run.action)
      : !runJob(run) && runPage(run) === host.dataset.pageRun);
    const run = candidates.find(x => x.id === state.run?.id && active()) || candidates.at(-1);
    const signature = run ? JSON.stringify([run.id, run.status, run.next_seq, run.pending_input, run.result]) : '';
    if (host.dataset.signature === signature) continue;
    host.querySelectorAll('form').forEach(form => rememberDraft(form));
    host.innerHTML = run ? runHTML(run) : '';
    host.dataset.signature = signature;
    restoreDrafts(host);
  }
  updateControls();
}
async function resolveRoute() {
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
    if (runJob(run)) state.params.set('job', runJob(run));
    if (run?.report_id && !state.params.get('report')) state.params.set('report', run.report_id);
  }
  const reportId = state.params.get('report');
  if (reportId) {
    try {
      const report = await api('/reports/' + encodeURIComponent(reportId));
      const jobId = state.params.get('job') || reportJobs(report)[0];
      if (jobId) {
        state.params.set('job', jobId);
        state.selectedReports[jobId] = reportId;
        state.openHistory.add(jobId);
      }
    } catch (error) { if (error.status !== 404) throw error; toast(error.message, true); state.params.delete('report'); }
  }
  if (state.params.get('job')) state.expanded.add(state.params.get('job'));
  const suffix = state.params.toString();
  const hash = `#/${state.page}${suffix ? '?' + suffix : ''}`;
  if (location.hash !== hash) history.replaceState(null, '', hash);
}
async function render() {
  const generation = ++state.generation;
  await resolveRoute();
  const renderer = {resume: resumePage, jobs: jobsPage, knowledge: knowledgePage}[state.page];
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
      state.expanded.add(jobId);
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
    cancelBatch(state.runBatches.get(run.id));
    showError(new Error(result.data?.message || '运行失败，请手动整理资料。'));
    return;
  }
  if (run.action === 'chat' && result.kind === 'chat') {
    state.messages.push({role: 'assistant', text: result.data.message || '', context: run.is_demo ? '固定模拟回复' : 'Agent 回复'});
    renderChat();
  }
  if (['profile_draft', 'job_drafts', 'knowledge_result'].includes(result.kind) && !$('#editor').open) reviewResult(run);
  if (run.report_id) {
    state.reports = (await api('/reports')).items;
    const jobId = runJob(run) || reportJobs(state.reports.find(x => x.id === run.report_id) || {})[0];
    if (jobId) {
      state.selectedReports[jobId] = run.report_id;
      state.openHistory.add(jobId);
      state.expanded.add(jobId);
    }
    if (state.page === 'jobs') await render();
    toast('本次结果已保存，可在岗位行内回看。');
  }
  updateControls();
}
async function respond(runId, decision, text = '') {
  const run = state.runs.get(runId);
  const pending = run?.pending_input;
  if (!pending) throw new Error('该输入已处理，请查看当前状态。');
  if (state.responding.has(pending.id)) return;
  state.responding.add(pending.id);
  document.querySelectorAll(`[data-run-answer="${CSS.escape(runId)}"] button`).forEach(x => { x.disabled = true; });
  if (decision === 'finish') cancelBatch(state.runBatches.get(runId));
  try {
    const response = await api(`/runs/${encodeURIComponent(runId)}/input`, {method: 'POST', body: {input_id: pending.id, decision, text}});
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
  updateBlindOptions();
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
function jobFields(data, confirmedBlindSpots = false) {
  const controls = {
    company: field('公司名称', 'company', data.company, {maxLength: 200}),
    title: field('职位名称', 'title', data.title, {maxLength: 200}),
    city: field('工作城市', 'city', data.city, {maxLength: 100}),
    address: field('办公地址', 'address', data.address, {maxLength: 300}),
    jd_text: field('岗位描述 JD', 'jd_text', data.jd_text, {area: true, rows: 5, maxLength: 50000}),
    salary: `<div class="form-grid">${field('月薪下限（元）', 'salary_min', data.salary_min ?? '', {type: 'number', max: 10000000})}${field('月薪上限（元）', 'salary_max', data.salary_max ?? '', {type: 'number', max: 10000000})}</div>`,
  };
  return jobKeys.map(key => {
    const reason = data.blind_spots?.[key] || '';
    // Agent 草稿中的盲区是建议；新导入时必须由用户逐项勾选。
    const checked = confirmedBlindSpots && !!reason;
    return `<div class="blind-field ${['jd_text', 'salary'].includes(key) ? 'full' : ''}" data-blind-field="${key}">${controls[key]}<label class="blind-option"><input type="checkbox" data-blind="${key}" ${checked ? 'checked' : ''}> ${esc(labels[key])}：招聘方未披露，已人工核对</label><label class="field" data-blind-reason="${key}" ${checked || reason ? '' : 'hidden'}><span>盲区说明（选择未披露时必填）</span><textarea name="blind_${key}" rows="2" maxlength="2000">${esc(reason)}</textarea></label></div>`;
  }).join('') + field('薪资补充说明', 'salary_note', data.salary_note, {full: true, maxLength: 500}) + field('我的备注', 'notes', data.notes, {area: true, rows: 2, full: true, maxLength: 5000});
}
function updateBlindOptions() {
  for (const section of $('#editor').querySelectorAll('[data-blind-field]')) {
    const key = section.dataset.blindField;
    const names = key === 'salary' ? ['salary_min', 'salary_max'] : [key];
    const known = names.every(name => section.querySelector(`[name="${name}"]`).value.trim());
    const checkbox = section.querySelector('[data-blind]');
    const reason = section.querySelector(`[name="blind_${key}"]`).value.trim();
    section.querySelector('.blind-option').hidden = known && !checkbox.checked && !reason;
  }
}
function readJob(root, original = {}) {
  const values = Object.fromEntries([...root.querySelectorAll('[name]')].map(x => [x.name, x.value.trim()]));
  const data = {...original, blind_spots: {}};
  for (const key of ['company', 'title', 'city', 'address', 'jd_text', 'salary_note', 'notes']) data[key] = values[key] || '';
  for (const key of ['salary_min', 'salary_max']) data[key] = values[key] ? Number(values[key]) : null;
  for (const key of jobKeys) {
    const checked = root.querySelector(`[data-blind="${key}"]`).checked;
    const known = key === 'salary' ? data.salary_min != null && data.salary_max != null : !!data[key];
    if (checked) {
      if (!values['blind_' + key]) throw new Error(`请填写${labels[key]}的盲区说明。`);
      data.blind_spots[key] = values['blind_' + key];
    } else if (!known) throw new Error(`请补充${labels[key]}，或逐项勾选“招聘方未披露”并说明原因。`);
  }
  for (const key of ['salary_min', 'salary_max']) {
    if (data[key] != null && (!Number.isInteger(data[key]) || data[key] < 1 || data[key] > 10000000)) throw new Error('月薪应为 1—10000000 的整数；未知请留空并确认盲区。');
  }
  if (data.salary_min != null && data.salary_max != null && data.salary_min > data.salary_max) throw new Error('薪资下限不能超过上限。');
  return data;
}
function reviewResult(run) {
  const data = run.result?.data || {};
  const kind = run.result?.kind;
  if (kind === 'profile_draft') {
    state.modal = {kind, data, batch: state.runBatches.get(run.id)};
    openDialog('核对简历与条件草稿', diagnosisHTML(data) + `<dl class="job-meta">${[...profileKeys, ...preferenceKeys].map(key => {
      const value = profileKeys.includes(key) ? data.profile[key] : data.preferences[key];
      return `<dt>${esc(labels[key])}</dt><dd class="prose">${esc(Array.isArray(value) ? value.join('、') : value ?? '待补充')}</dd>`;
    }).join('')}</dl>`, button('填入画像表单', 'use-profile-draft'));
  } else if (kind === 'job_drafts') {
    let batch = state.runBatches.get(run.id);
    if (batch?.cancelled) { toast('该批次已结束，请重新提取需要的资料。'); return; }
    batch ||= newBatch();
    state.runBatches.set(run.id, batch);
    state.modal = {kind, data: data.jobs || [], batch};
    openDialog('确认待导入岗位', diagnosisHTML(data) + `<p class="caption">逐项核对资料；缺失字段需补充，或明确确认招聘方未披露。</p><form id="job-drafts-form">${(data.jobs || []).map((job, index) => `<section class="draft-job"><header><h3>岗位 ${index + 1}</h3><label class="caption"><input type="checkbox" data-skip-draft="${index}"> 跳过此条</label></header><fieldset data-draft-index="${index}"><div class="form-grid">${jobFields(job)}</div></fieldset></section>`).join('')}</form>`, '<button form="job-drafts-form" type="submit">确认入库</button>');
  } else if (kind === 'knowledge_result') {
    state.modal = {kind, data: data.questions || [], batch: state.runBatches.get(run.id)};
    openDialog('核对题库处理结果', diagnosisHTML(data) + (data.questions || []).map(q => `<section class="detail-section"><h3>${esc(q.question)}</h3><p class="prose">${esc(q.answer)}</p><small>${esc(q.locator || '')}</small></section>`).join(''), button('确认保存题目', 'save-questions'));
  }
  updateControls();
}
async function editJob(id = null) {
  const item = id ? await api('/jobs/' + encodeURIComponent(id)) : {data: {source_ids: []}};
  const docs = await api('/documents');
  state.modal = {kind: 'job', item};
  openDialog(id ? '编辑岗位' : '添加岗位 · 人工核对', `<form id="job-form"><div class="tip">每项资料均须填写，或人工确认“招聘方未披露”并说明。原文和已知信息会一并保留。</div><div class="form-grid">${jobFields(item.data, !!id)}<label class="field full"><span>关联原始资料（可多选）</span><select name="source_ids" multiple size="3">${docs.items.map(x => `<option value="${esc(x.id)}" ${(item.data.source_ids || []).includes(x.id) ? 'selected' : ''}>${esc(x.filename)}</option>`).join('')}</select></label></div></form>`, '<button type="submit" form="job-form">保存岗位</button>');
}
async function editQuestion(id = null) {
  const response = id ? await api('/knowledge/questions?limit=500') : null;
  const item = id ? response.items.find(x => x.id === id) : {data: {}};
  if (!item) throw new Error('题目不存在，请刷新。');
  state.modal = {kind: 'question', item};
  openDialog(id ? '编辑题目' : '手动添加题目', `<form id="question-form"><div class="form-grid">${field('题目', 'question', item.data.question, {area: true, full: true, required: true, maxLength: 10000})}${field('参考答案', 'answer', item.data.answer, {area: true, full: true, rows: 5, maxLength: 30000})}${field('知识点', 'tags', (item.data.tags || []).join('，'), {full: true})}${field('来源位置 / 说明', 'locator', item.data.locator, {full: true, maxLength: 500})}</div></form>`, '<button form="question-form" type="submit">保存题目</button>');
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
  picker.accept = {resume: '.pdf,.docx', image: '.png,.jpg,.jpeg,.webp', knowledge: '.md'}[kind];
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
    const limit = (batch.kind === 'image' ? 8 : 16) * 1024 * 1024;
    if (file.size > limit) throw new Error(`${file.name} 超过 ${limit / 1024 / 1024} MiB 上限，已停止本批次。`);
    const body = new FormData();
    body.append('kind', batch.kind);
    body.append('file', file);
    const document = await api('/documents', {method: 'POST', body});
    if (batch.cancelled) return;
    await render();
    const action = {resume: 'parse_resume', image: 'extract_jobs', knowledge: 'index_knowledge'}[batch.kind];
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
async function saveJobDrafts(form) {
  const operation = state.modal;
  if (operation?.kind !== 'job_drafts' || operation.batch.cancelled || operation.batch.saving) return;
  const batch = operation.batch;
  // 在发送第一条请求之前，先核对全部保留的草稿。
  const entries = [...form.querySelectorAll('[data-draft-index]')].filter(x => !x.disabled)
    .map(section => ({section, data: readJob(section, operation.data[Number(section.dataset.draftIndex)])}));
  batch.saving = true;
  form.inert = true;
  try {
    for (const entry of entries) {
      if (batch.cancelled) break;
      await api('/jobs', {method: 'POST', body: entry.data});
      batch.saved++;
      entry.section.disabled = true;
      const skip = entry.section.closest('section').querySelector('[data-skip-draft]');
      skip.disabled = true;
      skip.checked = true;
    }
    if (batch.cancelled) {
      await render();
      toast(`已停止后续导入，本批已保存或复用 ${batch.saved} 条岗位。`);
      return;
    }
    if (state.modal === operation) closeDialog();
    await render();
    toast(entries.length ? '岗位已入库。' : '已跳过当前条目。');
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
async function saveQuestions() {
  const operation = state.modal;
  if (operation?.saving) return;
  operation.saving = true;
  operation.saved ||= 0;
  try {
    for (; operation.saved < operation.data.length; operation.saved++) {
      if (state.modal !== operation || operation.batch?.cancelled) return;
      await api('/knowledge/questions', {method: 'POST', body: operation.data[operation.saved]});
    }
    closeDialog();
    await render();
    toast('题目已确认保存。');
    await nextFile(operation.batch);
  } catch (error) {
    if (error.status === 413) { cancelBatch(operation.batch); closeDialog(); }
    throw error;
  } finally { operation.saving = false; }
}
function validateCandidate(form) {
  const data = formData(form);
  const missing = [...profileKeys, ...preferenceKeys].filter(key => !data[key]?.trim() || (key === 'skills' && !splitWords(data[key]).length));
  const error = $('#candidate-errors');
  error.hidden = !missing.length;
  error.textContent = missing.length ? '请补充：' + missing.map(key => labels[key]).join('、') : '';
  if (missing.length) {
    form.elements[missing[0]].focus();
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
  const source = state.profileDraft || state.candidate;
  const profile = {...source.profile};
  const preferences = {...source.preferences};
  for (const key of profileKeys) profile[key] = values[key].trim();
  profile.skills = splitWords(values.skills);
  profile.experience_years = Number(values.experience_years);
  for (const key of preferenceKeys) preferences[key] = values[key].trim();
  preferences.salary_min = Number(values.salary_min);
  preferences.salary_max = Number(values.salary_max);
  const revision = state.drafts.candidate?.revision ?? state.candidate.revision;
  state.candidate = await api('/candidate', {method: 'PUT', body: {revision, profile, preferences}});
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
  await go(runPage(state.run) || 'jobs', {run: state.run.id, ...(jobId ? {job: jobId} : {})});
}
const actions = {
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
  'upload': element => chooseFiles(element.dataset.kind),
  'new-job': () => editJob(),
  'edit-job': element => editJob(element.dataset.id),
  'open-job': element => go('jobs', {job: element.dataset.id}),
  'jobs-prev': () => { state.offset = Math.max(0, state.offset - 20); return render(); },
  'jobs-next': () => { state.offset += 20; return render(); },
  'delete-job': async element => {
    if (active() && runJob(state.run) === element.dataset.id) throw new Error('请先结束这个岗位的当前活动。');
    if (!confirm('移除这个岗位？已保存的历史报告仍会保留。')) return;
    await api('/jobs/' + encodeURIComponent(element.dataset.id), {method: 'DELETE'});
    if (state.params.get('job') === element.dataset.id) await go('jobs');
    else await render();
  },
  'delete-document': async element => {
    if (!confirm('移除这份资料和关联题目元数据？历史报告仍会保留。')) return;
    await api('/documents/' + encodeURIComponent(element.dataset.id), {method: 'DELETE'});
    await render();
  },
  'parse-resume': element => begin('parse_resume', {document_id: element.dataset.id}),
  'extract-image': element => begin('extract_jobs', {document_id: element.dataset.id}),
  'index-knowledge': element => begin('index_knowledge', {document_id: element.dataset.id}),
  'sample-profile': () => begin('parse_resume'),
  'sample-jobs': () => begin('extract_jobs', {text: '固定模拟样例'}),
  'use-profile-draft': async () => {
    if (state.drafts.candidate && !confirm('用这份待确认草稿替换当前未保存的画像与条件？')) return;
    const data = state.modal.data;
    const batch = state.modal.batch;
    const form = $('#profile-form');
    if (form) form.dataset.dirty = 'false';
    delete state.drafts.candidate;
    state.profileDraft = data;
    const values = {...data.profile, ...data.preferences, skills: (data.profile.skills || []).join('，')};
    values.experience_years = data.profile.experience_years ?? '';
    values.salary_min = data.preferences.salary_min ?? '';
    values.salary_max = data.preferences.salary_max ?? '';
    state.drafts.candidate = {values, revision: state.candidate?.revision ?? 0};
    cancelBatch(batch);
    closeDialog();
    await go('resume');
  },
  'review-result': element => reviewResult(state.runs.get(element.dataset.id)),
  'save-questions': saveQuestions,
  'paste-jd': () => {
    state.modal = {kind: 'paste'};
    openDialog('粘贴招聘原文', `<form id="paste-form">${field('招聘原文', 'text', '', {area: true, required: true, rows: 9, maxLength: 50000})}</form>`, '<button form="paste-form" type="submit">提取岗位</button>');
  },
  'evaluate-job': element => begin('evaluate', {job_ids: [element.dataset.id]}, null, element.dataset.id),
  'interview-job': element => begin('interview', {job_id: element.dataset.id}, null, element.dataset.id),
  'run-decision': element => respond(element.dataset.id, element.dataset.decision),
  'new-question': () => editQuestion(),
  'edit-question': element => editQuestion(element.dataset.id),
  'delete-question': async element => {
    if (!confirm('移除这道题？')) return;
    await api('/knowledge/questions/' + encodeURIComponent(element.dataset.id), {method: 'DELETE'});
    await render();
  },
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
    if (form.dataset.runAnswer) {
      if (!data.text?.trim()) throw new Error('请先填写回答。');
      await respond(form.dataset.runAnswer, 'answer', data.text);
    } else if (form.id === 'profile-form') {
      await saveCandidate(form);
    } else if (form.id === 'job-drafts-form') {
      await saveJobDrafts(form);
    } else if (form.id === 'job-search') {
      state.query = data.q;
      state.offset = 0;
      await go('jobs');
    } else if (form.id === 'job-form') {
      const item = state.modal.item;
      const job = readJob(form, item.data);
      job.source_ids = new FormData(form).getAll('source_ids');
      const result = await api('/jobs' + (item.id ? '/' + encodeURIComponent(item.id) : ''), {method: item.id ? 'PUT' : 'POST', body: item.id ? {revision: item.revision, data: job} : job});
      closeDialog();
      await go('jobs', {job: result.id});
      toast('岗位已确认保存。');
    } else if (form.id === 'question-form') {
      const item = state.modal.item;
      data.tags = splitWords(data.tags);
      data.document_id = item.data.document_id || null;
      await api('/knowledge/questions' + (item.id ? '/' + encodeURIComponent(item.id) : ''), {method: item.id ? 'PUT' : 'POST', body: data});
      closeDialog();
      await render();
      toast('题目已保存。');
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
    updateBlindOptions();
  }
});
document.addEventListener('change', async event => {
  const node = event.target;
  if (node.id === 'scenario') state.scenario = node.value;
  if (node.dataset.blind) {
    node.closest('[data-blind-field]').querySelector('[data-blind-reason]').hidden = !node.checked;
  }
  if (node.dataset.skipDraft != null) {
    node.closest('section').querySelector('fieldset').disabled = node.checked;
  }
  if (node.form) rememberDraft(node.form, true);
  if (node.closest('#editor') && state.modal) state.modal.dirty = true;
  if (node.dataset.reportSelect) {
    const job = node.dataset.reportSelect;
    state.selectedReports[job] = node.value;
    state.openHistory.add(job);
    await go('jobs', {job, report: node.value});
  }
});
document.addEventListener('toggle', event => {
  const node = event.target;
  if (node.dataset.jobDetails) node.open ? state.expanded.add(node.dataset.jobDetails) : state.expanded.delete(node.dataset.jobDetails);
  if (node.dataset.history) node.open ? state.openHistory.add(node.dataset.history) : state.openHistory.delete(node.dataset.history);
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
      if (jobId) state.expanded.add(jobId);
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
