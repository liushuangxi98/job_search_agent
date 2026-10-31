import {api, esc, field, formData, empty, money, score, when} from './api.js';

const $ = selector => document.querySelector(selector);
const pages = {
  resume: ['简历画像', '第一步 · 让 Agent 了解你的经历'],
  preferences: ['求职条件', '第二步 · 设定你的下一站'],
  jobs: ['岗位池', '第三步 · 汇集值得考虑的机会'],
  evaluate: ['岗位评估', '第四步 · 查看依据与执行过程'],
  reports: ['决策报告', '第五步 · 把信息变成清晰的选择'],
  knowledge: ['面试题库', '持续准备 · 积累可追溯的知识'],
  interview: ['模拟面试', '专注回答 · 结束后统一反馈'],
};
const labels = {name:'姓名', headline:'当前方向', summary:'个人简介', skills:'技能', experience:'经历', education:'教育背景', city:'城市', direction:'岗位方向', salary_min:'月薪下限', salary_max:'月薪上限', commute_origin:'通勤起点', commute_mode:'出行方式', commute_limit_minutes:'单程时间上限', address:'办公地址', jd_text:'岗位描述', notes:'备注'};
const state = {page:'resume', params:new URLSearchParams(), health:null, generation:0, selected:new Set(), query:'', offset:0,
  run:null, runCache:new Map(), lastRuns:{}, timer:null, pollError:false, processed:new Set(), shownMessages:new Set(), profile:null, preferences:null,
  currentJob:null, currentReport:null, modal:null, profileDraft:null, formDrafts:{}, importBatch:null, runBatches:new Map(), refreshAfterRun:new Set(), uploadKind:null,
  chatOpen:false, messages:[], chatDraft:'', sessionId:crypto.randomUUID(), scenario:'success', nextJob:null};
let toastTimer;

function toast(message, error=false) {
  const node = $('#toast'); node.textContent = message; node.hidden = false; node.classList.toggle('error', error);
  clearTimeout(toastTimer); toastTimer = setTimeout(() => node.hidden = true, error ? 7000 : 3500);
}
function showError(error) {
  toast(error.message || '操作失败', true);
  if (error.code === 'DATABASE_UNAVAILABLE') {
    $('#global-error').textContent = error.message; $('#global-error').hidden = false;
  }
}
function go(page, params={}) {
  const query = new URLSearchParams(params).toString();
  const hash = `#/${page}${query ? '?' + query : ''}`;
  if (location.hash === hash) render().catch(showError); else location.hash = hash;
}
const busy = () => state.run && ['running','needs_input'].includes(state.run.status);
const button = (text, action, attributes='', cls='') => `<button type="button" data-action="${action}" class="${cls}" ${attributes}>${text}</button>`;
const card = (title, subtitle, body, footer='') => `<article class="card"><header class="card-head"><div><h2>${title}</h2>${subtitle ? `<p>${subtitle}</p>` : ''}</div></header><div class="card-body">${body}</div>${footer ? `<footer class="card-footer">${footer}</footer>` : ''}</article>`;
const statusLabel = status => ({running:'运行中',needs_input:'等待补充',completed:'已完成',partial:'部分结果',failed:'失败'}[status] || status);
const isDemo = () => state.health?.mode === 'demo';

function rememberDraft(form) {
  const source=form.id==='profile-form'?state.profile:form.id==='preferences-form'?state.preferences:null;
  if(!source)return;
  state.formDrafts[form.id]={values:formData(form),revision:state.formDrafts[form.id]?.revision ?? source.revision};
}
function restoreDrafts() {
  for(const [id,draft] of Object.entries(state.formDrafts)){
    const form=document.getElementById(id);if(!form)continue;
    const source=id==='profile-form'?state.profile:state.preferences;
    const changed=source.revision!==draft.revision;
    for(const [key,value] of Object.entries(draft.values))if(form.elements[key])form.elements[key].value=value;
    source.revision=draft.revision;
    form.insertAdjacentHTML('afterbegin',`<div class="tip" style="margin-bottom:18px">${changed?'已保存资料有更新。你的未保存输入已保留，请重新核对，避免覆盖新资料。':'未保存的输入已保留。'} ${button('读取已保存资料','discard-form-draft',`data-form="${id}"`,'ghost small')}</div>`);
  }
}
function updateActivity() {
  const node=$('#active-activity');
  node.hidden=!busy() || (state.params.get('run')===state.run?.id && ['evaluate','interview'].includes(state.page));
  node.textContent=state.run?.action==='interview'?'返回当前面试':'返回当前活动';
}
function newBatch(queue=[]) {const batch={queue,cancelled:false,saved:0,saving:false};state.importBatch=batch;return batch;}
function cancelBatch(batch) {if(!batch)return;batch.cancelled=true;batch.queue.length=0;if(state.importBatch===batch)state.importBatch=null;}
function dismissDialog() {
  const batch=state.modal?.batch;
  if(batch){cancelBatch(batch);if(batch.saving)toast('正在停止后续导入；已发送的请求可能已经保存。');}
  closeDialog();
}

function fileRows(items, action='') {
  return items.length ? `<div class="files">${items.map(x => `<div class="file-row"><span class="file-icon">${esc(x.filename.split('.').pop().toUpperCase())}</span><div class="file-info"><a href="/api/documents/${encodeURIComponent(x.id)}/content" target="_blank" rel="noopener">${esc(x.filename)}</a><small>${(x.size/1024).toFixed(1)} KB · ${when(x.created_at)}</small></div>${action ? button('处理',action,`data-id="${x.id}"`,'ghost small') : ''}${button('移除','delete-document',`data-id="${x.id}"`,'ghost small')}</div>`).join('')}</div>` : '<p class="caption">还没有上传资料。</p>';
}
function uploadBox(kind, title, text) {
  return `<div class="upload-zone"><div class="upload-symbol" aria-hidden="true">↥</div><h3>${title}</h3><p>${text}</p>${button('选择文件','upload',`data-kind="${kind}"`,'secondary')}</div>`;
}
function profileFields(data) {
  return field('姓名 / 称呼','name',data.name) + field('当前方向','headline',data.headline) +
    field('个人简介','summary',data.summary,{area:true,full:true,placeholder:'介绍你的经验、优势和希望发展的方向'}) +
    field('技能','skills',(data.skills || []).join('，'),{full:true,placeholder:'Python，自动化测试，接口测试',hint:'用逗号或换行分隔'}) +
    field('工作与项目经历','experience',data.experience,{area:true,full:true,rows:5}) + field('教育背景','education',data.education,{full:true});
}

