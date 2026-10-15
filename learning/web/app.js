(() => {
  'use strict';

  const state = {
    data: null,
    selectedLesson: null,
    view: 'lesson',
    busy: false,
    loading: false,
    drafts: new Map(),
    noticeTimer: null,
    clipboardTrigger: null,
    quiz: new Map(),
    checks: new Map(),
    checking: false,
  };
  const elements = {
    content: document.getElementById('view-content'),
    lessonList: document.getElementById('lesson-list'),
    count: document.getElementById('course-count'),
    progress: document.getElementById('overall-progress'),
    resume: document.getElementById('resume-summary'),
    saved: document.getElementById('last-saved'),
    notice: document.getElementById('notice'),
    clipboard: document.getElementById('clipboard-fallback'),
    clipboardText: document.getElementById('clipboard-text'),
  };
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
  const icon = (name, className = '') => {
    const paths = {
      check: '<path d="m4 8 3 3 5-6"/>',
      copy: '<rect x="6" y="6" width="8" height="9" rx="1.5"/><path d="M10 6V3H2v9h4"/>',
      open: '<path d="M9 2h5v5M14 2 7 9"/><path d="M6 3H2v11h11v-4"/>',
      folder: '<path d="M2 5V3h5l2 2h5v8H2V5Z"/>',
    };
    return `<svg class="${escapeHtml(className)}" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.check}</svg>`;
  };
  const lessons = () => state.data?.catalog?.lessons || [];
  const getLesson = (id) => lessons().find((lesson) => lesson.id === id);
  const getProgress = (id) => state.data?.progress?.lessons?.[id] || { status: 'not_started', completedSteps: [], notes: '', workspace: null };
  const completedLessons = () => lessons().filter((lesson) => getProgress(lesson.id).status === 'completed');
  const statusLabel = (status) => ({ completed: '已完成', in_progress: '學習中', not_started: '尚未開始' }[status] || '尚未開始');
  const statusClass = (status) => ({ completed: 'completed', in_progress: 'in-progress' }[status] || 'not-started');
  const statusBadge = (status) => `<span class="status-badge ${statusClass(status)}">${status === 'completed' ? icon('check') : ''}${statusLabel(status)}</span>`;
  const completedSteps = (id) => new Set(getProgress(id).completedSteps || []);
  const stepCount = (lesson) => (lesson.steps || []).filter((step) => completedSteps(lesson.id).has(step.id)).length;
  const currentStep = (lesson) => {
    const steps = lesson.steps || [];
    const progress = getProgress(lesson.id);
    return steps.find((step) => step.id === progress.currentStep && !completedSteps(lesson.id).has(step.id))
      || steps.find((step) => !completedSteps(lesson.id).has(step.id))
      || steps[steps.length - 1];
  };
  const asText = (value) => {
    if (Array.isArray(value)) return value.map(asText).join('\n');
    if (value && typeof value === 'object') return Object.values(value).map(asText).join('\n');
    return String(value ?? '');
  };
  const textBlock = (value, className = '') => `<div class="instruction-text ${className}">${escapeHtml(asText(value))}</div>`;
  const fileUrl = (path, read = false) => {
    const normalized = String(path || '').replaceAll('\\', '/').replace(/^\/+/, '');
    if (!normalized || normalized.split('/').some((segment) => segment === '..')) return '#';
    return `/${read ? 'read' : 'files'}/${normalized.split('/').map(encodeURIComponent).join('/')}`;
  };
  const dateLabel = (value) => {
    if (!value || Number.isNaN(Date.parse(value))) return '尚未記錄';
    return new Intl.DateTimeFormat('zh-TW', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value));
  };

  class HttpError extends Error {
    constructor(message, status, data) { super(message); this.name = 'HttpError'; this.status = status; this.data = data; }
  }

  async function request(url, options = {}) {
    const response = await fetch(url, { cache: 'no-store', ...options, headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
    const contentType = response.headers.get('content-type') || '';
    const data = contentType.includes('application/json') ? await response.json() : { error: await response.text() };
    if (!response.ok) throw new HttpError(asText(data.error || data.message || `本機服務回應錯誤（${response.status}）。`), response.status, data);
    return data;
  }

  function showNotice(message, type = 'success', sticky = false) {
    window.clearTimeout(state.noticeTimer);
    elements.notice.textContent = message;
    elements.notice.className = `notice${type === 'success' ? '' : ` ${type}`}`;
    elements.notice.hidden = false;
    if (!sticky) state.noticeTimer = window.setTimeout(() => { elements.notice.hidden = true; }, 10000);
  }

  function preserveDraft() {
    const textarea = document.querySelector('[data-note-input]');
    if (textarea && state.selectedLesson) state.drafts.set(state.selectedLesson, textarea.value);
  }

  function setBusy(busy) {
    state.busy = busy;
    elements.content.setAttribute('aria-busy', String(busy));
    document.querySelectorAll('button').forEach((button) => {
      if (button.closest('#clipboard-fallback')) return;
      if (busy) {
        button.dataset.preBusyDisabled = String(button.disabled);
        button.disabled = true;
      } else if (button.dataset.preBusyDisabled !== undefined) {
        button.disabled = button.dataset.preBusyDisabled === 'true';
        delete button.dataset.preBusyDisabled;
      }
    });
    document.querySelectorAll('[data-note-input]').forEach((textarea) => { textarea.disabled = busy; });
  }

  async function bootstrap({ quiet = false, force = false } = {}) {
    if (state.loading || (!force && state.busy)) return;
    state.loading = true;
    try {
      const data = await request('/api/bootstrap');
      if (!data.catalog || !Array.isArray(data.catalog.lessons) || !data.progress) throw new Error('本機課程資料不完整，請重新啟動教學入口。');
      const previousRevision = state.data?.progress?.revision;
      const firstLoad = !state.data;
      preserveDraft();
      state.data = data;
      if (!getLesson(state.selectedLesson)) {
        state.selectedLesson = getLesson(data.progress.activeLesson)?.id || lessons().find((lesson) => getProgress(lesson.id).status !== 'completed')?.id || lessons()[0]?.id;
      }
      if (firstLoad || force || previousRevision !== data.progress.revision) render();
      if (!quiet && !firstLoad) showNotice('已重新載入專案中的學習進度。');
    } catch (error) {
      if (!state.data) {
        elements.content.innerHTML = `<div class="empty-state"><h2>尚未連上本機教學服務</h2><p>${escapeHtml(error.message)}</p><button type="button" class="button button-primary" data-action="retry">重新讀取</button></div>`;
        elements.content.setAttribute('aria-busy', 'false');
        elements.saved.textContent = '尚未載入共用進度';
      } else if (!quiet) showNotice(error.message, 'error', true);
    } finally {
      state.loading = false;
    }
  }

  function render() {
    if (!state.data) return;
    renderSidebar();
    document.querySelectorAll('[data-view]').forEach((button) => {
      if (button.dataset.view === state.view) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    if (state.view === 'overview') elements.content.innerHTML = renderOverview();
    else if (state.view === 'journal') elements.content.innerHTML = renderJournal();
    else elements.content.innerHTML = renderLesson();
    elements.content.setAttribute('aria-busy', String(state.busy));
    if (state.busy) elements.content.querySelectorAll('button').forEach((button) => { button.disabled = true; });
    if (window.innerWidth <= 780) {
      const selected = elements.lessonList.querySelector('[aria-current="true"]');
      if (selected) {
        const left = selected.offsetLeft - elements.lessonList.offsetLeft;
        const right = left + selected.offsetWidth;
        if (left < elements.lessonList.scrollLeft || right > elements.lessonList.scrollLeft + elements.lessonList.clientWidth) {
          elements.lessonList.scrollLeft = Math.max(0, left - 16);
        }
      }
    }
  }

  function renderSidebar() {
    const total = lessons().length;
    const completed = completedLessons().length;
    elements.count.innerHTML = `<strong>${completed}</strong> / ${total} 堂已完成`;
    elements.progress.max = total || 1;
    elements.progress.value = completed;
    elements.progress.setAttribute('aria-valuetext', `${total} 堂課中已完成 ${completed} 堂`);
    const active = getLesson(state.data.progress.activeLesson);
    const activeProgress = active ? getProgress(active.id) : null;
    const next = lessons().find((lesson) => getProgress(lesson.id).status !== 'completed');
    elements.resume.textContent = active && activeProgress.status === 'in_progress'
      ? `目前接續：第 ${active.id} 課 ${active.title}`
      : next ? `下一堂建議：第 ${next.id} 課 ${next.title}` : '15 堂課的學習紀錄已完成。';
    elements.saved.textContent = `共用進度更新：${dateLabel(state.data.progress.updatedAt)}`;
    elements.lessonList.innerHTML = lessons().map((lesson) => {
      const progress = getProgress(lesson.id);
      const selected = lesson.id === state.selectedLesson;
      const activeNow = lesson.id === state.data.progress.activeLesson && progress.status === 'in_progress';
      const marker = progress.status === 'completed' ? icon('check', 'nav-state') : `<span class="nav-state ${activeNow ? 'active' : 'pending'}" aria-hidden="true"></span>`;
      return `<button type="button" class="lesson-nav${selected ? ' is-selected' : ''}${progress.status === 'completed' ? ' is-completed' : ''}" data-lesson="${escapeHtml(lesson.id)}" ${selected ? 'aria-current="true"' : ''} aria-label="第 ${escapeHtml(lesson.id)} 課，${escapeHtml(lesson.title)}，${statusLabel(progress.status)}"><span class="lesson-number">${escapeHtml(lesson.id)}</span><span class="lesson-nav-copy"><span class="lesson-nav-title">${escapeHtml(lesson.title)}</span><span class="lesson-nav-status">${activeNow ? '目前接續' : statusLabel(progress.status)}</span></span>${marker}</button>`;
    }).join('');
  }

  function renderLesson() {
    const lesson = getLesson(state.selectedLesson);
    if (!lesson) return '<div class="empty-state"><h2>尚未找到課程</h2><p>請先從左側選擇課程。</p></div>';
    const progress = getProgress(lesson.id);
    const active = state.data.progress.activeLesson === lesson.id;
    const completed = progress.status === 'completed';
    const next = lessons().find((candidate) => Number(candidate.id) > Number(lesson.id) && getProgress(candidate.id).status !== 'completed');
    const session = !completed && (!active || progress.status === 'not_started')
      ? `<div class="session-bar"><span>${progress.status === 'not_started' ? '準備好後，從下方這一步開始。' : '接續本課目前的操作步驟。'}</span><button type="button" class="button button-primary button-small" data-action="activate">${progress.status === 'not_started' ? '開始本課' : '接續本課'}</button></div>`
      : '';
    const criteria = (lesson.completionCriteria || []).map((criterion) => `<li>${completed ? icon('check', 'check-icon') : '<span class="criteria-dot" aria-hidden="true"></span>'}<span>${escapeHtml(asText(criterion))}</span></li>`).join('');
    const mainCard = completed
      ? `<section class="completion-panel" aria-labelledby="completion-title"><div class="completion-heading"><span class="completion-symbol">${icon('check')}</span><div><h3 id="completion-title" tabindex="-1">這堂課已完成</h3><p>以下是你已經實際完成的內容。</p></div></div><ul class="criteria-list">${criteria}</ul>${renderOutputLinks(lesson)}${next ? `<div class="next-course"><p>下一堂：第 ${escapeHtml(next.id)} 課 ${escapeHtml(next.title)}</p><button type="button" class="button button-primary" data-select="${escapeHtml(next.id)}">查看下一課</button></div>` : ''}</section>${renderCompletedSteps(lesson)}`
      : renderStep(lesson, active && progress.status === 'in_progress');
    return `<header class="lesson-heading"><div class="heading-meta"><span class="eyebrow">課程 ${escapeHtml(lesson.id)}</span>${statusBadge(progress.status)}${lesson.stage ? `<span class="stage-label">${escapeHtml(asText(lesson.stage))}</span>` : ''}</div><h2>${escapeHtml(lesson.title)}</h2><p class="lesson-goal">${escapeHtml(asText(lesson.goal))}</p>${lesson.context ? `<details class="lesson-context"><summary>本課教材說明</summary><p>${escapeHtml(asText(lesson.context))}</p></details>` : ''}<div class="lesson-tools" aria-label="本課操作工具"><button type="button" class="button button-secondary" data-action="open-project">${icon('open')}開啟練習專案</button><button type="button" class="button button-secondary" data-action="copy-resume">${icon('copy')}複製接續訊息</button><p>把接續訊息貼到 Codex，就能依這一步繼續問答。</p></div></header>${session}<div class="content-grid"><div class="primary-column">${mainCard}${renderNotes(lesson)}</div><aside class="secondary-column" aria-label="練習與教學資源">${renderMedia(lesson)}${renderWorkspace(lesson)}${!completed && criteria ? `<section class="panel"><h3>本課完成的標準</h3><ul class="criteria-list">${criteria}</ul></section>` : ''}${renderFiles(lesson)}</aside></div>`;
  }

  function renderStep(lesson, canConfirm) {
    const step = currentStep(lesson);
    if (!step) return '<section class="panel"><h3>這堂課還沒有操作步驟</h3><p class="panel-subtitle">請在 Codex 對話中一起補充本課內容。</p></section>';
    const stepIndex = (lesson.steps || []).findIndex((candidate) => candidate.id === step.id);
    return `<section class="panel step-panel" aria-labelledby="step-title"><div class="step-top"><div class="step-meta"><span class="eyebrow">這次只做這一步</span><span>第 ${stepIndex + 1} / ${(lesson.steps || []).length} 步 · 已完成 ${stepCount(lesson)} 步</span></div><h3 id="step-title" tabindex="-1">${escapeHtml(step.title)}</h3></div><div class="step-body"><p class="section-label">照著操作</p>${textBlock(step.action)}${renderGuide(step)}${renderFigures(step)}<div class="success-check"><p class="section-label">${icon('check')}做到這樣就可以繼續</p>${textBlock(step.success)}</div>${renderQuiz(step)}${renderCheck(step, canConfirm)}<div class="step-actions"><button type="button" class="button button-primary" data-action="confirm" data-step="${escapeHtml(step.id)}"${canConfirm && quizPassed(step) && !state.checking ? '' : ' disabled'}${canConfirm ? '' : ' data-needs-start="true"'}>${icon('check')}我做完了，記錄進度</button><p class="small-hint">${!canConfirm ? '先按上方「開始本課」或「接續本課」，再記錄完成。' : !quizPassed(step) ? '先回答上方的小問題，再記錄完成。' : (step.checks || []).length ? '按下後會先自動檢查練習檔，通過才記錄。進度會同步給 Codex。' : '確認已看到上方的成功結果，再按這個按鈕。進度會同步給 Codex。'}</p></div>${step.help ? `<details class="step-help"><summary>找不到按鈕或畫面不同？</summary>${textBlock(step.help)}<p class="small-hint">仍不確定時，複製接續訊息到 Codex，並附上目前畫面。</p></details>` : ''}</div></section>`;
  }

  function renderGuide(step) {
    const guide = step.guide || [];
    if (!guide.length) return '';
    return `<ol class="guide-steps">${guide.map((item) => `<li>${item.mark ? `<span class="mark-badge" aria-label="對應圖上編號 ${escapeHtml(item.mark)}">${escapeHtml(item.mark)}</span>` : '<span class="mark-badge is-empty" aria-hidden="true"></span>'}<div><p class="guide-where">${escapeHtml(item.where)}</p><p class="guide-do">${escapeHtml(item.do)}</p>${item.see ? `<p class="guide-see">會看到：${escapeHtml(item.see)}</p>` : ''}</div></li>`).join('')}</ol>`;
  }

  function renderFigures(step) {
    const clamp = (value) => Math.min(98, Math.max(2, Number(value) || 0));
    return (step.figures || []).map((figure) => {
      const marks = figure.marks || [];
      return `<figure class="shot"><a class="shot-frame" href="${fileUrl(figure.image)}" target="_blank" rel="noopener" aria-label="放大查看：${escapeHtml(figure.caption)}"><img src="${fileUrl(figure.image)}" alt="${escapeHtml(figure.caption)}" loading="lazy">${marks.map((mark) => `<span class="shot-mark" style="left:${clamp(mark.x)}%;top:${clamp(mark.y)}%" aria-hidden="true">${escapeHtml(mark.n)}</span>`).join('')}</a><figcaption><span>${escapeHtml(figure.caption)}</span>${marks.length ? `<ol class="shot-legend">${marks.map((mark) => `<li><span class="mark-badge">${escapeHtml(mark.n)}</span>${escapeHtml(mark.label)}</li>`).join('')}</ol>` : ''}</figcaption></figure>`;
    }).join('');
  }

  const quizPassed = (step) => !step.quiz || state.quiz.get(step.id) === step.quiz.answer;

  function renderQuiz(step) {
    const quiz = step.quiz;
    if (!quiz) return '';
    const chosen = state.quiz.get(step.id);
    const right = chosen === quiz.answer;
    const feedback = chosen === undefined ? '' : `<p class="quiz-feedback ${right ? 'is-right' : 'is-wrong'}" role="status">${right ? '答對了。' : '再想想看，可以對照上方截圖。'}${right && quiz.explain ? ' ' + escapeHtml(quiz.explain) : ''}</p>`;
    return `<div class="quiz" role="group" aria-labelledby="quiz-${escapeHtml(step.id)}"><p class="section-label" id="quiz-${escapeHtml(step.id)}">確認一下：${escapeHtml(quiz.question)}</p><div class="quiz-options">${(quiz.options || []).map((option, index) => `<button type="button" class="quiz-option${chosen === index ? (right ? ' is-right' : ' is-wrong') : ''}" data-action="quiz" data-step="${escapeHtml(step.id)}" data-choice="${index}" aria-pressed="${chosen === index}">${escapeHtml(option)}</button>`).join('')}</div>${feedback}</div>`;
  }

  const CHECK_NAMES = { project_open: '是否開對練習副本', saved_since_start: '是否已儲存', erc: 'ERC', drc: 'DRC', footprints: '封裝是否都已指定', new_file: '是否產生新檔案', new_zip: '輸出 ZIP 內容' };

  function renderCheck(step, canConfirm) {
    const checks = step.checks || [];
    if (!checks.length) return '';
    const result = state.checks.get(step.id);
    const badge = (item) => item.ok === true ? '<span class="check-state is-ok">通過</span>'
      : item.ok === false ? `<span class="check-state ${item.required ? 'is-fail' : 'is-info'}">${item.required ? '未通過' : '參考'}</span>`
      : '<span class="check-state is-info">無法判斷</span>';
    const findings = (item) => (item.findings || []).length ? `<ul class="finding-list">${item.findings.map((f) => `<li class="${f.ignorable ? 'is-ignorable' : f.severity === 'error' ? 'is-error' : ''}"><strong>${f.severity === 'error' ? '錯誤' : '警告'} ×${escapeHtml(f.count)}</strong> <code>${escapeHtml(f.type)}</code>${f.ignorable ? ' <span class="tag">可忽略</span>' : ''}<span>${escapeHtml(f.explain || f.description)}</span></li>`).join('')}</ul>` : '';
    const body = result
      ? `<ul class="check-list">${result.items.map((item) => `<li>${badge(item)}<div><strong>${escapeHtml(item.label)}${item.required ? '' : '（參考）'}</strong><span>${escapeHtml(item.detail)}</span>${findings(item)}</div></li>`).join('')}</ul>`
      : `<p class="panel-subtitle">做完並存檔後，可以先檢查：${checks.map((check) => escapeHtml(CHECK_NAMES[check.type] || check.type)).join('、')}。</p>`;
    return `<div class="auto-check"><div class="auto-check-head"><p class="section-label">自動檢查</p><button type="button" class="button button-secondary button-small" data-action="run-check" data-step="${escapeHtml(step.id)}"${state.checking || !canConfirm ? ' disabled' : ''}>${state.checking ? '檢查中…' : result ? '重新檢查' : '先檢查一次'}</button></div>${body}</div>`;
  }

  async function runCheck(stepId) {
    if (state.checking) return;
    state.checking = true;
    render();
    try {
      const result = await request('/api/check', { method: 'POST', body: JSON.stringify({ lessonId: state.selectedLesson, stepId }) });
      if (result.check) state.checks.set(stepId, result.check);
      showNotice(result.check?.passed ? '自動檢查通過，可以記錄完成。' : '自動檢查還沒通過，請看「自動檢查」的說明。', result.check?.passed ? 'success' : 'warning');
    } catch (error) {
      showNotice(error.message || '自動檢查沒有完成。', 'error', true);
    } finally {
      state.checking = false;
      render();
    }
  }

  function renderNotes(lesson) {
    const notes = asText(getProgress(lesson.id).notes);
    const draft = state.drafts.get(lesson.id) || '';
    return `<section class="panel"><form class="note-form" data-note-form><label for="step-note">記下操作結果或卡住的地方</label><p class="panel-subtitle">更新目前筆記摘要；歷次紀錄保存在專案中。需要教學協助時，再貼接續訊息到 Codex。</p><textarea id="step-note" data-note-input rows="3" placeholder="例如：已找到 Edge.Cuts，但不知道如何把圓形改到這一層。"${state.busy ? ' disabled' : ''}>${escapeHtml(draft)}</textarea><div class="note-footer"><p>保存在專案內，與 Codex 共用。</p><button type="submit" class="button button-secondary button-small" data-save-note${draft.trim() ? '' : ' disabled'}>儲存筆記</button></div></form>${notes ? `<details class="saved-notes"><summary>查看目前筆記摘要</summary><pre>${escapeHtml(notes)}</pre></details>` : ''}</section>`;
  }

  function renderWorkspace(lesson) {
    const progress = getProgress(lesson.id);
    const isFirst = lesson.id === '01';
    const hasWorkspace = Boolean(progress.workspace) || isFirst;
    return `<section class="panel workspace-panel"><h3>實際操作的檔案</h3><p>${isFirst ? '第 01 課接續你已完成的專案與製板檔。' : hasWorkspace ? '繼續使用本課練習副本，變更會留在自己的練習中。' : '使用頁首「開啟練習專案」，會建立本課練習副本並記錄為開始學習。'}</p><div class="workspace-actions"><button type="button" class="button button-quiet button-small" data-action="open-folder"${hasWorkspace ? '' : ' disabled data-needs-start="true"'}>${icon('folder')}開啟練習資料夾</button></div>${progress.workspace ? `<span class="workspace-path">${escapeHtml(progress.workspace)}</span>` : ''}</section>`;
  }

  function mediaLink(link) {
    const url = String(link.url || '');
    if (!/^https:\/\/(www\.)?youtube\.com\//.test(url)) return '';
    return `<a class="file-label" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(link.label || url)}</a>`;
  }

  function renderMedia(lesson) {
    const own = (lesson.media || []).map(mediaLink).filter(Boolean);
    if (!own.length) return '';
    const series = ((state.data.catalog.sourceMedia || {}).links || []).map(mediaLink).filter(Boolean);
    return `<section class="panel media-panel"><h3>原作者教學影片</h3><ul class="file-list">${own.map((item) => `<li><span class="file-type">本課影片</span>${item}</li>`).join('')}${series.map((item) => `<li><span class="file-type">整個系列</span>${item}</li>`).join('')}</ul><p class="file-description">英文影片，以 KiCad 7 示範；按鈕與選單以你目前的 KiCad 畫面為準。原作者文章網站已無法開啟。</p></section>`;
  }

  function fileTypeLabel(file) {
    const type = String(file.type || '').toLowerCase();
    const labels = { reference: '原始教材', source: '原始教材', output: '學習成果', artifact: '學習成果', project: 'KiCad 專案', guide: '操作說明', journal: '學習紀錄', learner: '練習檔案', workspace: '練習副本' };
    return labels[type] || (/\.zip$/i.test(file.path || '') ? '製板輸出' : /\.kicad_(pro|pcb|sch)$/i.test(file.path || '') ? '原始教材' : '課程檔案');
  }

  function renderFiles(lesson) {
    const files = lesson.files || [];
    if (!files.length) return '';
    return `<section class="panel files-panel"><h3>教材與成果</h3><ul class="file-list">${files.map((file) => {
      const path = String(file.path || '');
      const isKiCad = /\.kicad_(pro|pcb|sch)$|\.(pro|sch|brd)$/i.test(path);
      return `<li><span class="file-type">${escapeHtml(fileTypeLabel(file))}</span>${isKiCad ? `<span class="file-label">${escapeHtml(file.label || path.split('/').pop())}</span><span class="file-description">請使用上方「開啟練習專案」。</span>` : `<a class="file-label" href="${fileUrl(path, /\.md$/i.test(path))}" target="_blank" rel="noopener"${/\.zip$/i.test(path) ? ' download' : ''}>${escapeHtml(file.label || path.split('/').pop())}</a>`}</li>`;
    }).join('')}</ul></section>`;
  }

  function renderOutputLinks(lesson) {
    return (lesson.files || []).filter((file) => file.type === 'output' && /\.zip$/i.test(file.path || '')).map((file) => `<a class="button button-secondary" href="${fileUrl(file.path)}" download>${icon('folder')}${escapeHtml(file.label || '下載製板 ZIP')}</a>`).join('');
  }

  function renderCompletedSteps(lesson) {
    if (!(lesson.steps || []).length) return '';
    return `<details class="review-steps"><summary>回看本課的 ${(lesson.steps || []).length} 個操作步驟</summary><ol class="review-step-list">${lesson.steps.map((step) => `<li><strong>${escapeHtml(step.title)}</strong><span>${escapeHtml(asText(step.action))}</span></li>`).join('')}</ol></details>`;
  }

  function renderOverview() {
    const completed = completedLessons().length;
    const learning = lessons().filter((lesson) => getProgress(lesson.id).status === 'in_progress').length;
    return `<header class="page-heading"><div class="eyebrow">15 堂課的共同入口</div><h2>課程總覽</h2><p>${escapeHtml(state.data.catalog.versionNote || '課程依原始教材排序；操作說明依你目前使用的 KiCad 版本調整。')}</p></header><div class="overview-summary"><div><strong>${completed}<span style="font-size:15px;font-weight:400"> / ${lessons().length}</span></strong><p>堂課已完成</p></div><div><p>${learning} 堂學習中</p><p>每一步都由你的實際操作確認。</p></div></div><div class="overview-list">${lessons().map((lesson) => {
      const progress = getProgress(lesson.id);
      return `<article class="overview-card"><div class="overview-card-top"><span class="eyebrow">課程 ${escapeHtml(lesson.id)}</span>${statusBadge(progress.status)}</div><h3>${escapeHtml(lesson.title)}</h3><p>${escapeHtml(asText(lesson.goal))}</p><div class="overview-card-bottom"><span>${stepCount(lesson)} / ${(lesson.steps || []).length} 步已完成</span><button type="button" class="button button-secondary button-small" data-select="${escapeHtml(lesson.id)}">${progress.status === 'completed' ? '回看課程' : '查看本課'}</button></div></article>`;
    }).join('')}</div>`;
  }

  function renderJournal() {
    const journal = state.data.journal || [];
    const guides = state.data.guides || [];
    const noted = lessons().filter((lesson) => asText(getProgress(lesson.id).notes).trim());
    return `<header class="page-heading"><div class="eyebrow">把學會的內容留下來</div><h2>學習紀錄與操作圖解</h2><p>收錄這次問答中遇到的問題、解法，以及後續各課的操作筆記。</p></header><section class="journal-section"><h3>課程學習紀錄</h3><div class="journal-list">${journal.length ? journal.map((entry) => `<article class="journal-card">${entry.date ? `<time datetime="${escapeHtml(entry.date)}">${escapeHtml(entry.date)}</time>` : ''}<h4>${escapeHtml(entry.title)}</h4><p>${escapeHtml(asText(entry.summary))}</p><a class="button button-secondary button-small" href="${fileUrl(entry.path, true)}" target="_blank" rel="noopener">閱讀完整紀錄</a></article>`).join('') : '<p class="panel-subtitle">完成操作後，學習紀錄會集中顯示在這裡。</p>'}</div></section>${guides.length ? `<section class="journal-section"><h3>對照畫面操作</h3><div class="guide-grid">${guides.map((guide) => `<article class="guide-card"><a class="guide-image" href="${fileUrl(guide.path)}" target="_blank" rel="noopener" aria-label="放大查看：${escapeHtml(guide.title)}"><img src="${fileUrl(guide.path)}" alt="${escapeHtml(guide.title)}" loading="lazy"></a><div class="guide-copy"><h4>${escapeHtml(guide.title)}</h4><p>${escapeHtml(asText(guide.description))}</p><a href="${fileUrl(guide.path)}" target="_blank" rel="noopener">放大查看圖解</a></div></article>`).join('')}</div></section>` : ''}<section class="journal-section"><h3>各課目前筆記摘要</h3>${noted.length ? `<div class="journal-note-list">${noted.map((lesson) => `<article class="journal-note"><h4>課程 ${escapeHtml(lesson.id)} · ${escapeHtml(lesson.title)}</h4><pre class="record-note">${escapeHtml(asText(getProgress(lesson.id).notes))}</pre></article>`).join('')}</div>` : '<p class="panel-subtitle">尚未新增筆記。可在「本次操作」記下觀察結果或卡住的地方。</p>'}</section>`;
  }

  async function mutate(action, extra = {}) {
    if (state.busy || !state.data) return;
    const lessonId = state.selectedLesson;
    let saved = false;
    preserveDraft();
    setBusy(true);
    try {
      const result = await request('/api/progress', { method: 'POST', body: JSON.stringify({ expectedRevision: state.data.progress.revision, action, lessonId, ...extra }) });
      if (!result.progress) throw new Error('本機服務沒有回傳更新後的進度，請重新讀取。');
      state.data.progress = result.progress;
      saved = true;
      if (action === 'add_note') state.drafts.delete(lessonId);
      render();
      if (action === 'activate_lesson') showNotice('已開始接續本課。先完成畫面中的這一步即可。');
      else if (action === 'confirm_step') showNotice(getProgress(lessonId).status === 'completed' ? '本課已完成，學習成果已記錄。可先回看成果，再決定下一課。' : '這一步已記錄。下方已顯示接下來的一步。');
      else showNotice('本課筆記摘要已更新；歷次紀錄保存在專案中。');
    } catch (error) {
      await handleActionError(error);
    } finally {
      setBusy(false);
      render();
      if (saved && action !== 'add_note') (document.getElementById('step-title') || document.getElementById('completion-title'))?.focus({ preventScroll: true });
    }
  }

  async function handleActionError(error) {
    if (error.data?.check) {
      state.checks.set(error.data.check.stepId, error.data.check);
      showNotice(error.message, 'warning', true);
    } else if (error.status === 409) {
      await bootstrap({ quiet: true, force: true });
      showNotice('進度已由其他視窗或 Codex 更新。已載入最新資料；請確認目前步驟，再重新操作。', 'warning', true);
    } else showNotice(error.message || '操作沒有完成，請稍後重試。', 'error', true);
  }

  async function openPractice(target) {
    if (state.busy || !state.data) return;
    const lessonId = state.selectedLesson;
    preserveDraft();
    setBusy(true);
    try {
      const workspace = await request('/api/workspace', { method: 'POST', body: JSON.stringify({ expectedRevision: state.data.progress.revision, lessonId }) });
      if (workspace.progress) state.data.progress = workspace.progress;
      const opened = await request('/api/open', { method: 'POST', body: JSON.stringify({ lessonId, target }) });
      if (!opened.ok) throw new Error('檔案已準備好，但尚未開啟。請從練習資料夾查看。');
      render();
      showNotice(`${workspace.created ? '已建立本課練習副本。' : ''}${target === 'folder' ? '已開啟練習資料夾。' : '已送出開啟練習專案的要求，請查看 KiCad 視窗。'}`);
    } catch (error) {
      await handleActionError(error);
    } finally {
      setBusy(false);
      render();
    }
  }

  function resumeMessage() {
    const lesson = getLesson(state.selectedLesson);
    if (!lesson) return '';
    const progress = getProgress(lesson.id);
    const step = currentStep(lesson);
    return [
      '請接續這個專案的 KiCad 引導式學習。',
      `專案：${state.data.projectRoot || '目前專案根目錄'}`,
      '請先讀取根目錄 AGENTS.md、learning/catalog.json、learning/progress.json，以及相關學習紀錄，以檔案中的最新進度為準。',
      `我目前查看第 ${lesson.id} 課：${lesson.title}。網站顯示狀態：${statusLabel(progress.status)}。`,
      progress.status === 'completed' ? '這堂課已完成，請先回顧成果；尚未開始的下一課，需要我實際開始後再記錄。' : step ? `目前待確認步驟：${step.id} ${step.title}。` : '',
      '請借鏡第 01 課的問答方式，一次帶我完成一小步：說明具體按鈕與操作、成功時應看到的畫面，再等我回報。遇到找不到按鈕或畫面不同，請依我的截圖解釋；需要時提供清楚標示的操作圖。',
      '只有我確認實際完成，才更新共用進度。不要因為教材中已有完成檔，就把課程當作已學會。',
      (state.drafts.get(lesson.id) || '').trim() ? `我目前補充：${state.drafts.get(lesson.id).trim()}` : '',
    ].filter(Boolean).join('\n\n');
  }

  async function copyResume(trigger) {
    preserveDraft();
    const message = resumeMessage();
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(message);
      showNotice('接續訊息已複製。貼到目前這個 Codex 對話，即可依本課進度繼續教學。');
    } catch {
      state.clipboardTrigger = trigger;
      elements.clipboardText.value = message;
      elements.clipboard.hidden = false;
      elements.clipboardText.focus();
      elements.clipboardText.select();
      elements.clipboardText.setSelectionRange(0, message.length);
    }
  }

  function closeClipboard() {
    elements.clipboard.hidden = true;
    if (state.clipboardTrigger?.isConnected) state.clipboardTrigger.focus();
  }

  function selectLesson(id) {
    if (!getLesson(id) || state.busy) return;
    preserveDraft();
    state.selectedLesson = id;
    state.view = 'lesson';
    render();
    document.getElementById('main-content').focus({ preventScroll: true });
    if (window.innerWidth <= 780) document.getElementById('main-content').scrollIntoView({ block: 'start' });
  }

  document.addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button || button.disabled) return;
    if (button.dataset.lesson || button.dataset.select) { selectLesson(button.dataset.lesson || button.dataset.select); return; }
    if (button.dataset.view) {
      preserveDraft(); state.view = button.dataset.view; render(); return;
    }
    switch (button.dataset.action) {
      case 'activate': void mutate('activate_lesson'); break;
      case 'confirm': void mutate('confirm_step', { stepId: button.dataset.step }); break;
      case 'run-check': void runCheck(button.dataset.step); break;
      case 'quiz': state.quiz.set(button.dataset.step, Number(button.dataset.choice)); render(); break;
      case 'open-project': void openPractice('project'); break;
      case 'open-folder': void openPractice('folder'); break;
      case 'copy-resume': void copyResume(button); break;
      case 'close-clipboard': closeClipboard(); break;
      case 'retry': void bootstrap({ force: true }); break;
      default: break;
    }
  });
  document.addEventListener('input', (event) => {
    if (!event.target.matches('[data-note-input]')) return;
    state.drafts.set(state.selectedLesson, event.target.value);
    const save = document.querySelector('[data-save-note]');
    if (save) save.disabled = state.busy || !event.target.value.trim();
  });
  document.addEventListener('submit', (event) => {
    if (!event.target.matches('[data-note-form]')) return;
    event.preventDefault();
    const note = document.querySelector('[data-note-input]')?.value.trim();
    if (note) void mutate('add_note', { note });
  });
  document.addEventListener('keydown', (event) => {
    if (elements.clipboard.hidden) return;
    if (event.key === 'Escape') { event.preventDefault(); closeClipboard(); }
    if (event.key === 'Tab') {
      const textarea = elements.clipboardText;
      const close = elements.clipboard.querySelector('button');
      if (event.shiftKey && document.activeElement === textarea) { event.preventDefault(); close.focus(); }
      else if (!event.shiftKey && document.activeElement === close) { event.preventDefault(); textarea.focus(); }
    }
  });
  elements.clipboard.addEventListener('click', (event) => { if (event.target === elements.clipboard) closeClipboard(); });
  window.addEventListener('beforeunload', (event) => {
    preserveDraft();
    if ([...state.drafts.values()].some((draft) => draft.trim())) { event.preventDefault(); event.returnValue = ''; }
  });
  window.setInterval(() => {
    if (document.hidden || state.busy || state.loading || document.activeElement?.matches('[data-note-input]') || !elements.clipboard.hidden) return;
    void bootstrap({ quiet: true });
  }, 15000);

  void bootstrap({ quiet: true });
})();
