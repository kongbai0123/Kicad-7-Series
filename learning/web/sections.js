(() => {
  'use strict';

  // One page per section: every step of the section in order, text left and real screenshot right.
  // Navigation is never gated; progress is still written only through /api/progress.
  const state = {
    data: null,
    route: null,
    busy: false,
    loading: false,
    checking: null,
    checks: new Map(),
    quiz: new Map(),
    drafts: new Map(),
    confirmReset: null,
    noticeTimer: null,
    clipboardTrigger: null,
    lightboxTrigger: null,
  };
  const $ = (id) => document.getElementById(id);
  const STORE = 'kicad-sections-v1:';
  const read = (key) => { try { return localStorage.getItem(STORE + key); } catch { return null; } };
  const write = (key, value) => { try { localStorage.setItem(STORE + key, value); } catch { /* storage is optional */ } };
  const narrow = matchMedia('(max-width: 1099px)');
  const isMini = () => !narrow.matches && document.body.classList.contains('nav-mini');
  let expanded;
  try { expanded = new Set(JSON.parse(read('expanded')) || []); } catch { expanded = new Set(); }
  if (read('nav-mini') === '1') document.body.classList.add('nav-mini');

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const asText = (value) => Array.isArray(value) ? value.map(asText).join('\n') : String(value ?? '');
  const fileUrl = (path, readMd = false) => {
    const normalized = String(path || '').replaceAll('\\', '/').replace(/^\/+/, '');
    if (!normalized || normalized.split('/').some((part) => part === '..')) return '#';
    return `/${readMd ? 'read' : 'files'}/${normalized.split('/').map(encodeURIComponent).join('/')}`;
  };
  const dateLabel = (value) => (!value || Number.isNaN(Date.parse(value))) ? '尚未記錄'
    : new Intl.DateTimeFormat('zh-TW', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value));
  const splitSentences = (text) => asText(text).split(/[；。]\s*/).map((s) => s.trim()).filter(Boolean);

  const lessons = () => state.data?.catalog?.lessons || [];
  const lessonById = (id) => lessons().find((lesson) => lesson.id === id);
  const progressOf = (id) => state.data?.progress?.lessons?.[id] || { status: 'not_started', completedSteps: [], evidence: [], notes: '', workspace: null };
  const doneSet = (id) => new Set(progressOf(id).completedSteps || []);
  const stepById = (lesson, id) => lesson.steps.find((step) => step.id === id);
  const sectionSteps = (lesson, section) => section.steps.map((id) => stepById(lesson, id)).filter(Boolean);
  const sectionNumber = (lesson, section) => `${Number(lesson.id)}.${lesson.sections.indexOf(section) + 1}`;
  const totalSteps = () => lessons().reduce((n, lesson) => n + lesson.steps.length, 0);
  const totalDone = () => lessons().reduce((n, lesson) => n + doneSet(lesson.id).size, 0);
  const firstOpenSection = (lesson) => {
    const done = doneSet(lesson.id);
    return lesson.sections.find((section) => section.steps.some((id) => !done.has(id))) || lesson.sections[0];
  };
  const reference = () => state.data?.reference;

  class HttpError extends Error {
    constructor(message, status, data) { super(message); this.status = status; this.data = data; }
  }
  async function request(url, options = {}) {
    const response = await fetch(url, { cache: 'no-store', ...options, headers: options.body ? { 'Content-Type': 'application/json' } : {} });
    const type = response.headers.get('content-type') || '';
    const data = type.includes('application/json') ? await response.json() : { error: await response.text() };
    if (!response.ok) throw new HttpError(asText(data.error || `本機服務回應錯誤（${response.status}）。`), response.status, data);
    return data;
  }
  function notice(message, type = 'success', sticky = false) {
    clearTimeout(state.noticeTimer);
    const box = $('notice');
    box.textContent = message;
    box.className = `notice${type === 'success' ? '' : ` ${type}`}`;
    box.hidden = false;
    if (!sticky) state.noticeTimer = setTimeout(() => { box.hidden = true; }, 6000);
  }

  // ---------- Routing ----------
  function parseRoute() {
    const parts = location.hash.replace(/^#\/?/, '').split('/').filter(Boolean);
    if (parts[0] === 'overview' || parts[0] === 'journal') return { view: parts[0] };
    if (parts[0] === '00') return { view: 'reference' };
    const lesson = lessonById(parts[0]);
    if (!lesson) {
      const active = lessonById(state.data.progress.activeLesson) || lessons()[0];
      return { view: 'section', lesson, redirect: `#/${active.id}/${firstOpenSection(active).id}` };
    }
    const section = lesson.sections.find((item) => item.id === parts[1]);
    if (!section) return { view: 'section', redirect: `#/${lesson.id}/${firstOpenSection(lesson).id}` };
    return { view: 'section', lesson, section, step: parts[2] };
  }
  function go() {
    if (!state.data) return;
    const route = parseRoute();
    if (route.redirect) { history.replaceState(null, '', route.redirect); return go(); }
    const changed = JSON.stringify([route.view, route.lesson?.id, route.section?.id]) !== JSON.stringify([state.route?.view, state.route?.lesson?.id, state.route?.section?.id]);
    state.route = route;
    if (route.lesson) expanded.add(route.lesson.id);
    renderAll();
    closeDrawer();
    if (route.step) document.getElementById(`step-${route.step}`)?.scrollIntoView({ block: 'start' });
    else if (changed) { scrollTo({ top: 0 }); $('main').focus({ preventScroll: true }); }
  }

  // ---------- Navigation ----------
  function syncToggle() {
    const shown = narrow.matches ? document.body.classList.contains('nav-open') : !document.body.classList.contains('nav-mini');
    $('nav-toggle').textContent = shown ? '« 收合章節' : '☰ 展開章節';
    $('nav-toggle').setAttribute('aria-expanded', String(shown));
  }
  function closeDrawer() {
    document.body.classList.remove('nav-open');
    $('nav-backdrop').hidden = true;
    syncToggle();
  }
  function renderNav() {
    const route = state.route || {};
    $('nav-count').textContent = `${lessons().length + (reference() ? 1 : 0)} 章`;
    $('nav-total').textContent = `${totalDone()} / ${totalSteps()} 小步已確認 · 練習題 ${lessons().filter((l) => progressOf(l.id).challenge).length} / ${lessons().length}`;
    const ref = reference();
    const refHtml = ref ? `<div class="ch${route.view === 'reference' ? ' is-current' : ''}"><a class="ch-row" href="#/00" style="text-decoration:none;color:inherit"><span class="ch-num">0</span><span class="ch-copy"><span class="ch-title">${escapeHtml(ref.title)}</span><span class="ch-meta">速查，不計入進度</span></span></a></div>` : '';
    $('nav-tree').innerHTML = refHtml + lessons().map((lesson) => {
      const done = doneSet(lesson.id);
      const open = expanded.has(lesson.id) || isMini();
      const current = route.lesson?.id === lesson.id;
      const sections = open ? `<ol class="sec-list" id="sec-${lesson.id}">${lesson.sections.map((section) => {
        const count = section.steps.filter((id) => done.has(id)).length;
        const here = current && route.section?.id === section.id;
        return `<li><a class="sec-link${count === section.steps.length ? ' is-done' : ''}" href="#/${lesson.id}/${section.id}"${here ? ' aria-current="page"' : ''}><span class="sec-num">${sectionNumber(lesson, section)}</span><span class="sec-title">${escapeHtml(section.title)}</span><span class="sec-count">${count}/${section.steps.length}</span></a></li>`;
      }).join('')}</ol>` : '';
      return `<div class="ch${current ? ' is-current' : ''}"><button type="button" class="ch-row" data-toggle="${lesson.id}" aria-expanded="${open}" aria-controls="sec-${lesson.id}"><span class="ch-num">${Number(lesson.id)}</span><span class="ch-copy"><span class="ch-title">${escapeHtml(lesson.title)}</span><span class="ch-meta">${done.size} / ${lesson.steps.length} 小步已確認${progressOf(lesson.id).challenge ? ' · 練習題 ✓' : ''}</span></span></button>${sections}</div>`;
    }).join('');
  }
  function renderCrumb() {
    const route = state.route;
    let html = '<a href="#/overview">KiCad 邊做邊學</a>';
    if (route.view === 'reference') html += ' › <strong>第 00 章 速查</strong>';
    else if (route.view === 'overview') html += ' › <strong>課程總覽</strong>';
    else if (route.view === 'journal') html += ' › <strong>學習紀錄</strong>';
    else html += ` › 第 ${route.lesson.id} 課 ${escapeHtml(route.lesson.title)} › <strong>${sectionNumber(route.lesson, route.section)} ${escapeHtml(route.section.title)}</strong>`;
    $('crumb').innerHTML = html;
    $('sync').textContent = `共用進度：${dateLabel(state.data.progress.updatedAt)}`;
    document.title = route.view === 'section' ? `${sectionNumber(route.lesson, route.section)} ${route.section.title} · KiCad 邊做邊學` : 'KiCad 邊做邊學';
  }

  // ---------- Section page ----------
  const ROLE_LABEL = (role) => state.data.catalog.figureRoles?.[role] || '';
  function renderFigure(figure, index) {
    const clamp = (v) => Math.min(98, Math.max(2, Number(v) || 0));
    const marks = figure.marks || [];
    return `<figure class="fig">${figure.role ? `<span class="fig-role ${escapeHtml(figure.role)}">${escapeHtml(ROLE_LABEL(figure.role))}</span>` : ''}<button type="button" class="shot" data-zoom="${index}" aria-label="放大查看：${escapeHtml(figure.caption)}"><img src="${fileUrl(figure.image)}" alt="${escapeHtml(figure.caption)}" loading="lazy">${marks.map((m) => `<span class="shot-mark" style="left:${clamp(m.x)}%;top:${clamp(m.y)}%" aria-hidden="true">${escapeHtml(m.n)}</span>`).join('')}</button><figcaption>${escapeHtml(figure.caption)} · 點圖可放大</figcaption>${marks.length ? `<ol class="legend">${marks.map((m) => `<li><span class="mark">${escapeHtml(m.n)}</span><span>${escapeHtml(m.label)}</span></li>`).join('')}</ol>` : ''}</figure>`;
  }
  const CHECK_NAMES = { project_open: '是否開對練習副本', saved_since_start: '是否已儲存', erc: 'ERC', drc: 'DRC', footprints: '封裝是否都已指定', new_file: '是否產生新檔案', new_zip: '輸出 ZIP 內容' };
  function renderCheck(key, checks, payload) {
    if (!(checks || []).length) return '';
    const result = state.checks.get(key);
    const badge = (item) => item.ok === true ? '<span class="state ok">通過</span>'
      : item.ok === false ? `<span class="state ${item.required ? 'fail' : 'info'}">${item.required ? '未通過' : '參考'}</span>`
      : `<span class="state warn">${item.required ? '無法驗證' : '無法判斷'}</span>`;
    const findings = (item) => (item.findings || []).length ? `<ul class="findings">${item.findings.map((f) => `<li class="${f.ignorable ? 'is-ignorable' : ''}"><strong>${f.severity === 'error' ? '錯誤' : '警告'} ×${escapeHtml(f.count)}</strong> <code>${escapeHtml(f.type)}</code>${f.ignorable ? ' <span class="tag">可忽略</span>' : ''} ${escapeHtml(f.explain || f.description)}</li>`).join('')}</ul>` : '';
    const body = result
      ? `<ul class="check-list">${result.items.map((item) => `<li>${badge(item)}<div><strong>${escapeHtml(item.label)}${item.required ? '' : '（參考）'}</strong><span>${escapeHtml(item.detail)}</span>${findings(item)}</div></li>`).join('')}</ul>${(result.unverified || []).length ? '<p class="hint">有必要檢查無法執行；仍可記錄完成，但會標為「無法自動驗證」，不算工具通過。</p>' : ''}`
      : `<p class="hint">做完並存檔後可先檢查：${checks.map((c) => escapeHtml(CHECK_NAMES[c.type] || c.type)).join('、')}。記錄完成時也會自動檢查。</p>`;
    const running = state.checking === key;
    return `<div class="auto-check"><div class="auto-check-head"><p>自動檢查練習檔</p><button type="button" class="btn btn-small" data-action="run-check" data-key="${escapeHtml(key)}" data-payload="${escapeHtml(JSON.stringify(payload))}"${state.checking ? ' disabled' : ''}>${running ? '檢查中…' : result ? '重新檢查' : '先檢查一次'}</button></div>${body}</div>`;
  }
  function evidenceBadges(lessonId, stepId) {
    const evidence = (progressOf(lessonId).evidence || []).filter((e) => e.stepId === stepId);
    const kinds = new Set(evidence.map((e) => e.kind));
    const at = evidence.find((e) => e.kind === 'user_confirmed')?.at;
    return `<div class="badges"><span class="state ok">✓ 學員確認 ${escapeHtml(dateLabel(at))}</span>${kinds.has('tool_verified') ? '<span class="state ok">工具驗證</span>' : ''}${kinds.has('tool_unverified') ? '<span class="state warn">無法自動驗證</span>' : ''}</div>`;
  }
  function renderQuiz(step) {
    const quiz = step.quiz;
    if (!quiz) return '';
    const chosen = state.quiz.get(step.id);
    const right = chosen === quiz.answer;
    const feedback = chosen === undefined ? '' : `<p class="quiz-feedback" role="status">${right ? `答對了。${escapeHtml(quiz.explain || '')}` : '再想想看，可以對照右側截圖。'}</p>`;
    return `<div class="quiz" role="group" aria-label="想一想"><p><strong>想一想（選答）：</strong>${escapeHtml(quiz.question)}</p><div class="quiz-options">${quiz.options.map((option, i) => `<button type="button" class="quiz-option${chosen === i ? (right ? ' is-right' : ' is-wrong') : ''}" data-action="quiz" data-step="${escapeHtml(step.id)}" data-choice="${i}" aria-pressed="${chosen === i}">${escapeHtml(option)}</button>`).join('')}</div>${feedback}</div>`;
  }
  function renderStep(lesson, step, index, figureBase) {
    const done = doneSet(lesson.id).has(step.id);
    const guide = step.guide || [];
    const figures = step.figures || [];
    const ops = guide.length ? `<p class="label">怎麼做</p><ol class="ops">${guide.map((g) => `<li>${g.mark ? `<span class="mark" aria-label="對應圖上編號 ${escapeHtml(g.mark)}">${escapeHtml(g.mark)}</span>` : '<span class="mark is-empty" aria-hidden="true"></span>'}<div><p class="where">${escapeHtml(g.where)}</p><p class="do">${escapeHtml(g.do)}</p>${g.see ? `<p class="see">會看到：${escapeHtml(g.see)}</p>` : ''}</div></li>`).join('')}</ol>` : '';
    const success = splitSentences(step.success);
    const pitfalls = (step.pitfalls || []).length ? `<div class="pitfalls"><p class="label warn" style="margin-top:0">常見失誤</p><ul>${step.pitfalls.map((p) => `<li>${escapeHtml(p)}</li>`).join('')}</ul></div>` : '';
    const confirm = done
      ? `<div class="confirm-row">${evidenceBadges(lesson.id, step.id)}</div>`
      : `<div class="confirm-row"><button type="button" class="btn btn-primary" data-action="confirm" data-lesson="${lesson.id}" data-step="${escapeHtml(step.id)}"${state.busy || state.checking ? ' disabled' : ''}>我做完了，記錄這一步</button><p class="hint">${(step.checks || []).length ? '按下後會先自動檢查練習檔，必要項目通過才記錄。' : '看到上方「做完應該看到」的結果再按。'}可以不照順序記錄。</p></div>`;
    const help = step.help ? `<details class="more"><summary>找不到位置或畫面不同？</summary><p>${escapeHtml(step.help)}</p><p class="muted">仍不確定時，按頁首「複製接續訊息」貼到 AI 助教，並附上目前畫面。</p></details>` : '';
    return `<article class="step${figures.length ? '' : ' no-figure'}" id="step-${escapeHtml(step.id)}" aria-labelledby="t-${escapeHtml(step.id)}"><div class="step-text"><div class="step-head"><span class="dot${done ? ' is-done' : ''}">${done ? '✓' : index + 1}</span><h2 id="t-${escapeHtml(step.id)}">${escapeHtml(step.title)}</h2><span class="step-id">${escapeHtml(step.id)}</span></div><p class="focus">${escapeHtml(step.action)}</p>${ops}<p class="label">做完應該看到</p><ul class="checks">${success.map((s) => `<li>${escapeHtml(s)}</li>`).join('')}</ul>${pitfalls}${renderQuiz(step)}${done ? '' : renderCheck(step.id, step.checks, { lessonId: lesson.id, stepId: step.id })}${confirm}${help}</div>${figures.length ? `<div class="step-figs">${figures.map((f, i) => renderFigure(f, `${figureBase}:${i}`)).join('')}</div>` : ''}</article>`;
  }
  function renderSection() {
    const { lesson, section } = state.route;
    const steps = sectionSteps(lesson, section);
    const done = doneSet(lesson.id);
    const index = lesson.sections.indexOf(section);
    const all = lessons().flatMap((l) => l.sections.map((s) => ({ l, s })));
    const position = all.findIndex((item) => item.s === section);
    const prev = all[position - 1];
    const next = all[position + 1];
    const link = (item, cls, label) => item ? `<a class="${cls}" href="#/${item.l.id}/${item.s.id}"><span>${label}</span>${sectionNumber(item.l, item.s)} ${escapeHtml(item.s.title)}</a>` : '';
    const progress = progressOf(lesson.id);
    const head = `<header class="page-head"><p class="eyebrow">第 ${lesson.id} 課 · ${escapeHtml(lesson.stage || '')} · 第 ${index + 1} 節／共 ${lesson.sections.length} 節</p><h1><span class="h-num">${sectionNumber(lesson, section)}</span>${escapeHtml(section.title)}</h1><p class="point">${escapeHtml(section.point)}</p><div class="tools"><button type="button" class="btn" data-action="open-project" data-lesson="${lesson.id}">開啟練習專案</button><button type="button" class="btn" data-action="open-folder" data-lesson="${lesson.id}"${progress.workspace ? '' : ' disabled title="先按「開啟練習專案」建立副本"'}>開啟練習資料夾</button><button type="button" class="btn" data-action="copy-resume">複製接續訊息</button><span class="meter">本節 ${steps.filter((s) => done.has(s.id)).length} / ${steps.length} 小步已確認 · 本課 ${done.size} / ${lesson.steps.length}</span></div><ol class="chips">${steps.map((s, i) => `<li><a href="#/${lesson.id}/${section.id}/${s.id}" class="${done.has(s.id) ? 'is-done' : ''}"><span class="dot${done.has(s.id) ? ' is-done' : ''}">${done.has(s.id) ? '✓' : i + 1}</span>${escapeHtml(s.title)}</a></li>`).join('')}</ol></header>`;
    const last = index === lesson.sections.length - 1;
    return `${head}${steps.map((s, i) => renderStep(lesson, s, i, `${lesson.id}:${s.id}`)).join('')}${last ? renderWrapUp(lesson) : ''}<nav class="pager" aria-label="換節">${link(prev, 'prev', '上一節')}${link(next, 'next', '下一節')}</nav>${renderLessonTools(lesson)}`;
  }
  function renderWrapUp(lesson) {
    const progress = progressOf(lesson.id);
    const criteria = (lesson.completionCriteria || []).map((c) => `<li>${escapeHtml(asText(c))}</li>`).join('');
    const challenge = lesson.challenge;
    let challengeHtml = '';
    if (challenge) {
      const done = progress.challenge;
      const confirm = done
        ? `<div class="confirm-row"><div class="badges"><span class="state ok">✓ 練習題已確認 ${escapeHtml(dateLabel(done.confirmedAt))}</span>${(progress.evidence || []).some((e) => e.challenge && e.kind === 'tool_verified') ? '<span class="state ok">工具驗證</span>' : ''}${(progress.evidence || []).some((e) => e.challenge && e.kind === 'tool_unverified') ? '<span class="state warn">無法自動驗證</span>' : ''}</div></div>`
        : `<div class="confirm-row"><button type="button" class="btn btn-primary" data-action="confirm-challenge" data-lesson="${lesson.id}"${state.busy || state.checking ? ' disabled' : ''}>我做完練習題了</button><p class="hint">練習題和主線分開記錄，不計入 ${lesson.steps.length} 小步。</p></div>`;
      challengeHtml = `<section class="panel challenge" aria-labelledby="ch-${lesson.id}"><p class="eyebrow">本課練習題 · 選做</p><h2 id="ch-${lesson.id}">${escapeHtml(challenge.title)}</h2><p>${escapeHtml(challenge.goal)}</p><div class="panel-grid"><div><h3>要做的事</h3><ol class="plain">${challenge.tasks.map((t) => `<li>${escapeHtml(t)}</li>`).join('')}</ol></div><div><h3>做完應該看到</h3><ul class="checks">${challenge.success.map((t) => `<li>${escapeHtml(t)}</li>`).join('')}</ul>${challenge.hint ? `<p class="muted">${escapeHtml(challenge.hint)}</p>` : ''}</div></div>${renderCheck(`${lesson.id}-challenge`, challenge.checks, { lessonId: lesson.id, challenge: true })}${confirm}</section>`;
    }
    const physical = (lesson.physicalResults || []).length ? `<section class="panel"><h2>實體結果</h2><p class="muted">製板、焊接與上電只有你實際做了並回報才記錄；輸出 ZIP 不代表已製板。</p><ul class="physical">${lesson.physicalResults.map((item) => {
      const value = progress.physical?.[item.id];
      return `<li><div class="row"><strong>${escapeHtml(item.label)}</strong>${value ? `<span class="state ok">已回報 ${escapeHtml(dateLabel(value.at))}</span>` : '<span class="state info">未記錄</span>'}</div>${value ? `<pre class="saved">${escapeHtml(value.note)}</pre>` : `<details class="more"><summary>我已實際完成，記錄結果</summary><textarea rows="2" data-physical="${escapeHtml(item.id)}" placeholder="例如：板廠、日期、量測結果或照片位置"></textarea><div class="row"><button type="button" class="btn btn-small" data-action="record-physical" data-lesson="${lesson.id}" data-item="${escapeHtml(item.id)}">記錄</button></div></details>`}</li>`;
    }).join('')}</ul></section>` : '';
    return `${criteria ? `<section class="panel"><h2>本課完成的標準</h2><ul class="checks">${criteria}</ul></section>` : ''}${challengeHtml}${physical}`;
  }
  function mediaLink(link) {
    const url = String(link.url || '');
    return /^https:\/\/(www\.)?youtube\.com\//.test(url) ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(link.label || url)}</a>` : '';
  }
  function renderLessonTools(lesson) {
    const progress = progressOf(lesson.id);
    const media = (lesson.media || []).map(mediaLink).filter(Boolean);
    const files = (lesson.files || []).map((file) => {
      const path = String(file.path || '');
      const name = escapeHtml(file.label || path.split('/').pop());
      return /\.kicad_(pro|pcb|sch)$/i.test(path) ? `<li>${name}<span class="muted">（請用「開啟練習專案」）</span></li>` : `<li><a href="${fileUrl(path, /\.md$/i.test(path))}" target="_blank" rel="noopener"${/\.zip$/i.test(path) ? ' download' : ''}>${name}</a></li>`;
    }).join('');
    const draft = state.drafts.get(lesson.id) || '';
    return `<details class="panel" id="lesson-tools"${state.confirmReset === lesson.id ? ' open' : ''}><summary><strong>本課資源、筆記與重新學習</strong></summary><div class="panel-grid" style="margin-top:12px"><div>${media.length ? `<h3>原作者教學影片</h3><ul class="plain">${media.map((m) => `<li>${m}</li>`).join('')}</ul><p class="muted">英文影片，以 KiCad 7 示範；按鈕以你目前的 KiCad 畫面為準。</p>` : ''}${files ? `<h3>教材檔案</h3><ul class="plain">${files}</ul>` : ''}${lesson.context ? `<h3>本課教材說明</h3><p class="muted">${escapeHtml(asText(lesson.context))}</p>` : ''}</div><div><h3>卡關筆記</h3><form class="note-form" data-note-form data-lesson="${lesson.id}"><textarea rows="3" data-note-input data-lesson="${lesson.id}" placeholder="例如：已找到 Edge.Cuts，但不知道如何把圓形改到這一層。">${escapeHtml(draft)}</textarea><div class="row"><button type="submit" class="btn btn-small"${draft.trim() ? '' : ' disabled'} data-save-note>儲存筆記</button><span class="muted">保存在專案內，與 AI 助教共用。</span></div></form>${progress.notes ? `<details class="more"><summary>目前筆記摘要</summary><pre class="saved">${escapeHtml(progress.notes)}</pre></details>` : ''}${progress.workspace ? `<h3>練習副本</h3><p class="muted"><code>${escapeHtml(progress.workspace)}</code></p>` : ''}${renderReset(lesson.id)}</div></div></details>`;
  }
  function canReset(scope) {
    if (scope === 'all') return lessons().some((lesson) => canReset(lesson.id));
    const p = progressOf(scope);
    return p.status !== 'not_started' || Boolean(p.workspace) || (p.completedSteps || []).length > 0;
  }
  function renderReset(scope) {
    if (!canReset(scope)) return '';
    const all = scope === 'all';
    if (state.confirmReset !== scope) return `<div class="reset-box"><button type="button" class="btn btn-small btn-quiet" data-action="reset-ask" data-scope="${scope}">${all ? '全部課程重新開始' : '重新學習本課'}</button></div>`;
    return `<div class="reset-box is-confirming" role="group"><p><strong>${all ? '要讓全部 15 課都回到第 1 步嗎？' : '要讓本課回到第 1 步嗎？'}</strong></p><p>進度、筆記與練習副本會封存到 learning/archive/，不會刪除。請先在 KiCad 用「檔案 → 關閉專案」關掉${all ? '練習專案' : '本課專案'}。</p><div class="row"><button type="button" class="btn btn-primary btn-small" data-action="reset-confirm" data-scope="${scope}">確定重新開始</button><button type="button" class="btn btn-small" data-action="reset-cancel">取消</button></div></div>`;
  }

  // ---------- Chapter 00, overview, journal ----------
  function stepLink(id) {
    const lesson = lessonById(id.slice(0, 2));
    const section = lesson?.sections.find((s) => s.steps.includes(id));
    const step = lesson && stepById(lesson, id);
    return section ? `<a href="#/${lesson.id}/${section.id}/${id}">${escapeHtml(id)} ${escapeHtml(step.title)}</a>` : '';
  }
  function renderReference() {
    const ref = reference();
    if (!ref) return '<div class="empty"><h1>沒有速查資料</h1></div>';
    const v = ref.verification || {};
    return `<header class="page-head"><p class="eyebrow">第 00 章 · 速查</p><h1><span class="h-num">0</span>${escapeHtml(ref.title)}</h1><p class="point">${escapeHtml(ref.point)}</p><p class="muted">${escapeHtml(v.note || '')} 核對日期 ${escapeHtml(v.checkedOn || '')}：${(v.sources || []).map((s) => `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(s.label)}</a>`).join('、')}。</p></header>${ref.sections.map((section) => `<div class="sub-head" id="${escapeHtml(section.id)}"><h2>${escapeHtml(section.title)}</h2><p>${escapeHtml(section.point)}</p></div><div class="ref-grid">${section.cards.map((card) => `<article class="ref-card"><div class="ref-keys">${card.keys.map((k) => `<kbd>${escapeHtml(k)}</kbd>`).join('')}</div><h3>${escapeHtml(card.title)}</h3><div class="ref-keys">${card.editors.map((e) => `<span class="tag">${escapeHtml(e)}</span>`).join('')}</div><p>${escapeHtml(card.use)}</p><p class="watch">注意：${escapeHtml(card.watch)}</p>${card.steps.length ? `<p class="muted">${card.related ? '相關練習（教材未直接使用此鍵）' : '本課程實際用到'}：</p><div class="ref-links">${card.steps.map(stepLink).join('')}</div>` : ''}</article>`).join('')}</div>`).join('')}`;
  }
  function renderOverview() {
    const doneLessons = lessons().filter((l) => progressOf(l.id).status === 'completed').length;
    return `<header class="page-head"><p class="eyebrow">15 課 · ${lessons().reduce((n, l) => n + l.sections.length, 0)} 節 · ${totalSteps()} 小步</p><h1>課程總覽</h1><p class="point">${escapeHtml(state.data.catalog.versionNote || '')}</p><p class="muted">已確認 ${totalDone()} / ${totalSteps()} 小步，完成 ${doneLessons} 課。證據分成：${(state.data.catalog.evidenceLevels || []).map((e) => `<strong>${escapeHtml(e.label)}</strong>（${escapeHtml(e.meaning)}）`).join('；')}</p></header><div class="cards">${lessons().map((lesson) => {
      const p = progressOf(lesson.id);
      const done = doneSet(lesson.id).size;
      return `<article class="card"><p class="eyebrow">第 ${lesson.id} 課 · ${escapeHtml(lesson.stage || '')}</p><h3>${escapeHtml(lesson.title)}</h3><p>${escapeHtml(asText(lesson.goal))}</p><div class="bar" aria-hidden="true"><span style="width:${Math.round(done / lesson.steps.length * 100)}%"></span></div><p class="muted">${done} / ${lesson.steps.length} 小步 · 練習題${p.challenge ? '已完成' : '未完成'}</p><ol class="plain">${lesson.sections.map((s) => `<li><a href="#/${lesson.id}/${s.id}">${sectionNumber(lesson, s)} ${escapeHtml(s.title)}</a></li>`).join('')}</ol></article>`;
    }).join('')}</div>${canReset('all') ? `<section class="panel" style="margin-top:18px"><h2>想從頭再學一次？</h2><p class="muted">每課都能在該課的「本課資源、筆記與重新學習」單獨重來，也可以一次讓全部課程回到第 1 步。舊副本與進度會先封存。</p>${renderReset('all')}</section>` : ''}`;
  }
  function renderJournal() {
    const journal = state.data.journal || [];
    const guides = state.data.guides || [];
    const examples = state.data.examples || [];
    const noted = lessons().filter((l) => asText(progressOf(l.id).notes).trim());
    const card = (title, summary, href, label) => `<article class="card"><h3>${escapeHtml(title)}</h3><p>${escapeHtml(asText(summary))}</p><a class="btn btn-small" href="${href}" target="_blank" rel="noopener">${label}</a></article>`;
    return `<header class="page-head"><p class="eyebrow">把學會的內容留下來</p><h1>學習紀錄與操作圖解</h1></header><div class="sub-head"><h2>課程學習紀錄</h2></div><div class="cards">${journal.length ? journal.map((e) => card(e.title, e.summary, fileUrl(e.path, true), '閱讀完整紀錄')).join('') : '<p class="muted">完成操作後，學習紀錄會集中顯示在這裡。</p>'}</div>${examples.length ? `<div class="sub-head"><h2>公開完成範例</h2><p>示範紀錄，不會計入你的進度。</p></div><div class="cards">${examples.map((e) => card(e.title, e.summary, fileUrl(e.path, true), '閱讀示範紀錄')).join('')}</div>` : ''}${guides.length ? `<div class="sub-head"><h2>對照畫面操作</h2></div><div class="cards">${guides.map((g) => `<article class="card"><a class="guide-img" href="${fileUrl(g.path)}" target="_blank" rel="noopener"><img src="${fileUrl(g.path)}" alt="${escapeHtml(g.title)}" loading="lazy"></a><h3>${escapeHtml(g.title)}</h3><p>${escapeHtml(asText(g.description))}</p></article>`).join('')}</div>` : ''}<div class="sub-head"><h2>各課目前筆記摘要</h2></div>${noted.length ? `<div class="cards">${noted.map((l) => `<article class="card"><h3>第 ${l.id} 課 · ${escapeHtml(l.title)}</h3><pre class="saved">${escapeHtml(progressOf(l.id).notes)}</pre></article>`).join('')}</div>` : '<p class="muted" style="max-width:1320px;margin:0 auto">尚未新增筆記。</p>'}`;
  }

  function renderAll() {
    if (!state.data || !state.route) return;
    renderNav();
    renderCrumb();
    const view = state.route.view;
    $('main').innerHTML = view === 'reference' ? renderReference() : view === 'overview' ? renderOverview() : view === 'journal' ? renderJournal() : renderSection();
    $('main').setAttribute('aria-busy', String(state.busy));
  }
  function rerender() {
    // Keep the reader's place when progress changes underneath the page.
    const y = scrollY;
    renderAll();
    scrollTo({ top: y });
  }

  // ---------- Data and actions ----------
  async function bootstrap({ quiet = false } = {}) {
    if (state.loading || state.busy) return;
    state.loading = true;
    try {
      const data = await request('/api/bootstrap');
      if (!data.catalog?.lessons?.every((l) => Array.isArray(l.sections))) throw new Error('此服務仍是舊版課程資料，請重新執行 Start-Learning.cmd。');
      const changed = state.data?.progress?.revision !== data.progress.revision;
      const first = !state.data;
      state.data = data;
      if (first) go();
      else if (changed) { rerender(); if (!quiet) notice('已載入最新的共用進度。'); }
    } catch (error) {
      if (!state.data) $('main').innerHTML = `<div class="empty"><h1>尚未連上本機教學服務</h1><p>${escapeHtml(error.message)}</p><button type="button" class="btn btn-primary" data-action="retry">重新讀取</button></div>`;
      $('sync').textContent = '共用進度暫時無法讀取';
    } finally {
      state.loading = false;
    }
  }
  async function handleError(error) {
    if (error.data?.check) {
      state.checks.set(error.data.check.stepId, error.data.check);
      notice(error.message, 'warning', true);
    } else if (error.status === 409) {
      state.busy = false;
      await bootstrap({ quiet: true });
      notice('進度已由其他視窗或 AI 助教更新，已載入最新資料。', 'warning', true);
    } else notice(error.message || '操作沒有完成，請稍後再試。', 'error', true);
  }
  async function mutate(payload, success) {
    if (state.busy) return;
    state.busy = true;
    rerender();
    try {
      const result = await request('/api/progress', { method: 'POST', body: JSON.stringify({ expectedRevision: state.data.progress.revision, ...payload }) });
      state.data.progress = result.progress;
      notice(success);
      return true;
    } catch (error) {
      await handleError(error);
      return false;
    } finally {
      state.busy = false;
      rerender();
    }
  }
  async function runCheck(key, payload) {
    if (state.checking) return;
    state.checking = key;
    rerender();
    try {
      const result = await request('/api/check', { method: 'POST', body: JSON.stringify(payload) });
      if (result.check) state.checks.set(key, result.check);
      notice(result.check?.passed ? ((result.check.unverified || []).length ? '沒有發現問題，但部分必要檢查無法執行。' : '自動檢查通過，可以記錄完成。') : '自動檢查還沒通過，請看檢查結果的說明。', result.check?.passed ? 'success' : 'warning');
    } catch (error) {
      notice(error.message || '自動檢查沒有完成。', 'error', true);
    } finally {
      state.checking = null;
      rerender();
    }
  }
  async function openPractice(lessonId, target) {
    if (state.busy) return;
    state.busy = true;
    rerender();
    try {
      const workspace = await request('/api/workspace', { method: 'POST', body: JSON.stringify({ expectedRevision: state.data.progress.revision, lessonId }) });
      if (workspace.progress) state.data.progress = workspace.progress;
      await request('/api/open', { method: 'POST', body: JSON.stringify({ lessonId, target }) });
      notice(`${workspace.created ? '已建立本課練習副本。' : ''}${target === 'folder' ? '已開啟練習資料夾。' : '已送出開啟練習專案的要求，請查看 KiCad 視窗。'}`);
    } catch (error) {
      await handleError(error);
    } finally {
      state.busy = false;
      rerender();
    }
  }
  async function resetProgress(scope) {
    if (state.busy) return;
    state.busy = true;
    rerender();
    try {
      const result = await request('/api/reset', { method: 'POST', body: JSON.stringify({ expectedRevision: state.data.progress.revision, lessonId: scope, confirm: true }) });
      state.data.progress = result.progress;
      state.confirmReset = null;
      (scope === 'all' ? lessons() : [lessonById(scope)]).forEach((lesson) => {
        state.drafts.delete(lesson.id);
        lesson.steps.forEach((step) => { state.checks.delete(step.id); state.quiz.delete(step.id); });
        state.checks.delete(`${lesson.id}-challenge`);
      });
      notice(scope === 'all' ? '全部課程已回到第 1 步，舊的練習副本已封存。' : '本課已回到第 1 步，舊的練習副本已封存。');
    } catch (error) {
      await handleError(error);
    } finally {
      state.busy = false;
      rerender();
    }
  }
  function resumeMessage() {
    const route = state.route;
    const lesson = route.lesson || lessonById(state.data.progress.activeLesson);
    const done = doneSet(lesson.id);
    const section = route.section || firstOpenSection(lesson);
    const open = sectionSteps(lesson, section).find((s) => !done.has(s.id));
    return [
      '請接續這個專案的 KiCad 引導式學習。',
      `專案：${state.data.projectRoot || '目前專案根目錄'}`,
      '請先讀取根目錄 AGENTS.md、learning/catalog.json、learning/progress.json，以及相關學習紀錄，以檔案中的最新進度為準。',
      `我目前在第 ${lesson.id} 課 ${lesson.title}，第 ${sectionNumber(lesson, section)} 節「${section.title}」；本課已確認 ${done.size} / ${lesson.steps.length} 小步。`,
      open ? `本節還沒確認的小步：${open.id} ${open.title}。` : '本節小步都已確認。',
      '請一次帶我做一小步：說明在哪個視窗、按什麼、成功時會看到什麼，再等我回報。只有我確認實際完成，才更新共用進度。',
      (state.drafts.get(lesson.id) || '').trim() ? `我目前補充：${state.drafts.get(lesson.id).trim()}` : '',
    ].filter(Boolean).join('\n\n');
  }
  async function copyResume(trigger) {
    const message = resumeMessage();
    try {
      if (!navigator.clipboard?.writeText) throw new Error('no clipboard');
      await navigator.clipboard.writeText(message);
      notice('接續訊息已複製，貼到 AI 助教對話即可依本節進度繼續。');
    } catch {
      state.clipboardTrigger = trigger;
      $('clipboard-text').value = message;
      $('clipboard-fallback').hidden = false;
      $('clipboard-text').focus();
      $('clipboard-text').select();
    }
  }

  // ---------- Lightbox keeps the numbered marks (issue #3) ----------
  function openLightbox(key, trigger) {
    const [lessonId, stepId, index] = key.split(':');
    const figure = stepById(lessonById(lessonId), stepId)?.figures?.[Number(index)];
    if (!figure) return;
    state.lightboxTrigger = trigger;
    $('lightbox-caption').textContent = figure.caption;
    $('lightbox-original').href = fileUrl(figure.image);
    const clamp = (v) => Math.min(98, Math.max(2, Number(v) || 0));
    $('lightbox-body').innerHTML = `<div class="shot"><img src="${fileUrl(figure.image)}" alt="${escapeHtml(figure.caption)}">${(figure.marks || []).map((m) => `<span class="shot-mark" style="left:${clamp(m.x)}%;top:${clamp(m.y)}%" title="${escapeHtml(m.label)}">${escapeHtml(m.n)}</span>`).join('')}</div>${(figure.marks || []).length ? `<ol class="legend" style="color:#fff;margin-top:8px">${figure.marks.map((m) => `<li><span class="mark">${escapeHtml(m.n)}</span><span>${escapeHtml(m.label)}</span></li>`).join('')}</ol>` : ''}`;
    $('lightbox').hidden = false;
    $('lightbox').querySelector('button').focus();
  }
  function closeLightbox() {
    $('lightbox').hidden = true;
    $('lightbox-body').innerHTML = '';
    state.lightboxTrigger?.focus();
  }
  function closeClipboard() {
    $('clipboard-fallback').hidden = true;
    state.clipboardTrigger?.focus();
  }

  // ---------- Events ----------
  $('nav-toggle').addEventListener('click', () => {
    if (narrow.matches) {
      const open = document.body.classList.toggle('nav-open');
      $('nav-backdrop').hidden = !open;
    } else {
      write('nav-mini', document.body.classList.toggle('nav-mini') ? '1' : '0');
      renderNav();
    }
    syncToggle();
  });
  $('nav-backdrop').addEventListener('click', closeDrawer);
  narrow.addEventListener('change', () => { closeDrawer(); renderNav(); });
  document.addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (!button || button.disabled) {
      if (event.target.closest('#nav a')) closeDrawer();
      return;
    }
    if (button.dataset.toggle) {
      const id = button.dataset.toggle;
      if (isMini()) { location.hash = `#/${id}/${firstOpenSection(lessonById(id)).id}`; return; }
      expanded.has(id) ? expanded.delete(id) : expanded.add(id);
      write('expanded', JSON.stringify([...expanded]));
      renderNav();
      document.querySelector(`[data-toggle="${id}"]`)?.focus();
      return;
    }
    if (button.dataset.zoom) { openLightbox(button.dataset.zoom, button); return; }
    const { action, lesson, step } = button.dataset;
    switch (action) {
      case 'confirm': void mutate({ action: 'confirm_step', lessonId: lesson, stepId: step }, '這一步已記錄。'); break;
      case 'confirm-challenge': void mutate({ action: 'confirm_challenge', lessonId: lesson }, '練習題已記錄。'); break;
      case 'record-physical': {
        const note = document.querySelector(`[data-physical="${button.dataset.item}"]`)?.value.trim();
        if (!note) { notice('請先寫下實際結果，例如板廠、日期或量測。', 'warning'); break; }
        void mutate({ action: 'record_physical', lessonId: lesson, item: button.dataset.item, note }, '實體結果已記錄。');
        break;
      }
      case 'run-check': void runCheck(button.dataset.key, JSON.parse(button.dataset.payload)); break;
      case 'quiz': state.quiz.set(step, Number(button.dataset.choice)); rerender(); break;
      case 'open-project': void openPractice(lesson, 'project'); break;
      case 'open-folder': void openPractice(lesson, 'folder'); break;
      case 'copy-resume': void copyResume(button); break;
      case 'reset-ask': state.confirmReset = button.dataset.scope; rerender(); break;
      case 'reset-cancel': state.confirmReset = null; rerender(); break;
      case 'reset-confirm': void resetProgress(button.dataset.scope); break;
      case 'close-lightbox': closeLightbox(); break;
      case 'close-clipboard': closeClipboard(); break;
      case 'retry': void bootstrap(); break;
      default: break;
    }
  });
  document.addEventListener('input', (event) => {
    if (!event.target.matches('[data-note-input]')) return;
    state.drafts.set(event.target.dataset.lesson, event.target.value);
    const save = event.target.form?.querySelector('[data-save-note]');
    if (save) save.disabled = state.busy || !event.target.value.trim();
  });
  document.addEventListener('submit', (event) => {
    if (!event.target.matches('[data-note-form]')) return;
    event.preventDefault();
    const lessonId = event.target.dataset.lesson;
    const note = (state.drafts.get(lessonId) || '').trim();
    if (note) void mutate({ action: 'add_note', lessonId, note }, '本課筆記摘要已更新。').then((ok) => { if (ok) { state.drafts.delete(lessonId); rerender(); } });
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    if (!$('lightbox').hidden) closeLightbox();
    else if (!$('clipboard-fallback').hidden) closeClipboard();
    else if (document.body.classList.contains('nav-open')) closeDrawer();
  });
  $('lightbox').addEventListener('click', (event) => { if (event.target === $('lightbox')) closeLightbox(); });
  addEventListener('hashchange', go);
  addEventListener('beforeunload', (event) => {
    if ([...state.drafts.values()].some((d) => d.trim())) { event.preventDefault(); event.returnValue = ''; }
  });
  setInterval(() => {
    if (document.hidden || state.busy || state.checking || document.activeElement?.matches('textarea')) return;
    void bootstrap({ quiet: true });
  }, 15000);
  syncToggle();
  void bootstrap({ quiet: true });
})();