async function resumePage() {
  const [profile, docs, memories] = await Promise.all([api('/profile'),api('/documents?kind=resume'),api('/memories')]);
  state.profile = profile;
  const data = state.profileDraft || profile.data;
  return `<div class="page-intro"><div><h2>先从你的经历开始</h2><p>上传简历，确认画像；也可以直接填写，让每条信息都由你掌握。</p></div><span class="badge ${profile.confirmed ? 'green' : 'amber'}">${profile.confirmed ? '画像已确认' : '等待确认'}</span></div><div class="split"><div class="stack">${card('简历资料','PDF / DOCX · 单文件不超过 16 MiB',uploadBox('resume','上传你的简历','支持文本型 PDF 和 DOCX') + `<div style="margin-top:20px">${fileRows(docs.items,'parse-resume')}</div>` + (isDemo() ? `<div style="margin-top:16px">${button('查看示例画像','sample-profile','','ghost small')}</div>` : ''))}${card('已确认的记忆','可查看、修改或移除',memories.items.length ? memories.items.map(m => `<div class="memory-row"><p>${esc(m.data.content)}<small class="muted" style="display:block">${esc(m.data.evidence)}</small></p>${button('编辑','edit-memory',`data-id="${m.id}"`,'ghost small')}${button('移除','delete-memory',`data-id="${m.id}"`,'ghost small')}</div>`).join('') : '<p class="caption">确认的偏好和练习记录会展示在这里。</p>',button('补充一条记忆','add-memory','','ghost small'))}</div><article class="card"><header class="card-head"><div><h2>我的求职画像</h2><p>核对内容后保存，Agent 将以这份画像为依据。</p></div><span class="badge">修订 ${profile.revision}</span></header><div class="card-body">${state.profileDraft ? '<div class="tip" style="margin-bottom:20px">当前为待确认草稿。演示模式下，这些是固定示例，并未解析你的简历。</div>' : ''}<form id="profile-form"><div class="form-grid">${profileFields(data)}</div><div class="form-actions"><button class="secondary" name="save" value="draft">保存草稿</button><button name="save" value="confirm">保存并确认画像</button></div></form>${(data.evidence || []).length ? `<details style="margin-top:20px"><summary class="caption">查看原始提取依据</summary><pre class="prose">${esc(JSON.stringify(data.evidence,null,2))}</pre></details>` : ''}</div></article></div>`;
}
async function preferencesPage() {
  const value = await api('/preferences'); state.preferences = value; const data = value.data;
  return `<div class="page-intro"><div><h2>找到适合自己的机会</h2><p>这些条件用于比较和解释，不会因为不完全匹配而自动淘汰岗位。</p></div>${isDemo() ? button('填入示例条件','sample-preferences','','secondary') : ''}</div>${card('求职偏好','所有岗位采用同一套条件进行比较',`<form id="preferences-form"><div class="form-grid">${field('期望城市','city',data.city,{placeholder:'例如：杭州'})}${field('岗位方向','direction',data.direction)}${field('期望月薪下限（元）','salary_min',data.salary_min ?? '',{type:'number'})}${field('期望月薪上限（元）','salary_max',data.salary_max ?? '',{type:'number'})}${field('通勤出发地址','commute_origin',data.commute_origin,{full:true,placeholder:'请输入城市及具体地点'})}${field('可接受单程时间（分钟）','commute_limit_minutes',data.commute_limit_minutes,{type:'number'})}<label class="field"><span>常用出行方式</span><select name="commute_mode"><option value="public_transit" ${data.commute_mode==='public_transit'?'selected':''}>公共交通</option><option value="driving" ${data.commute_mode==='driving'?'selected':''}>驾车</option></select><small>两种路线都会展示，评分采用这里选定的方式。</small></label></div><div class="tip" style="margin-top:24px">岗位总分采用固定权重：匹配度 60% · 薪资符合度 25% · 通勤便利度 15%。缺少数据时保留未知，不填零。</div><div class="form-actions"><button>保存求职条件</button></div></form>`)}`;
}
async function jobsPage() {
  const response = await api(`/jobs?q=${encodeURIComponent(state.query)}&offset=${state.offset}&limit=20`);
  const docs = await api('/documents?kind=image');
  return `<div class="page-intro"><div><h2>把机会放在一起看</h2><p>从招聘截图导入，或手动记录你感兴趣的岗位。</p></div>${button('＋ 添加岗位','new-job')}</div><div class="toolbar"><form id="job-search" class="search"><input aria-label="搜索岗位" name="q" placeholder="搜索公司、职位或城市" value="${esc(state.query)}"><button class="secondary">搜索</button></form><div class="actions">${button('导入截图','upload','data-kind="image"','secondary')}${button('粘贴招聘原文','paste-jd','','secondary')}${isDemo() ? button('加入示例岗位','sample-jobs','','secondary') : ''}<button type="button" data-action="evaluate-selection" id="evaluate-selection" ${state.selected.size ? '' : 'disabled'}>评估选中岗位 (${state.selected.size})</button></div></div><article class="card">${response.items.length ? `<div class="table-scroll"><table><thead><tr><th class="table-check"></th><th>职位 / 公司</th><th>城市</th><th>月薪区间</th><th>资料</th><th></th></tr></thead><tbody>${response.items.map(item => `<tr><td class="table-check"><input type="checkbox" data-select-job="${item.id}" ${state.selected.has(item.id)?'checked':''} aria-label="选择 ${esc(item.data.title)}"></td><td><a class="job-title" href="#" data-action="open-job" data-id="${item.id}">${esc(item.data.title)}</a><small>${esc(item.data.company)}</small></td><td>${esc(item.data.city || '待补充')}</td><td>${money(item.data.salary_min)}${item.data.salary_max != null ? ' — ' + money(item.data.salary_max) : ''}</td><td><span class="badge ${item.data.jd_text ? 'green' : 'amber'}">${item.data.jd_text ? '已有 JD' : '待补 JD'}</span></td><td class="table-actions">${button('查看','open-job',`data-id="${item.id}"`,'ghost')}${button('移除','delete-job',`data-id="${item.id}"`,'ghost')}</td></tr>`).join('')}</tbody></table></div>` : empty('岗位池还是空的','添加几个感兴趣的机会，再比较匹配程度、薪资和通勤。',button('手动添加岗位','new-job','','secondary'))}<footer class="table-footer"><span>共 ${response.total} 个岗位 · 只比较，不强制淘汰</span><div class="actions">${button('上一页','jobs-prev',state.offset===0?'disabled':'','ghost small')}<span>${Math.floor(state.offset/20)+1}</span>${button('下一页','jobs-next',state.offset+20>=response.total?'disabled':'','ghost small')}</div></footer></article>${docs.items.length ? `<div style="margin-top:22px">${card('已上传招聘截图','原图保留在本机，可重新提取或查看来源',fileRows(docs.items,'extract-image'))}</div>` : ''}`;
}
function traceHTML(run) {
  return `<div class="trace">${run.events.length ? run.events.map(e => `<div class="trace-item"><span class="trace-time">${when(e.at).split(' ').pop()}</span><div class="trace-content"><strong>${esc(e.message)}</strong>${Object.keys(e.data||{}).length ? `<pre>${esc(JSON.stringify(e.data,null,2))}</pre>` : ''}</div></div>`).join('') : '<p class="caption">等待第一条执行事件…</p>'}</div>`;
}
function pendingHTML(run) {
  if (!run?.pending_input) return '';
  const pending = run.pending_input;
  if (pending.kind === 'proposal') {
    return `<section class="pending"><h3>请确认这次修改</h3><p>${esc(pending.prompt)}</p><dl class="changes">${Object.entries(pending.proposal?.changes || {}).map(([k,v]) => `<dt>${esc(labels[k]||k)}</dt><dd>${esc(typeof v==='object'?JSON.stringify(v):v)}</dd>`).join('')}</dl><div class="actions">${button('拒绝修改','run-decision',`data-id="${run.id}" data-decision="reject"`,'secondary')}${button('确认并保存','run-decision',`data-id="${run.id}" data-decision="confirm"`)}</div></section>`;
  }
  return `<section class="pending"><h3>需要你补充一点信息</h3><p>${esc(pending.prompt)}</p><form data-run-answer="${run.id}"><textarea name="text" rows="3" aria-label="补充信息" required placeholder="在这里填写补充内容"></textarea><div class="actions">${button('结束本次','run-decision',`data-id="${run.id}" data-decision="finish"`,'secondary')}<button>提交并继续</button></div></form></section>`;
}
function scoreTable(data) {
  const rows = data.jobs || [];
  return `<div class="table-scroll"><table><thead><tr><th>岗位</th><th>总分</th><th>匹配</th><th>薪资</th><th>通勤</th></tr></thead><tbody>${rows.map(row=>`<tr><td><strong>${esc(row.company)}</strong><small>${esc(row.title)}</small></td><td><span class="big-score">${score(row.total_score)}</span>${row.total_score==null?'<small>待补充</small>':''}</td><td>${score(row.match_score)}</td><td>${score(row.salary_score)}</td><td>${score(row.commute_score)}</td></tr>`).join('')}</tbody></table></div>`;
}
function reportContent(report) {
  const data = report.data;
  const head = `<header class="card-head"><div><h2>${esc(data.title || '报告')}</h2><p>${when(report.created_at)} ${report.is_demo?'· 模拟结果':''}</p></div><div class="actions"><a class="badge" href="/api/reports/${report.id}/export?format=md">导出 MD ↗</a> <a class="badge" href="/api/reports/${report.id}/export?format=json">JSON ↗</a></div></header><div class="card-body"><p class="caption">${esc(data.summary || '')}</p>`;
  if (report.kind === 'evaluation') {
    return head + `</div>${scoreTable(data)}<div class="card-body">${(data.jobs||[]).map(row=>`<section class="report-note"><h3>${esc(row.company)} · ${esc(row.title)}</h3><p style="margin-top:8px">${esc(row.recommendation || '')}</p><ul>${(row.gaps||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul><div class="chip-list"><span class="chip">公交 ${row.commutes?.public_transit?.duration_minutes==null?'待补充':esc(row.commutes.public_transit.duration_minutes)+' 分钟'}</span><span class="chip">驾车 ${row.commutes?.driving?.duration_minutes==null?'待补充':esc(row.commutes.driving.duration_minutes)+' 分钟'}</span></div>${(row.missing||[]).length?`<p class="muted" style="margin-top:8px">待补充：${esc(row.missing.join('、'))}</p>`:''}${(row.evidence||[]).length?`<details style="margin-top:8px"><summary class="caption">依据与引用</summary><pre class="prose">${esc(JSON.stringify(row.evidence,null,2))}</pre></details>`:''}</section>`).join('')}</div>`;
  }
  return head + `<div class="stats" style="margin-top:20px">${(data.dimensions||[]).map(item=>`<div class="stat"><small>${esc(item.name)}</small><strong>${item.score==null?'未考察':score(item.score)}</strong></div>`).join('')}</div><h3>练习重点</h3><ul class="prose">${(data.weaknesses||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul><h3>推荐练习</h3><p class="prose">${esc((data.practice||[]).join('\n'))}</p><details style="margin-top:24px"><summary>回看本次问答</summary><ol class="answers">${(data.answers||[]).map(a=>`<li><strong>${esc(a.question)}</strong><p>${esc(a.answer)}</p><small class="muted">${esc(a.source || '')}</small></li>`).join('')}</ol></details></div>`;
}
async function evaluatePage() {
  const run = state.runCache.get(state.params.get('run')) || state.lastRuns.evaluate;
  if (!run) return card('开始一次岗位评估','选中岗位后，这里会展示处理进度、依据和执行轨迹。',empty('还没有评估任务','先确认画像和求职条件，再从岗位池选择 1—10 个岗位。',button('去岗位池选择','go-jobs')));
  const status = statusLabel(run.status);
  return `<div class="page-intro"><div><h2>每一步，都可以回看</h2><p>工具、观察结果和待补充信息会在这里同步显示。</p></div><span class="badge ${run.status==='failed'?'red':run.status==='needs_input'?'amber':'green'}">${status}</span></div><div class="stats"><div class="stat"><small>当前状态</small><strong style="font-size:22px">${status}</strong></div><div class="stat"><small>执行事件</small><strong>${run.events.length}<span>条</span></strong></div><div class="stat"><small>结果类型</small><strong style="font-size:22px">${run.is_demo?'固定联调样例':'真实 Agent'}</strong></div></div>${pendingHTML(run)}${run.result?.kind==='error'?`<div class="error-banner">${esc(run.result.data.message)}</div>`:''}<article class="card"><header class="card-head"><div><h2>执行轨迹</h2><p>按时间记录本次运行的事件。</p></div>${run.report_id?button('查看完整报告','view-report',`data-id="${run.report_id}"`):''}</header>${traceHTML(run)}</article>${run.result?.kind==='evaluation_report'?`<article class="card" style="margin-top:22px"><header class="card-head"><h2>结果概览</h2><span class="caption">匹配 60% · 薪资 25% · 通勤 15%</span></header>${scoreTable(run.result.data)}</article>`:''}`;
}
async function reportsPage() {
  const response = await api('/reports');
  if (!response.items.length) {state.currentReport=null;return card('我的决策报告','完成的评估与面试结果会保存在这里。',empty('暂时还没有报告','发起岗位评估或完成一次模拟面试，报告就会出现在这里。',button('去岗位池','go-jobs')));}
  const report = response.items.find(x=>x.id===state.params.get('id')) || response.items[0]; state.currentReport=report;
  return `<div class="page-intro"><div><h2>留住每一次判断的依据</h2><p>报告保存当时的输入与结果，修改资料不会覆盖历史记录。</p></div><span class="badge">${response.total} 份报告</span></div><div class="report-layout"><div class="report-list">${response.items.map(r=>`<button class="report-link ${r.id===report.id?'active':''}" data-action="view-report" data-id="${r.id}">${esc(r.data.title || (r.kind==='evaluation'?'岗位评估':'模拟面试'))}<small>${when(r.created_at)} ${r.is_demo?'· 模拟':''}</small></button>`).join('')}</div><article class="card">${reportContent(report)}</article></div>`;
}
async function knowledgePage() {
  const [docs, questions] = await Promise.all([api('/documents?kind=knowledge'),api('/knowledge/questions?limit=100')]);
  return `<div class="page-intro"><div><h2>用自己的题库，做有依据的练习</h2><p>导入 Markdown 文件。解析、向量检索和重排通过你实现的 Agent 接入。</p></div>${button('＋ 手动添加题目','new-question','','secondary')}</div><div class="split"><div class="stack">${card('知识资料','UTF-8 Markdown · 单文件不超过 16 MiB',uploadBox('knowledge','导入 MD 题库','可一次选择多份文件，逐份上传')+`<div style="margin-top:20px">${fileRows(docs.items,'index-knowledge')}</div>`)}<div class="tip">上传仅保存原文。运行索引后的覆盖结果由 Agent 提供；模拟模式不会创建向量索引。</div></div><article class="card"><header class="card-head"><div><h2>题目列表</h2><p>当前 ${questions.total} 道题 · 展示最近 100 条</p></div><span class="badge green">本地资料</span></header>${questions.items.length?questions.items.map(item=>`<section class="question-item"><header><h3>${esc(item.data.question)}</h3><div style="white-space:nowrap">${button('编辑','edit-question',`data-id="${item.id}"`,'ghost small')}${button('移除','delete-question',`data-id="${item.id}"`,'ghost small')}</div></header><div class="chip-list">${(item.data.tags||[]).map(x=>`<span class="chip">${esc(x)}</span>`).join('')}</div><details><summary>参考答案与来源</summary><p>${esc(item.data.answer || '未提供参考答案')}</p><small>${esc(item.data.locator || '手动录入')}${item.data.document_id?` · <a target="_blank" rel="noopener" href="/api/documents/${encodeURIComponent(item.data.document_id)}/content">原文 ↗</a>`:''}</small></details></section>`).join(''):empty('题库还没有内容','上传 MD 后运行索引，或先手动录入一道题。')}</article></div>`;
}
async function interviewPage() {
  const run = state.runCache.get(state.params.get('run')) || state.lastRuns.interview;
  if (!run) return card('准备开始面试','请先选择一个岗位。',empty('还没有面试会话','从岗位详情进入，让问题围绕该岗位展开。',button('选择岗位','go-jobs')));
  if (run.status==='running') return card('正在准备下一步','请稍候',`<div class="loading">正在处理本次面试…</div>${traceHTML(run)}`);
  if (run.status==='failed') return card('本次面试未完成','',`<div class="error-banner">${esc(run.result?.data?.message || '运行失败')}</div>${button('返回岗位池','go-jobs','','secondary')}`);
  if (run.result?.kind==='interview_report') return card('本次面试已结束','报告已保存',button('查看面试报告','view-report',`data-id="${run.report_id}"`));
  const data=run.result?.data||{};
  return `<div class="interview-meta"><span>文字面试 · ${run.is_demo?'固定演示题序':'根据回答动态选题'}</span>${button('提前结束并生成报告','run-decision',`data-id="${run.id}" data-decision="finish"`,'secondary small')}</div><div class="interview-progress"><span style="width:${Math.min((data.round||1)/8*100,100)}%"></span></div><article class="card"><div class="card-body"><span class="badge green">第 ${esc(data.round || 1)} / 最多 8 轮</span><h2 class="interview-question">${esc(data.question || run.pending_input?.prompt || '')}</h2><p class="caption">来源：${esc(data.source || '见 Agent 提供的题目引用')}</p><form data-run-answer="${run.id}" style="margin-top:25px"><textarea class="interview-answer" name="text" required placeholder="写下你的思路。可以结合经历说明，也可以如实回答尚不了解。" aria-label="面试回答"></textarea><div class="form-actions"><button>提交回答 →</button></div></form></div></article><p class="caption" style="margin-top:18px">追问计入轮数；没有倒计时，结束后统一反馈。${run.is_demo?'演示题序和评分均为固定样例。':''}</p>`;
}
const renderers = {resume:resumePage,preferences:preferencesPage,jobs:jobsPage,evaluate:evaluatePage,reports:reportsPage,knowledge:knowledgePage,interview:interviewPage};

async function render() {
  const generation=++state.generation;
  const [path,query='']=(location.hash.slice(1)||'/resume').split('?');
  state.page=path.replace(/^\//,'') || 'resume'; state.params=new URLSearchParams(query);
  if (!pages[state.page]) {go('resume');return;}
  document.body.classList.toggle('interview-mode',state.page==='interview');
  $('#page-title').textContent=pages[state.page][0]; $('#eyebrow').textContent=pages[state.page][1];
  document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('active',a.dataset.page===state.page));
  const runId=state.params.get('run');
  if (runId && !state.runCache.has(runId)) {
    try {setRun(await api('/runs/'+encodeURIComponent(runId)),false);}
    catch(error){if(error.status===404){toast(error.message,true);go(state.page);return;}else throw error;}
  }
  const html=await renderers[state.page]();
  if (generation!==state.generation) return;
  $('#view').innerHTML=html;
  restoreDrafts();updateActivity();
  if (state.nextJob && state.page==='jobs') {const id=state.nextJob;state.nextJob=null;await openJob(id);}
  renderChat();
}

function context() {
  if (state.currentJob) return {type:'job',id:state.currentJob.id,session_id:state.sessionId};
  if (state.page==='reports' && state.currentReport) return {type:'report',id:state.currentReport.id,session_id:state.sessionId};
  return {type:state.page,session_id:state.sessionId};
}
function contextTitle() {
  return state.currentJob ? `${state.currentJob.data.company} · ${state.currentJob.data.title}` : state.page==='reports'&&state.currentReport ? state.currentReport.data.title : (pages[state.page]?.[0] || '工作台');
}
function setRun(value,active=true) {
  const previous=state.runCache.get(value.id);
  if(previous && value.events?.length && value.events[0].seq>1){
    const seen=new Set(previous.events.map(x=>x.seq));value.events=[...previous.events,...value.events.filter(x=>!seen.has(x.seq))];
  }else if(previous && !value.events?.length)value.events=previous.events;
  state.runCache.set(value.id,value);
  if(active){state.run=value;state.lastRuns[value.action]=value;updateActivity();}
  return value;
}
async function begin(action,input={},batch=null) {
  if (isDemo()) input.demo_scenario=state.scenario;
  const run=await api('/runs',{method:'POST',body:{action,context:context(),input}}); setRun(run);
  if(action==='extract_jobs')state.runBatches.set(run.id,batch || newBatch());
  if (action==='evaluate') go('evaluate',{run:run.id});
  if (action==='interview') {closeJob();go('interview',{run:run.id});}
  await poll();
}
async function poll() {
  clearTimeout(state.timer);
  if (!state.run) return;
  const id=state.run.id;
  try {
    const value=await api(`/runs/${id}?after=${state.run.next_seq || 0}`);
    if (state.run?.id!==id) return;
    state.pollError=false;
    setRun(value);
    if (state.page==='evaluate' || state.page==='interview') await render();
    renderChat();
    await settled(value);
    if (value.status==='running') state.timer=setTimeout(()=>poll(),1000);
  } catch(error) {
    if(state.run?.id!==id)return;
    if(!state.pollError)showError(error);state.pollError=true;
    if(error.status && error.status<500){
      cancelBatch(state.runBatches.get(id));
      setRun({...state.run,status:'failed',pending_input:null,result:{kind:'error',data:{message:error.message}}});
      renderChat();
    }else if(state.run.status==='running')state.timer=setTimeout(()=>poll(),1000);
  }
}
async function settled(run) {
  if (run.status==='running') return;
  if (run.status==='needs_input') {
    if (run.action!=='interview' && !state.shownMessages.has(run.pending_input.id)) {
      state.shownMessages.add(run.pending_input.id); state.chatOpen=true; renderChat();
    }
    return;
  }
  if (state.processed.has(run.id)) return;
  state.processed.add(run.id);
  const result=run.result||{};
  if (run.status==='failed') {toast(result.data?.message||'运行失败',true);cancelBatch(state.runBatches.get(run.id));return;}
  if (result.kind==='profile_draft') {state.modal={kind:'profile-draft',data:result.data.profile};openDialog('核对画像草稿',`<div class="tip">${esc(result.data.notice||'请核对后填入画像表单，再确认保存。')}</div><pre class="prose" style="margin-top:18px">${esc(JSON.stringify(result.data.profile,null,2))}</pre>`,button('填入画像表单','use-profile-draft'));}
  if (result.kind==='job_drafts') {
    const batch=state.runBatches.get(run.id) || newBatch();if(batch.cancelled)return;
    state.modal={kind:'job-drafts',data:result.data.jobs||[],batch};
    openDialog('确认待导入岗位',`<div class="tip">${esc(result.data.notice||'请在入库前补齐公司和职位，或跳过不需要的条目。')}</div><form id="job-drafts-form">${state.modal.data.map((job,i)=>`<section class="report-note"><header style="padding:0 0 12px;border:0"><h3>岗位 ${i+1}</h3><label class="caption"><input type="checkbox" data-skip-draft="${i}"> 跳过此条</label></header><fieldset data-draft-index="${i}" style="border:0;padding:0;margin:0"><div class="form-grid">${jobFields(job)}</div></fieldset></section>`).join('')}</form>`,`<button type="submit" form="job-drafts-form">确认入库</button>`);
  }
  if (result.kind==='knowledge_result') {
    state.modal={kind:'knowledge-result',data:result.data.questions||[]};
    openDialog('题库处理结果',`<div class="tip">${esc(result.data.notice||'处理完成')}</div>${(result.data.questions||[]).map(q=>`<div class="report-note"><h3>${esc(q.question)}</h3><p>${esc(q.answer)}</p></div>`).join('')}`,isDemo()?button('保存演示题目','save-demo-questions'):button('查看题库','close-modal','','secondary'));
  }
  if (result.kind==='chat') {
    state.messages.push({role:'assistant',text:result.data.message||'',context:run.is_demo?'模拟回复':'Agent 回复'}); renderChat();
    if (state.refreshAfterRun.delete(run.id)) await render();
  }
  if (result.kind==='interview_report') {
    state.nextJob=run.subject_job_id||state.interviewJobId||null; go('jobs'); toast('面试报告已保存，可在报告页查看。');
  }
  if (result.kind==='evaluation_report') toast(run.status==='partial'?'已保存部分结果，缺失项仍可补充。':'评估结果已保存。');
}
async function respond(runId,decision,text='') {
  const run=state.runCache.get(runId) || state.run;
  if (!run?.pending_input) throw new Error('该输入已处理，请刷新当前状态。');
  if(decision==='finish')cancelBatch(state.runBatches.get(runId));
  const response=await api(`/runs/${runId}/input`,{method:'POST',body:{input_id:run.pending_input.id,decision,text}});
  if(decision==='confirm')state.refreshAfterRun.add(runId);
  setRun(response); await poll();
}

function openDialog(title,body,footer='') {
  const dialog=$('#editor');
  dialog.innerHTML=`<header><h2>${esc(title)}</h2>${button('✕','close-modal','','ghost')}</header><div class="dialog-body">${body}</div>${footer?`<footer>${button('取消','close-modal','','secondary')}${footer}</footer>`:''}`;
  if (!dialog.open) dialog.showModal();
}
function closeDialog() {$('#editor').close();state.modal=null;}
function closeJob() {state.currentJob=null;$('#job-drawer').hidden=true;$('#drawer-overlay').hidden=true;renderChat();}
async function openJob(id) {
  const [job,documents,reports]=await Promise.all([api('/jobs/'+id),api('/documents'),api('/reports')]); state.currentJob=job; const data=job.data;
  const sources=documents.items.filter(x=>data.source_ids.includes(x.id));
  const recent=reports.items.find(report=>report.kind==='interview'&&report.snapshot.jobs?.[0]?.id===id)?.id;
  $('#job-drawer').innerHTML=`<header class="drawer-head"><div><div class="eyebrow">岗位详情</div><h2>${esc(data.title)}</h2><p class="caption">${esc(data.company)}</p></div>${button('✕','close-job','','ghost')}</header><div class="drawer-body"><section><dl class="drawer-meta"><dt>工作城市</dt><dd>${esc(data.city||'待补充')}</dd><dt>月薪区间</dt><dd>${money(data.salary_min)} — ${money(data.salary_max)}</dd><dt>办公地址</dt><dd>${esc(data.address||'待补充')}</dd>${data.salary_note?`<dt>薪资说明</dt><dd>${esc(data.salary_note)}</dd>`:''}</dl></section><section><h3>岗位描述</h3><p class="prose">${esc(data.jd_text||'尚未填写 JD，可以编辑或通过聊天补充。')}</p></section>${data.notes?`<section><h3>我的备注</h3><p class="prose">${esc(data.notes)}</p></section>`:''}<section><h3>资料来源</h3>${sources.length?sources.map(s=>`<p><a target="_blank" rel="noopener" href="/api/documents/${s.id}/content">${esc(s.filename)} ↗</a></p>${s.kind==='image'?`<img class="source-image" src="/api/documents/${s.id}/content" alt="招聘原图" loading="lazy">`:''}`).join(''):'<p class="caption">手动记录或演示资料。</p>'}</section>${recent?button('查看最近面试报告','view-report',`data-id="${recent}"`,'secondary small'):''}</div><footer class="drawer-actions">${button('编辑岗位','edit-job',`data-id="${id}"`,'secondary')}${button('评估此岗位','evaluate-job',`data-id="${id}"`,'secondary')}${button('模拟面试','interview-job',`data-id="${id}"`)}</footer>`;
  $('#job-drawer').hidden=false;$('#drawer-overlay').hidden=false;renderChat();
}
function jobFields(data) {
  return field('公司名称','company',data.company,{required:true,maxLength:200})+field('职位名称','title',data.title,{required:true,maxLength:200})+field('工作城市','city',data.city,{maxLength:100})+field('办公地址','address',data.address,{maxLength:300})+field('月薪下限（元）','salary_min',data.salary_min??'',{type:'number'})+field('月薪上限（元）','salary_max',data.salary_max??'',{type:'number'})+field('薪资补充说明','salary_note',data.salary_note,{full:true,maxLength:500})+field('岗位描述 JD','jd_text',data.jd_text,{area:true,full:true,rows:6,maxLength:50000})+field('我的备注','notes',data.notes,{area:true,full:true,rows:2,maxLength:5000});
}
async function editJob(id=null) {
  const item=id?await api('/jobs/'+id):{data:{source_ids:[]}};
  const docs=await api('/documents');state.modal={kind:'job',item};
  openDialog(id?'编辑岗位':'添加岗位',`<form id="job-form"><div class="form-grid">${jobFields(item.data)}<label class="field full"><span>关联资料（可多选）</span><select name="source_ids" multiple size="${Math.min(Math.max(docs.items.length,2),4)}">${docs.items.map(doc=>`<option value="${doc.id}" ${item.data.source_ids.includes(doc.id)?'selected':''}>${esc(doc.filename)}</option>`).join('')}</select></label></div><div class="form-actions"><button>保存岗位</button></div></form>`);
}
async function editQuestion(id=null) {
  const response=id?await api('/knowledge/questions?limit=500'):null;
  const item=id?response.items.find(x=>x.id===id):{data:{}};
  if (!item) throw new Error('题目不存在，请刷新。');
  state.modal={kind:'question',item};const data=item.data;
  openDialog(id?'编辑题目':'手动添加题目',`<form id="question-form"><div class="form-grid">${field('题目','question',data.question,{area:true,full:true,required:true})}${field('参考答案','answer',data.answer,{area:true,full:true,rows:5})}${field('知识点','tags',(data.tags||[]).join('，'),{full:true,placeholder:'用逗号分隔'})}${field('来源位置 / 说明','locator',data.locator,{full:true})}</div><div class="form-actions"><button>保存题目</button></div></form>`);
}
async function editMemory(id=null) {
  const response=id?await api('/memories'):null;const item=id?response.items.find(x=>x.id===id):{data:{category:'preference'}};
  if (!item) throw new Error('记录不存在，请刷新。');state.modal={kind:'memory',item};const data=item.data;
  openDialog(id?'修改已确认记忆':'补充已确认记忆',`<form id="memory-form"><div class="form-grid">${field('内容','content',data.content,{area:true,full:true,required:true})}<label class="field full"><span>类别</span><select name="category">${[['preference','求职偏好'],['profile','个人画像'],['weakness','练习薄弱项']].map(([v,t])=>`<option value="${v}" ${data.category===v?'selected':''}>${t}</option>`).join('')}</select></label>${field('确认依据','evidence',data.evidence,{area:true,full:true,rows:2})}</div><div class="form-actions"><button>确认并保存</button></div></form>`);
}

function renderChat() {
  const drawer=$('#chat-drawer');drawer.hidden=!state.chatOpen;if(!state.chatOpen)return;
  const pending=state.run?.status==='needs_input'&&state.run.action!=='interview'?state.run:null;
  const waiting=state.run?.status==='running';
  const linked=pending?({evaluate:'当前岗位评估活动',parse_resume:'当前简历解析活动',extract_jobs:'当前岗位提取活动',index_knowledge:'当前题库处理活动',chat:'当前聊天修改提案'}[pending.action]||'当前活动'):contextTitle();
  drawer.innerHTML=`<header class="chat-head"><div><h3>聊一聊，补充一点信息</h3><small>当前关联：${esc(linked)}</small></div>${button('✕','close-chat','','ghost')}</header><div class="chat-messages" id="chat-messages">${state.messages.length?state.messages.map(m=>`<div class="bubble ${m.role==='user'?'user':''}"><small>${esc(m.context || '')}</small>${esc(m.text)}</div>`).join(''):'<div class="bubble">可以补充经历、修改求职偏好，或就当前岗位和报告追问。涉及资料修改时，会先请你确认。</div>'}${pending?pendingHTML(pending):''}${waiting?'<p class="caption">正在处理当前活动…</p>':''}</div><form id="chat-form" class="chat-compose"><textarea name="text" id="chat-text" placeholder="补充信息，或问一个问题…" aria-label="聊天消息" ${busy()?'disabled':''}>${esc(state.chatDraft)}</textarea><footer><small>${isDemo()?'当前回复为固定联调样例':'只关联当前对象'}</small><button class="small" ${busy()?'disabled':''}>发送 ↑</button></footer></form>`;
  const messages=$('#chat-messages');messages.scrollTop=messages.scrollHeight;
}

async function chooseFiles(kind) {
  state.uploadKind=kind;const picker=$('#file-picker');picker.accept={resume:'.pdf,.docx',image:'.png,.jpg,.jpeg,.webp',knowledge:'.md'}[kind];picker.multiple=kind!=='resume';picker.value='';picker.click();
}
async function uploadFiles(files) {
  const kind=state.uploadKind;const uploaded=[];
  try {
    for(const file of files){
      const limit=(kind==='image'?8:16)*1024*1024;
      if(file.size>limit)throw new Error(`${file.name} 超过 ${limit/1024/1024} MiB 上限，已停止本次上传。`);
      const body=new FormData();body.append('kind',kind);body.append('file',file);
      uploaded.push(await api('/documents',{method:'POST',body}));
    }
  } finally {await render();}
  toast(`已保存 ${uploaded.length} 份资料。`);
  if(kind==='resume' && uploaded[0])await begin('parse_resume',{document_id:uploaded[0].id});
  if(kind==='image')await nextImage(newBatch(uploaded.map(x=>x.id)));
}
async function nextImage(batch) {
  if(!batch || batch.cancelled || state.importBatch!==batch)return;
  const id=batch.queue.shift();
  if(!id){state.importBatch=null;return;}
  try{await begin('extract_jobs',{document_id:id},batch);}catch(error){cancelBatch(batch);throw error;}
}

async function saveJobDrafts(form) {
  const operation=state.modal;
  if(!operation || operation.kind!=='job-drafts' || operation.batch.cancelled || operation.batch.saving)return;
  const batch=operation.batch;
  const entries=[];
  for(const section of form.querySelectorAll('[data-draft-index]')){
    if(section.disabled)continue;
    const values=Object.fromEntries([...section.querySelectorAll('[name]')].map(control=>[control.name,control.value]));
    const data={...operation.data[Number(section.dataset.draftIndex)],...values};
    data.company=data.company.trim();data.title=data.title.trim();
    if(!data.company || !data.title)throw new Error('请补齐每条岗位的公司和职位名称，或勾选跳过。');
    for(const key of ['salary_min','salary_max']){
      data[key]=data[key]?Number(data[key]):null;
      if(data[key]!=null && (!Number.isInteger(data[key])||data[key]<=0||data[key]>10000000))throw new Error('月薪应为 1—10000000 的整数，未知可留空。');
    }
    if(data.salary_min!=null && data.salary_max!=null && data.salary_min>data.salary_max)throw new Error('薪资下限不能超过上限，请在入库前修改。');
    entries.push({data,section});
  }
  batch.saving=true;form.inert=true;
  try{
    for(const entry of entries){
      if(batch.cancelled)break;
      await api('/jobs',{method:'POST',body:entry.data});batch.saved++;
      entry.section.disabled=true;
      const skip=entry.section.closest('section').querySelector('[data-skip-draft]');if(skip)skip.disabled=true;
    }
    if(batch.cancelled){await render();toast(`已停止后续导入，本批已保存或复用 ${batch.saved} 条。`);return;}
    if(state.modal===operation)closeDialog();
    await render();toast(entries.length?'岗位已入库，可继续编辑核对。':'已跳过当前条目。');
    await nextImage(batch);
  }catch(error){
    if(error.status===413){cancelBatch(batch);if(state.modal===operation)closeDialog();}
    error.message+=batch.saved?` 本批已有 ${batch.saved} 条保存或复用，未继续处理后续条目。`:'';
    throw error;
  }finally{batch.saving=false;form.inert=false;}
}

const actions={
  'go-jobs':()=>go('jobs'),
  'open-chat':()=>{state.chatOpen=true;renderChat();},'close-chat':()=>{state.chatOpen=false;renderChat();},
  'close-modal':dismissDialog,'close-job':closeJob,
  'resume-activity':()=>{if(!state.run)return;if(['evaluate','interview'].includes(state.run.action))go(state.run.action,{run:state.run.id});else{state.chatOpen=true;renderChat();}},
  'discard-form-draft':async element=>{if(confirm('放弃这份未保存的表单输入，重新读取已保存资料？')){delete state.formDrafts[element.dataset.form];if(element.dataset.form==='profile-form')state.profileDraft=null;await render();}},
  'upload':element=>chooseFiles(element.dataset.kind),
  'new-job':()=>editJob(),'edit-job':element=>editJob(element.dataset.id),'open-job':element=>openJob(element.dataset.id),
  'jobs-prev':()=>{state.offset=Math.max(0,state.offset-20);return render();},'jobs-next':()=>{state.offset+=20;return render();},
  'delete-job':async element=>{if(confirm('移除这个岗位？已保存的报告会保留。')){await api('/jobs/'+element.dataset.id,{method:'DELETE'});state.selected.delete(element.dataset.id);await render();}},
  'delete-document':async element=>{if(confirm('移除资料和关联题目元数据？历史报告保留快照；向量索引的清理由你的 Agent 负责。')){await api('/documents/'+element.dataset.id,{method:'DELETE'});await render();}},
  'parse-resume':element=>begin('parse_resume',{document_id:element.dataset.id}),
  'extract-image':element=>begin('extract_jobs',{document_id:element.dataset.id}),
  'index-knowledge':element=>begin('index_knowledge',{document_id:element.dataset.id}),
  'sample-profile':()=>begin('parse_resume'),
  'sample-preferences':async()=>{const examples=await api('/demo/examples');const form=$('#preferences-form');for(const [k,v] of Object.entries(examples.preferences))if(form.elements[k])form.elements[k].value=v;rememberDraft(form);toast('已填入示例，请核对后保存。');},
  'sample-jobs':async()=>{const data=await api('/demo/examples');for(const item of data.jobs)await api('/jobs',{method:'POST',body:item});await render();toast('示例岗位已加入，精确重复记录会复用。');},
  'use-profile-draft':async()=>{delete state.formDrafts['profile-form'];state.profileDraft=state.modal.data;closeDialog();go('resume');},
  'save-demo-questions':async()=>{for(const data of state.modal.data)await api('/knowledge/questions',{method:'POST',body:data});closeDialog();await render();toast('固定演示题已保存。');},
  'paste-jd':()=>{state.modal={kind:'paste'};openDialog('粘贴招聘原文',`<form id="paste-form">${field('招聘原文','text','',{area:true,required:true,rows:9})}<div class="form-actions"><button>提取岗位</button></div></form>`);},
  'evaluate-selection':()=>begin('evaluate',{job_ids:[...state.selected]}),
  'evaluate-job':async element=>{const id=element.dataset.id;closeJob();await begin('evaluate',{job_ids:[id]});},
  'interview-job':async element=>{state.interviewJobId=element.dataset.id;await begin('interview',{job_id:element.dataset.id});},
  'view-report':element=>{closeJob();state.chatOpen=false;go('reports',{id:element.dataset.id});},
  'run-decision':element=>respond(element.dataset.id,element.dataset.decision),
  'new-question':()=>editQuestion(),'edit-question':element=>editQuestion(element.dataset.id),
  'delete-question':async element=>{if(confirm('移除这道题？')){await api('/knowledge/questions/'+element.dataset.id,{method:'DELETE'});await render();}},
  'add-memory':()=>editMemory(),'edit-memory':element=>editMemory(element.dataset.id),
  'delete-memory':async element=>{if(confirm('移除这条记忆？')){await api('/memories/'+element.dataset.id,{method:'DELETE'});await render();}},
};

document.addEventListener('click',async event=>{
  const element=event.target.closest('[data-action]');if(!element)return;
  const action=actions[element.dataset.action];if(!action)return;event.preventDefault();
  if(element.disabled)return;element.disabled=true;
  try{await action(element);}catch(error){showError(error);}finally{element.disabled=false;}
});
document.addEventListener('submit',async event=>{
  event.preventDefault();const form=event.target;const submitter=event.submitter;
  if(submitter?.disabled)return;if(submitter)submitter.disabled=true;
  try{
    const data=formData(form);
    if(form.dataset.runAnswer){await respond(form.dataset.runAnswer,'answer',data.text);return;}
    if(form.id==='job-drafts-form'){await saveJobDrafts(form);return;}
    if(form.id==='profile-form'){
      const profile={...state.profile.data,...(state.profileDraft||{}),...data,skills:data.skills.split(/[,，\n]/).map(x=>x.trim()).filter(Boolean)};
      const confirmed=submitter?.value!=='draft';
      await api('/profile',{method:'PUT',body:{revision:state.profile.revision,confirmed,data:profile}});state.profileDraft=null;delete state.formDrafts[form.id];await render();toast(confirmed?'画像已确认，可以继续设置求职条件。':'草稿已保存。');
    }else if(form.id==='preferences-form'){
      for(const key of ['salary_min','salary_max','commute_limit_minutes'])data[key]=data[key]?Number(data[key]):null;
      await api('/preferences',{method:'PUT',body:{revision:state.preferences.revision,data}});delete state.formDrafts[form.id];await render();toast('求职条件已保存。');
    }else if(form.id==='job-search'){
      state.query=data.q;state.offset=0;await render();
    }else if(form.id==='job-form'){
      const item=state.modal.item;data.source_ids=new FormData(form).getAll('source_ids');
      for(const key of ['salary_min','salary_max'])data[key]=data[key]?Number(data[key]):null;
      const result=await api('/jobs'+(item.id?'/'+item.id:''),{method:item.id?'PUT':'POST',body:item.id?{revision:item.revision,data}:data});closeDialog();await render();if(state.currentJob?.id===item.id)await openJob(result.id);toast('岗位已保存。');
    }else if(form.id==='question-form'){
      const item=state.modal.item;data.tags=data.tags.split(/[,，\n]/).map(x=>x.trim()).filter(Boolean);data.document_id=item.data.document_id||null;
      await api('/knowledge/questions'+(item.id?'/'+item.id:''),{method:item.id?'PUT':'POST',body:data});closeDialog();await render();toast('题目已保存。');
    }else if(form.id==='memory-form'){
      const item=state.modal.item;await api('/memories'+(item.id?'/'+item.id:''),{method:item.id?'PUT':'POST',body:data});closeDialog();await render();toast('记忆已保存。');
    }else if(form.id==='paste-form'){
      closeDialog();await begin('extract_jobs',{text:data.text});
    }else if(form.id==='chat-form'){
      if(!data.text?.trim())return;state.messages.push({role:'user',text:data.text,context:contextTitle()});state.chatDraft='';renderChat();await begin('chat',{text:data.text});
    }
  }catch(error){showError(error);}finally{if(submitter)submitter.disabled=false;}
});
document.addEventListener('change',event=>{
  if(event.target.id==='scenario')state.scenario=event.target.value;
  if(event.target.dataset.skipDraft!=null){const section=$(`#editor [data-draft-index="${event.target.dataset.skipDraft}"]`);if(section)section.disabled=event.target.checked;}
  if(event.target.form)rememberDraft(event.target.form);
  if(event.target.dataset.selectJob){const id=event.target.dataset.selectJob;event.target.checked?state.selected.add(id):state.selected.delete(id);const button=$('#evaluate-selection');if(button){button.textContent=`评估选中岗位 (${state.selected.size})`;button.disabled=!state.selected.size;}}
});
document.addEventListener('input',event=>{if(event.target.id==='chat-text')state.chatDraft=event.target.value;if(event.target.form)rememberDraft(event.target.form);});
$('#file-picker').addEventListener('change',event=>uploadFiles([...event.target.files]).catch(showError));
$('#drawer-overlay').addEventListener('click',closeJob);
$('#editor').addEventListener('cancel',event=>{event.preventDefault();dismissDialog();});
window.addEventListener('hashchange',()=>{closeJob();render().catch(showError);});

async function bootstrap(){
  try{
    state.health=await api('/health');$('#global-error').hidden=true;
    $('#mode-badge').textContent=isDemo()?'模拟数据模式':'真实接入模式';$('#mode-badge').className='badge '+(isDemo()?'amber':'green');
    $('#mode-banner').hidden=!isDemo();$('#connection').className='connection ok';$('#connection span').textContent='本机服务 · MySQL 已连接';
    const current=await api('/runs');if(current.active)setRun(current.active);
    await render();if(state.run?.status==='running')await poll();
    if(state.run?.status==='needs_input'&&state.run.action==='interview'){state.interviewJobId=state.run.subject_job_id||null;go('interview',{run:state.run.id});}
    else if(state.run?.status==='needs_input'){state.chatOpen=true;renderChat();}
  }catch(error){$('#connection').className='connection error';$('#connection span').textContent='连接需要检查';$('#view').innerHTML=empty('工作台暂时无法连接','请确认本机服务已启动、.env 已填写，并已运行数据库初始化。');showError(error);}
}
bootstrap();
