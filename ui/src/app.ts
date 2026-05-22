/* CDN globals */
declare const Chart: any;
declare const mermaid: any;
declare const hljs: any;

// ── Types ─────────────────────────────────────────────────────────────────────
interface Program {
  name: string;
  source_file: string;
  para_count: number;
  item_count: number;
  rule_count: number;
  risk_count: number;
}

interface Stats {
  programs: number;
  paragraphs: number;
  data_items: number;
  conditions_88: number;
  statements: number;
  business_rules: number;
  call_edges: number;
  cfg_edges: number;
  file_io_ops: number;
  risks: number;
  coverage_pct: number;
  ok_files: number;
  total_files: number;
  cobol_files: number;
  jcl_files: number;
  bms_files: number;
  csd_files: number;
  copybook_files: number;
  asm_files: number;
  db2_statements: number;
  ims_calls: number;
  mq_calls: number;
  cics_verbs: number;
  copybook_refs: number;
}

interface ASTNode {
  uuid: string;
  kind: string;
  name?: string;
  start_line?: number;
  end_line?: number;
  children?: ASTNode[];
}

interface CoverageRow {
  source_file: string;
  status: string;
  error_class?: string;
  parse_time_ms?: number;
}

interface RiskRow {
  program_name?: string;
  kind: string;
  severity: string;
  note?: string;
  line?: number;
}

// ── State ─────────────────────────────────────────────────────────────────────
let currentPage = 'dashboard';
let programs: Program[] = [];
let currentProgram: string | null = null;
let diagSource = '';
let _exportContent = '';
let specText = '';
let _personaResults: Record<string, string> = {};
let _activePersonaTab = '';
let _transformSessionId = '';
let _transformSteps: any[] = [];
let _currentTransformStep = 0;
let coverageAllRows: CoverageRow[] = [];
let riskAllRows: RiskRow[] = [];
let currentVizTab = 'ast';
let pipelineRunning = false;

let covChart: any = null;
let layerChart: any = null;
let covDonut: any = null;
let covBar2: any = null;
let riskDonut: any = null;
let riskBar2: any = null;
let complexityChart: any = null;

// ── Page Isolation ────────────────────────────────────────────────────────────
let _ctrl = new AbortController();

function abortCurrentPage(): void {
  _ctrl.abort();
  _ctrl = new AbortController();
}

function sig(): AbortSignal {
  return _ctrl.signal;
}

// ── API Client ────────────────────────────────────────────────────────────────
async function apiFetch<T = unknown>(path: string, opts: RequestInit = {}): Promise<T> {
  const r = await fetch(path, { ...opts, signal: sig() });
  if (!r.ok) {
    const t = await r.text().catch(() => r.statusText);
    throw new Error(t);
  }
  return r.json() as Promise<T>;
}

function isAbort(e: unknown): boolean {
  return e instanceof Error && e.name === 'AbortError';
}

function $<T extends Element>(id: string): T | null {
  return document.getElementById(id) as T | null;
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function showToast(msg: string, type: 'ok' | 'error' = 'ok'): void {
  const t = $('toast')!;
  t.textContent = msg;
  (t as HTMLElement).style.background = type === 'error' ? '#ef4444' : 'var(--ust-teal)';
  (t as HTMLElement).style.display = 'block';
  setTimeout(() => { (t as HTMLElement).style.display = 'none'; }, 2200);
}

// ── Navigation ────────────────────────────────────────────────────────────────
function navigate(page: string): void {
  abortCurrentPage();
  currentPage = page;

  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const navEl = document.querySelector(`.nav-item[onclick="navigate('${page}')"]`);
  if (navEl) navEl.classList.add('active');

  document.querySelectorAll<HTMLElement>('section[id^="page-"]').forEach(s => { s.style.display = 'none'; });
  const sec = document.getElementById(`page-${page}`) as HTMLElement | null;
  if (sec) { sec.style.display = ''; sec.classList.add('fade-in'); }

  const titles: Record<string, string> = {
    dashboard: 'Dashboard', pipeline: 'Run Pipeline', programs: 'Program Explorer',
    visualizations: 'Visualizations', diagrams: 'Diagrams', spec: 'Spec Generator',
    transform: 'Forward Engineering — Transform', coverage: 'Coverage Report',
    risks: 'Risk Register', settings: 'Settings', layers: 'Layer Explorer',
    platform: 'Target Platform Recommender',
  };
  const titleEl = $('page-title') as HTMLElement | null;
  if (titleEl) titleEl.textContent = titles[page] ?? page;

  if (page === 'dashboard')      void loadDashboard();
  if (page === 'programs')       { void loadPrograms(); void loadProgramDropdowns(); }
  if (page === 'visualizations') void loadVizProgramDropdown();
  if (page === 'diagrams')       void loadDiagram('call_graph', document.querySelector<HTMLElement>('.diag-btn'));
  if (page === 'spec')           { void loadProgramDropdowns(); void loadCurrentModel(); }
  if (page === 'transform')      void loadTransformPage();
  if (page === 'platform')       void loadPlatformPage();
  if (page === 'coverage')       void loadCoverage();
  if (page === 'risks')          void loadRisks();
  if (page === 'settings')       void loadSettings();
  if (page === 'layers')         void loadLayersPage();
}

// ── Health ────────────────────────────────────────────────────────────────────
async function checkHealth(): Promise<void> {
  try {
    const h = await apiFetch<{ db_ready: boolean; pipeline_running: boolean }>('/health');
    const el = $<HTMLElement>('api-status');
    const badge = $<HTMLElement>('db-badge');
    const dot = h.db_ready ? '#4ade80' : '#fbbf24';
    const label = h.db_ready ? 'API + DB ready' : 'API ready — no DB';
    if (el) el.innerHTML = `<span style="width:7px;height:7px;border-radius:50%;background:${dot};display:inline-block;flex-shrink:0;"></span> <span>${label}</span>`;
    if (badge) { badge.textContent = h.db_ready ? 'DB: ready' : 'DB: run pipeline first'; badge.style.color = dot; }
  } catch {
    const el = $<HTMLElement>('api-status');
    if (el) el.innerHTML = `<span style="width:7px;height:7px;border-radius:50%;background:#f87171;display:inline-block;flex-shrink:0;"></span> <span>API unreachable</span>`;
  }
}

// ── Dashboard ─────────────────────────────────────────────────────────────────
async function loadDashboard(): Promise<void> {
  try {
    const s = await apiFetch<Stats>('/stats');
    // Core artifact counts
    (['programs','paragraphs','data_items','conditions_88','statements','business_rules',
      'call_edges','cfg_edges','risks','cics_verbs'] as const).forEach(f => {
      const el = $<HTMLElement>(`s-${f}`);
      if (el) el.textContent = ((s as any)[f] ?? 0).toLocaleString();
    });
    // Coverage
    const pctEl = $<HTMLElement>('s-coverage_pct');
    if (pctEl) pctEl.textContent = (s.coverage_pct ?? 0) + '%';
    const okEl = $<HTMLElement>('s-ok_files');
    if (okEl) okEl.textContent = (s.ok_files ?? 0).toString();
    const totEl = $<HTMLElement>('s-total_files');
    if (totEl) totEl.textContent = (s.total_files ?? 0).toString();
    // File type breakdown
    (['cobol_files','jcl_files','bms_files','csd_files','copybook_files',
      'asm_files','db2_statements','ims_calls','mq_calls','copybook_refs'] as const).forEach(f => {
      const el = $<HTMLElement>(`s-${f}`);
      if (el) el.textContent = ((s as any)[f] ?? 0).toLocaleString();
    });

    const cvCtx = ($<HTMLCanvasElement>('coverage-chart'))?.getContext('2d');
    if (cvCtx) {
      if (covChart) covChart.destroy();
      covChart = new Chart(cvCtx, {
        type: 'doughnut',
        data: { labels: ['Parsed OK','Failed'], datasets: [{ data: [s.ok_files, s.total_files - s.ok_files], backgroundColor: ['#4ade80','#f87171'], borderWidth: 0 }] },
        options: { plugins: { legend: { labels: { color: '#7e8c9a', font: { size: 12 } } } }, cutout: '70%' },
      });
    }

    const lCtx = ($<HTMLCanvasElement>('layer-chart'))?.getContext('2d');
    if (lCtx) {
      if (layerChart) layerChart.destroy();
      layerChart = new Chart(lCtx, {
        type: 'bar',
        data: {
          labels: ['Programs','Paragraphs','Data Items','Stmts','Bus. Rules','Call Edges','Risks'],
          datasets: [{ data: [s.programs, s.paragraphs, s.data_items, s.statements, s.business_rules, s.call_edges, s.risks],
            backgroundColor: ['#006e74','#0097ab','#009ddc','#00afd9','#4ade80','#fbbf24','#f87171'], borderRadius: 4, borderWidth: 0 }],
        },
        options: { plugins: { legend: { display: false } }, scales: {
          x: { ticks: { color: '#7e8c9a', font: { size: 11 } }, grid: { color: '#2b333f' } },
          y: { ticks: { color: '#7e8c9a' }, grid: { color: '#2b333f' } },
        }},
      });
    }
    renderBirdsEyeView(s);
    renderKnowledgeGraph(s);
  } catch(e) {
    if (!isAbort(e)) { console.warn('Dashboard unavailable:', (e as Error).message); renderBirdsEyeView({} as Stats); }
  }
}

function renderBirdsEyeView(s: Partial<Stats>): void {
  const inv = $<HTMLElement>('birds-eye-inventory');
  if (!inv) return;
  const rows: Array<{ label: string; count: number; color: string; ext: string }> = [
    { label: 'COBOL Programs',  count: (s as any).cobol_files    ?? 0, color: '#5ecdd1', ext: '.cbl' },
    { label: 'Copybooks',       count: (s as any).copybook_files  ?? 0, color: '#60c8fa', ext: '.cpy' },
    { label: 'JCL Jobs',        count: (s as any).jcl_files       ?? 0, color: '#fbbf24', ext: '.jcl' },
    { label: 'BMS Maps',        count: (s as any).bms_files       ?? 0, color: '#4ade80', ext: '.bms' },
    { label: 'CSD Definitions', count: (s as any).csd_files       ?? 0, color: '#a78bfa', ext: '.csd' },
    { label: 'Assembler',       count: (s as any).asm_files       ?? 0, color: '#f97316', ext: '.asm' },
    { label: 'DB2 Statements',  count: (s as any).db2_statements  ?? 0, color: '#f87171', ext: 'SQL'  },
    { label: 'CICS Verbs',      count: (s as any).cics_verbs      ?? 0, color: '#d876d6', ext: 'CICS' },
    { label: 'Copybook Refs',   count: (s as any).copybook_refs   ?? 0, color: '#55d4eb', ext: 'COPY' },
  ];
  const total = rows.filter(r => r.ext !== 'SQL' && r.ext !== 'CICS' && r.ext !== 'COPY')
                    .reduce((a, r) => a + r.count, 0);
  inv.innerHTML = rows.map(r => {
    const pct = total > 0 && !['SQL','CICS','COPY'].includes(r.ext)
      ? Math.round((r.count / total) * 100) : 0;
    return `<div style="display:flex;align-items:center;gap:10px;">
      <span style="min-width:130px;font-size:12px;color:var(--muted);">${r.label}</span>
      <div style="flex:1;background:var(--surface2);border-radius:3px;height:6px;overflow:hidden;">
        <div style="width:${pct}%;background:${r.color};height:100%;border-radius:3px;"></div>
      </div>
      <span style="min-width:28px;text-align:right;font-weight:700;font-size:13px;color:${r.color};">${r.count.toLocaleString()}</span>
      <span style="min-width:30px;font-size:11px;color:var(--muted);">${r.ext}</span>
    </div>`;
  }).join('');
}

function renderKnowledgeGraph(s: Partial<Stats>): void {
  const fields: Array<[string, keyof Stats]> = [
    ['kg-programs', 'programs'],
    ['kg-cfg',      'cfg_edges'],
    ['kg-calls',    'call_edges'],
    ['kg-rules',    'business_rules'],
    ['kg-risks',    'risks'],
  ];
  for (const [id, key] of fields) {
    const el = $<HTMLElement>(id);
    if (el) el.textContent = ((s as any)[key] ?? 0).toLocaleString();
  }
}


// ── Programs ──────────────────────────────────────────────────────────────────
async function loadPrograms(): Promise<void> {
  const q = ($<HTMLInputElement>('prog-search'))?.value ?? '';
  try {
    const data = await apiFetch<{ items: Program[]; total: number }>(`/programs?q=${encodeURIComponent(q)}&limit=200`);
    programs = data.items ?? [];
    const countEl = $<HTMLElement>('prog-count');
    if (countEl) countEl.textContent = `${data.total} programs`;
    renderProgramsTable(programs);
  } catch(e) {
    if (!isAbort(e)) {
      const tb = $<HTMLElement>('programs-body');
      if (tb) tb.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:40px;">Run the pipeline to populate programs.</td></tr>';
    }
  }
}

function renderProgramsTable(progs: Program[]): void {
  const tbody = $<HTMLElement>('programs-body');
  if (!tbody) return;
  if (!progs.length) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:40px;">No programs found.</td></tr>';
    return;
  }
  tbody.innerHTML = progs.map(p => `
    <tr style="cursor:pointer;" onclick="openProgram('${p.name}')">
      <td><span style="font-weight:700;color:#5ecdd1;">${p.name ?? '—'}</span></td>
      <td><span style="font-size:11px;color:var(--muted);">${(p.source_file ?? '').split('/').pop()}</span></td>
      <td><span class="badge badge-sky">${p.para_count ?? 0}</span></td>
      <td>${p.item_count ?? 0}</td>
      <td>${p.rule_count ?? 0}</td>
      <td>${(p.risk_count ?? 0) > 0 ? `<span class="badge badge-red">${p.risk_count}</span>` : '<span class="badge badge-gray">0</span>'}</td>
      <td><span style="font-size:12px;color:#5ecdd1;">View →</span></td>
    </tr>`).join('');
}

async function openProgram(name: string): Promise<void> {
  const tw = $<HTMLElement>('programs-table-wrap');
  const pd = $<HTMLElement>('prog-detail');
  const ps = $<HTMLElement>('prog-search');
  const pc = $<HTMLElement>('prog-count');
  const dn = $<HTMLElement>('detail-name');
  if (tw) tw.style.display = 'none';
  if (pd) pd.style.display = '';
  if (ps) ps.style.display = 'none';
  if (pc) pc.style.display = 'none';
  if (dn) dn.textContent = name;
  currentProgram = name;
  try {
    const d = await apiFetch<any>(`/programs/${encodeURIComponent(name)}/detail`);
    renderParas(d.paragraphs);
    renderDataItems(d.data_items);
    renderCallGraph(d.call_graph);
    renderBizRules(d.business_rules);
    renderFileIO(d.file_io);
    renderProgRisks(d.risks);
    loadProgSource(name);
  } catch(e) {
    if (!isAbort(e)) console.warn(e);
  }
}

function closeDetail(): void {
  const tw = $<HTMLElement>('programs-table-wrap');
  const pd = $<HTMLElement>('prog-detail');
  const ps = $<HTMLElement>('prog-search');
  const pc = $<HTMLElement>('prog-count');
  if (tw) tw.style.display = '';
  if (pd) pd.style.display = 'none';
  if (ps) ps.style.display = '';
  if (pc) pc.style.display = '';
  switchTab('paragraphs');
}

function switchTab(name: string): void {
  document.querySelectorAll<HTMLElement>('.tab-panel').forEach(p => { p.style.display = 'none'; });
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  const panel = $<HTMLElement>(`tab-${name}`);
  if (panel) panel.style.display = '';
  const order: Record<string, number> = { paragraphs: 0, dataitems: 1, callgraph: 2, bizrules: 3, fileio: 4, progrisk: 5, source: 6 };
  const tabEls = document.querySelectorAll('.tab');
  const idx = order[name];
  if (idx !== undefined && tabEls[idx]) tabEls[idx].classList.add('active');
}

function renderParas(rows: any[]): void {
  const el = $<HTMLElement>('para-tbody');
  if (!el) return;
  el.innerHTML = (rows ?? []).map(r =>
    `<tr><td style="font-weight:600;color:var(--ust-sky);">${r.name}</td><td>${r.start_line}</td><td>${r.end_line}</td></tr>`
  ).join('') || '<tr><td colspan="3" style="color:var(--muted);">None</td></tr>';
}

function renderDataItems(rows: any[]): void {
  const el = $<HTMLElement>('di-tbody');
  if (!el) return;
  el.innerHTML = (rows ?? []).map(r => `
    <tr>
      <td style="font-weight:500;">${r.name}</td>
      <td><span class="badge badge-gray">${r.level}</span></td>
      <td><code style="font-size:11px;color:var(--ust-sky);">${r.pic ?? ''}</code></td>
      <td>${r.usage ?? 'DISPLAY'}</td>
      <td><span class="badge ${r.canonical_kind === 'decimal' ? 'badge-orange' : r.canonical_kind === 'alpha' ? 'badge-sky' : 'badge-gray'}">${r.canonical_kind ?? ''}</span></td>
      <td>${r.precision ?? ''}</td><td>${r.scale ?? ''}</td>
    </tr>`).join('') || '<tr><td colspan="7" style="color:var(--muted);">None</td></tr>';
}

function renderCallGraph(rows: any[]): void {
  const el = $<HTMLElement>('cg-tbody');
  if (!el) return;
  el.innerHTML = (rows ?? []).map(r => `
    <tr><td style="font-weight:500;">${r.callee_name}</td>
    <td><span class="badge badge-sky">${r.call_type}</span></td>
    <td>${r.is_resolved ? '<span class="badge badge-green">✓ resolved</span>' : '<span class="badge badge-amber">unresolved</span>'}</td>
    </tr>`).join('') || '<tr><td colspan="3" style="color:var(--muted);">No external calls</td></tr>';
}

function renderBizRules(rows: any[]): void {
  const el = $<HTMLElement>('br-tbody');
  if (!el) return;
  el.innerHTML = (rows ?? []).map(r =>
    `<tr><td>${r.line}</td><td><span class="badge badge-orange">${r.kind}</span></td>
    <td style="max-width:300px;font-size:12px;color:var(--muted);">${(r.predicate_raw ?? '').slice(0, 80)}</td>
    <td style="font-size:12px;">${(r.then_summary ?? '').slice(0, 60)}</td>
    <td style="font-size:12px;">${(r.else_summary ?? '').slice(0, 60)}</td></tr>`
  ).join('') || '<tr><td colspan="5" style="color:var(--muted);">None</td></tr>';
}

function renderFileIO(rows: any[]): void {
  const el = $<HTMLElement>('fio-tbody');
  if (!el) return;
  el.innerHTML = (rows ?? []).map(r =>
    `<tr><td style="font-weight:500;">${r.file_name}</td>
    <td><span class="badge ${r.operation === 'WRITE' || r.operation === 'REWRITE' ? 'badge-amber' : 'badge-sky'}">${r.operation}</span></td>
    <td style="font-size:12px;color:var(--muted);">${r.record_copybook ?? ''}</td></tr>`
  ).join('') || '<tr><td colspan="3" style="color:var(--muted);">No file I/O</td></tr>';
}

function renderProgRisks(rows: any[]): void {
  const el = $<HTMLElement>('risk-tbody');
  if (!el) return;
  el.innerHTML = (rows ?? []).map(r =>
    `<tr><td><span class="badge badge-orange">${r.kind}</span></td>
    <td><span class="sev-${r.severity}" style="font-weight:700;">${r.severity}</span></td>
    <td style="font-size:12px;color:var(--muted);">${r.note ?? ''}</td>
    <td>${r.line ?? ''}</td></tr>`
  ).join('') || '<tr><td colspan="4" style="color:var(--muted);">No risks detected</td></tr>';
}

async function loadProgSource(name: string): Promise<void> {
  const container = $<HTMLElement>('prog-source-container');
  if (!container) return;
  container.innerHTML = '<div style="color:var(--muted);text-align:center;padding:20px;">Loading source…</div>';
  try {
    const data = await apiFetch<{ content: string; line_count: number }>(`/programs/${encodeURIComponent(name)}/source`);
    container.innerHTML = `
      <div style="margin-bottom:8px;font-size:12px;color:var(--muted);">${data.line_count} lines</div>
      <pre style="margin:0;"><code class="language-cobol" style="font-size:11px;line-height:1.5;">${escapeHtml(data.content)}</code></pre>`;
    const el = container.querySelector('code');
    if (el) hljs.highlightElement(el as HTMLElement);
  } catch { /* ignore if no source */ }
}

// ── Diagrams ──────────────────────────────────────────────────────────────────
mermaid.initialize({ startOnLoad: false, theme: 'dark', darkMode: true,
  themeVariables: { primaryColor: '#003c51', primaryTextColor: '#f0f4f4', lineColor: '#0097ab',
    secondaryColor: '#1c242c', tertiaryColor: '#252c32' } });

async function loadDiagram(name: string, btn: Element | null): Promise<void> {
  document.querySelectorAll('.diag-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  const titles: Record<string, string> = {
    call_graph: 'Call Graph', transaction_flow: 'Transaction Flow',
    jcl_job_chain: 'JCL Job Chain', file_io_graph: 'File I/O Graph',
  };
  const titleEl = $<HTMLElement>('diag-title');
  if (titleEl) titleEl.textContent = titles[name] ?? name;
  const loading = $<HTMLElement>('diag-loading');
  const empty = $<HTMLElement>('diag-empty');
  const render = $<HTMLElement>('diag-render');
  if (loading) loading.style.display = '';
  if (empty) empty.style.display = 'none';
  if (render) render.innerHTML = '';
  try {
    const d = await apiFetch<{ content: string }>(`/diagrams/${name}`);
    diagSource = d.content;
    if (loading) loading.style.display = 'none';
    const srcEl = $<HTMLElement>('diag-source');
    if (srcEl) { srcEl.textContent = diagSource; hljs.highlightElement(srcEl); }
    if (render) {
      const id = 'mmd-' + Date.now();
      const { svg } = await mermaid.render(id, diagSource);
      render.innerHTML = svg;
      const svgEl = render.querySelector('svg');
      if (svgEl) (svgEl as HTMLElement).style.maxWidth = '100%';
    }
  } catch(e) {
    if (!isAbort(e)) {
      if (loading) loading.style.display = 'none';
      if (empty) empty.style.display = '';
    }
  }
}

function copyDiagramSource(): void {
  void navigator.clipboard.writeText(diagSource).then(() => showToast('Copied!'));
}

// ── Program Dropdowns ─────────────────────────────────────────────────────────
async function loadProgramDropdowns(): Promise<void> {
  try {
    const data = await apiFetch<{ items: Program[] }>('/programs?limit=500');
    programs = data.items ?? [];
    const opts = programs.map(p => `<option value="${p.name}">${p.name}</option>`).join('');
    ['spec-program'].forEach(id => {
      const el = $<HTMLSelectElement>(id);
      if (!el) return;
      const cur = el.value;
      el.innerHTML = '<option value="">— select program —</option>' +
        programs.map(p => `<option value="${p.name}" ${p.name === cur ? 'selected' : ''}>${p.name}</option>`).join('');
    });
    const transformSel = $<HTMLSelectElement>('transform-program');
    if (transformSel && transformSel.options.length <= 1) {
      transformSel.innerHTML = '<option value="">— select program —</option>' + opts;
    }
  } catch(e) {
    if (!isAbort(e)) console.warn('loadProgramDropdowns failed:', e);
  }
}

// ── Spec Generator ────────────────────────────────────────────────────────────
async function loadCurrentModel(): Promise<void> {
  try {
    const s = await apiFetch<{ provider: string; llm_provider: string; openai_model: string; gemini_model: string }>('/settings');
    const model = (s.provider === 'gemini' || s.llm_provider === 'gemini') ? s.gemini_model : s.openai_model;
    const el = $<HTMLElement>('spec-model-badge');
    if (el) el.textContent = `${s.llm_provider || s.provider} / ${model}`;
  } catch { /* ignore */ }
}

async function onSpecProgramChange(): Promise<void> {
  const prog = ($<HTMLSelectElement>('spec-program'))?.value ?? '';
  const scope = ($<HTMLSelectElement>('spec-scope'))?.value ?? 'program';
  if (!prog) { const u = $<HTMLInputElement>('spec-uuid'); if (u) u.value = ''; return; }
  if (scope === 'program') {
    try {
      const d = await apiFetch<{ uuid: string }>(`/programs/${encodeURIComponent(prog)}`);
      const u = $<HTMLInputElement>('spec-uuid');
      if (u) u.value = d.uuid ?? '';
    } catch { /* ignore */ }
  } else {
    await loadParaDropdown(prog);
  }
}

async function onSpecScopeChange(): Promise<void> {
  const scope = ($<HTMLSelectElement>('spec-scope'))?.value ?? 'program';
  const wrap = $<HTMLElement>('spec-para-wrap');
  if (wrap) wrap.style.display = scope === 'paragraph' ? '' : 'none';
  await onSpecProgramChange();
}

async function loadParaDropdown(prog: string): Promise<void> {
  try {
    const d = await apiFetch<{ paragraphs: any[] }>(`/programs/${encodeURIComponent(prog)}/detail`);
    const sel = $<HTMLSelectElement>('spec-paragraph');
    if (!sel) return;
    sel.innerHTML = (d.paragraphs ?? []).map(p => `<option value="${p.uuid}">${p.name} (L${p.start_line})</option>`).join('');
    const u = $<HTMLInputElement>('spec-uuid');
    if (u && sel.options[0]) u.value = sel.options[0].value;
    sel.onchange = () => { if (u) u.value = sel.value; };
  } catch { /* ignore */ }
}

async function generateSpecPersonas(): Promise<void> {
  const prog  = ($<HTMLSelectElement>('spec-program'))?.value ?? '';
  const scope = ($<HTMLSelectElement>('spec-scope'))?.value ?? 'program';
  const uuid_ = ($<HTMLInputElement>('spec-uuid'))?.value ?? '';
  if (!prog && !uuid_) { showToast('Select a program first', 'error'); return; }

  const personas: string[] = ['business_summary','highlevel_arch','lowlevel_arch',
    'functional_spec','technical_spec','modernization_spec']
    .filter(p => ($<HTMLInputElement>(`persona-${p}`))?.checked);
  if (!personas.length) { showToast('Select at least one persona', 'error'); return; }

  const btn = $<HTMLButtonElement>('spec-btn');
  const progressEl = $<HTMLElement>('spec-progress');
  const progressMsg = $<HTMLElement>('spec-progress-msg');
  const progressFill = $<HTMLElement>('spec-progress-fill');
  const tabsEl = $<HTMLElement>('spec-tabs');
  const contentEl = $<HTMLElement>('spec-tab-content');
  const emptyState = $<HTMLElement>('spec-empty-state');

  if (btn) btn.disabled = true;
  if (progressEl) progressEl.style.display = '';
  if (emptyState) emptyState.style.display = 'none';

  // Clear old tabs
  _personaResults = {};
  if (tabsEl) tabsEl.innerHTML = '';
  if (contentEl) contentEl.innerHTML = '';

  const PERSONA_LABELS: Record<string, string> = {
    business_summary: 'Business Summary', highlevel_arch: 'High-Level Arch',
    lowlevel_arch: 'Low-Level Arch', functional_spec: 'Functional Spec',
    technical_spec: 'Technical Spec', modernization_spec: 'Modernisation',
  };

  // Add pending tabs
  for (const p of personas) {
    addPersonaTab(p, PERSONA_LABELS[p] ?? p, 'pending', tabsEl, contentEl);
  }

  let done = 0;
  try {
    const resp = await fetch('/generate-spec/personas', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ program_name: prog, scope, uuid: uuid_, personas }),
      signal: sig(),
    });
    if (!resp.ok) throw new Error(await resp.text());

    const reader = resp.body?.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    while (reader) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buf += decoder.decode(chunk.value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        try {
          const evt = JSON.parse(line.slice(5).trim());
          if (evt.event === 'persona_done') {
            _personaResults[evt.persona] = evt.content ?? '';
            done++;
            const pct = Math.round((done / personas.length) * 100);
            if (progressFill) progressFill.style.width = pct + '%';
            if (progressMsg) progressMsg.textContent = `${done}/${personas.length} personas complete…`;
            updatePersonaTab(evt.persona, evt.content, tabsEl, contentEl, PERSONA_LABELS);
          } else if (evt.event === 'persona_error') {
            updatePersonaTabError(evt.persona, evt.error, tabsEl);
          } else if (evt.event === 'all_done') {
            if (progressMsg) progressMsg.textContent = 'All personas complete!';
          }
        } catch { /* partial JSON */ }
      }
    }
  } catch(e) {
    if (!isAbort(e)) showToast(`Generation failed: ${(e as Error).message}`, 'error');
  } finally {
    if (btn) btn.disabled = false;
    if (progressEl) setTimeout(() => { if (progressEl) progressEl.style.display = 'none'; }, 2000);
    const mdBtn  = $<HTMLButtonElement>('spec-export-md');
    const pdfBtn = $<HTMLButtonElement>('spec-export-pdf');
    if (mdBtn)  mdBtn.disabled  = Object.keys(_personaResults).length === 0;
    if (pdfBtn) pdfBtn.disabled = Object.keys(_personaResults).length === 0;
  }
}

function addPersonaTab(persona: string, label: string, state: string,
                       tabsEl: HTMLElement|null, contentEl: HTMLElement|null): void {
  if (tabsEl) {
    const tab = document.createElement('div');
    tab.id = `persona-tab-${persona}`;
    tab.className = 'tab';
    tab.style.cssText = 'padding:8px 14px;font-size:12px;';
    tab.innerHTML = `<span id="persona-tab-dot-${persona}" style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#fbbf24;margin-right:5px;vertical-align:middle;"></span>${label}`;
    tab.onclick = () => switchPersonaTab(persona);
    tabsEl.appendChild(tab);
  }
  if (contentEl) {
    const div = document.createElement('div');
    div.id = `persona-content-${persona}`;
    div.style.display = 'none';
    div.style.cssText = 'display:none;white-space:pre-wrap;font-size:13px;line-height:1.7;color:var(--text);';
    div.textContent = 'Generating…';
    contentEl.appendChild(div);
  }
}

function updatePersonaTab(persona: string, content: string,
                          tabsEl: HTMLElement|null, contentEl: HTMLElement|null,
                          labels: Record<string,string>): void {
  const dot = $<HTMLElement>(`persona-tab-dot-${persona}`);
  if (dot) dot.style.background = '#4ade80';
  const div = $<HTMLElement>(`persona-content-${persona}`);
  if (div) div.textContent = content;
  // Auto-switch to first completed tab
  if (_activePersonaTab === '') switchPersonaTab(persona);
}

function updatePersonaTabError(persona: string, error: string, tabsEl: HTMLElement|null): void {
  const dot = $<HTMLElement>(`persona-tab-dot-${persona}`);
  if (dot) dot.style.background = '#f87171';
  const div = $<HTMLElement>(`persona-content-${persona}`);
  if (div) { div.textContent = `Error: ${error}`; div.style.color = '#f87171'; }
}

function switchPersonaTab(persona: string): void {
  _activePersonaTab = persona;
  document.querySelectorAll('[id^="persona-tab-"]').forEach(t => (t as HTMLElement).classList.remove('active'));
  document.querySelectorAll('[id^="persona-content-"]').forEach(d => { (d as HTMLElement).style.display = 'none'; });
  const tab = $<HTMLElement>(`persona-tab-${persona}`);
  const div = $<HTMLElement>(`persona-content-${persona}`);
  if (tab) tab.classList.add('active');
  if (div) div.style.display = 'block';
}

function toggleAllPersonas(state: boolean): void {
  ['business_summary','highlevel_arch','lowlevel_arch','functional_spec','technical_spec','modernization_spec']
    .forEach(p => {
      const cb = $<HTMLInputElement>(`persona-${p}`);
      if (cb) cb.checked = state;
    });
}

function exportSpecMd(): void {
  if (!Object.keys(_personaResults).length) return;
  const prog = ($<HTMLSelectElement>('spec-program'))?.value ?? 'spec';
  const content = Object.entries(_personaResults)
    .map(([p, c]) => `# ${p.replace(/_/g,' ').replace(/\b\w/g, l => l.toUpperCase())}\n\n${c}\n\n---\n\n`)
    .join('');
  const blob = new Blob([content], { type: 'text/markdown' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `${prog}_spec.md`;
  a.click();
}

async function exportSpecPdf(): Promise<void> {
  if (!Object.keys(_personaResults).length) return;
  const prog = ($<HTMLSelectElement>('spec-program'))?.value ?? 'spec';
  const content = Object.entries(_personaResults)
    .map(([p, c]) => `# ${p.replace(/_/g,' ').replace(/\b\w/g, l => l.toUpperCase())}\n\n${c}\n\n---\n\n`)
    .join('');
  try {
    const resp = await fetch('/specs/export/pdf', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, title: `${prog} — Specification` }),
    });
    if (!resp.ok) { showToast('PDF export failed — install weasyprint', 'error'); return; }
    const blob = await resp.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `${prog}_spec.pdf`;
    a.click();
    showToast('PDF downloaded!');
  } catch(e) {
    if (!isAbort(e)) showToast(`PDF failed: ${(e as Error).message}`, 'error');
  }
}

function copySpec(): void {
  const content = Object.values(_personaResults).join('\n\n---\n\n');
  void navigator.clipboard.writeText(content).then(() => showToast('Copied!'));
}

// ── Holistic Modernization Report ─────────────────────────────────────────────
let _modReportText = '';

async function generateModernizationReport(): Promise<void> {
  const btn = $<HTMLButtonElement>('mod-btn');
  const progress = $<HTMLElement>('mod-progress');
  const progressMsg = $<HTMLElement>('mod-progress-msg');
  const result = $<HTMLElement>('mod-result');
  const preview = $<HTMLElement>('mod-preview');
  const useLlm = ($<HTMLInputElement>('mod-use-llm'))?.checked ?? false;

  if (btn) btn.disabled = true;
  if (result) result.style.display = 'none';
  if (progress) progress.style.display = '';
  if (progressMsg) progressMsg.textContent = 'Building holistic modernization report from ANTLR artifacts…';

  try {
    const resp = await fetch('/generate-modernization-report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ use_llm: useLlm }),
      signal: sig(),
    });
    if (!resp.ok) throw new Error(await resp.text());

    const reader = resp.body?.getReader();
    const decoder = new TextDecoder();
    let done = false;
    let lastEvt: Record<string, unknown> = {};

    while (!done && reader) {
      const chunk = await reader.read();
      done = chunk.done;
      if (chunk.value) {
        const text = decoder.decode(chunk.value);
        for (const line of text.split('\n')) {
          if (!line.startsWith('data:')) continue;
          try {
            const evt = JSON.parse(line.slice(5).trim()) as Record<string, unknown>;
            lastEvt = evt;
            if (evt.event === 'start' && progressMsg)
              progressMsg.textContent = String(evt.message ?? 'Generating…');
            if (evt.event === 'done') done = true;
          } catch { /* partial line */ }
        }
      }
    }

    if (lastEvt.event === 'done') {
      // Fetch the report text
      const specRes = await apiFetch<{ program: string; markdown: string }>(
        '/specs/MODERNIZATION_REPORT'
      );
      _modReportText = specRes.markdown ?? '';

      const sizeKb = lastEvt.size_kb as number ?? 0;
      const title = $<HTMLElement>('mod-result-title');
      const meta  = $<HTMLElement>('mod-result-meta');
      if (title) title.textContent = 'Report generated — CardDemo Modernization Spec';
      if (meta)  meta.textContent  = `${sizeKb} KB · 10 sections · ANTLR-derived artifacts`;
      if (preview) preview.textContent = _modReportText.slice(0, 3000) + '\n\n… (click View Report for full content)';
      if (result)  result.style.display = '';
      showToast('Modernization report ready!');
    } else if (lastEvt.event === 'error') {
      showToast(`Error: ${lastEvt.message}`, 'error');
    }
  } catch (e) {
    if (!isAbort(e)) showToast(`Failed: ${(e as Error).message}`, 'error');
  } finally {
    if (progress) progress.style.display = 'none';
    if (btn) btn.disabled = false;
  }
}

function viewModernizationReport(): void {
  const preview = $<HTMLElement>('mod-preview');
  if (!_modReportText) { showToast('Generate the report first', 'error'); return; }
  if (preview) {
    preview.textContent = _modReportText;
    preview.style.maxHeight = preview.style.maxHeight === 'none' ? '400px' : 'none';
  }
}

function copyModernizationReport(): void {
  if (!_modReportText) { showToast('Nothing to copy yet', 'error'); return; }
  void navigator.clipboard.writeText(_modReportText).then(() => showToast('Full report copied to clipboard!'));
}

// ── Transform Page ────────────────────────────────────────────────────────────

const TRANSFORM_STEP_NAMES = [
  'Discovery', 'Specification', 'Architecture',
  'Domain Model', 'Business Logic', 'Integration', 'Tests'
];

async function loadTransformPage(): Promise<void> {
  // Load portfolio stats for the readiness banner
  try {
    const s = await apiFetch<any>('/stats');
    const set = (id: string, v: any) => { const el = $<HTMLElement>(id); if (el) el.textContent = String(v ?? '—'); };
    set('tx-port-programs', (s.programs ?? 0).toLocaleString());
    set('tx-port-rules', (s.business_rules ?? 0).toLocaleString());
    set('tx-port-risks-high', 0); // will be fetched below
    set('tx-port-jcl', (s.jcl_files ?? 0).toLocaleString());
  } catch { /* DB not ready */ }
  try {
    const cov = await apiFetch<any>('/coverage');
    const highRisks = (cov.risk_summary?.HIGH ?? 0);
    const el = $<HTMLElement>('tx-port-risks-high');
    if (el) el.textContent = String(highRisks);
  } catch { /* ignore */ }

  // Populate program dropdown for HITL deep-dive
  const sel = $<HTMLSelectElement>('transform-program');
  if (!sel || sel.options.length > 1) return;
  try {
    const data = await apiFetch<{ programs: any[] }>('/programs');
    sel.innerHTML = '<option value="">— select program —</option>' +
      (data.programs ?? []).map((p: any) => `<option value="${p.name}">${p.name}</option>`).join('');
  } catch { /* ignore */ }
}

async function startTransform(): Promise<void> {
  const prog = ($<HTMLSelectElement>('transform-program'))?.value ?? '';
  const fw   = ($<HTMLInputElement>('transform-framework'))?.value ?? 'Spring Boot';
  const auto = ($<HTMLInputElement>('transform-auto'))?.checked ?? false;
  if (!prog) { showToast('Select a program first', 'error'); return; }

  const btn = $<HTMLButtonElement>('transform-start-btn');
  if (btn) btn.disabled = true;

  try {
    const session = await apiFetch<any>('/transform/sessions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ program_name: prog, framework: fw, auto_mode: auto }),
    });
    _transformSessionId = session.session_id;
    _transformSteps = session.steps ?? [];
    _currentTransformStep = 0;
    renderTransformUI(auto);
    showToast(`Session ${_transformSessionId} created — run Step 1`);
  } catch(e) {
    if (!isAbort(e)) showToast(`Failed: ${(e as Error).message}`, 'error');
    if (btn) btn.disabled = false;
  }
}

function renderTransformUI(autoMode: boolean): void {
  const progressBar = $<HTMLElement>('transform-progress-bar');
  const stepPanel   = $<HTMLElement>('transform-step-panel');
  const complete    = $<HTMLElement>('transform-complete');
  if (progressBar) progressBar.style.display = '';
  if (stepPanel)   stepPanel.style.display   = '';
  if (complete)    complete.style.display    = 'none';

  // Render step indicators
  const indicators = $<HTMLElement>('transform-step-indicators');
  if (indicators) {
    indicators.innerHTML = TRANSFORM_STEP_NAMES.map((name, i) => `
      <div style="display:flex;align-items:center;flex:1;" id="step-ind-${i}">
        <div style="text-align:center;flex:1;">
          <div style="width:32px;height:32px;border-radius:50%;background:var(--surface2);border:2px solid var(--border);
               display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;
               margin:0 auto 4px;color:var(--muted);" id="step-bubble-${i}">${i+1}</div>
          <div style="font-size:10px;color:var(--muted);">${name}</div>
        </div>
        ${i < TRANSFORM_STEP_NAMES.length - 1 ? '<div style="flex:1;height:2px;background:var(--border);margin-bottom:14px;" id="step-connector-'+i+'"></div>' : ''}
      </div>
    `).join('');
  }

  // Render the first step
  renderStepCard(0, autoMode);
}

function renderStepCard(stepId: number, autoMode: boolean): void {
  const panel = $<HTMLElement>('transform-step-panel');
  if (!panel) return;
  const stepName = TRANSFORM_STEP_NAMES[stepId] ?? `Step ${stepId + 1}`;
  panel.innerHTML = `
    <div class="card" id="active-step-card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
        <div>
          <span class="badge badge-teal" style="margin-right:8px;">Step ${stepId + 1} of ${TRANSFORM_STEP_NAMES.length}</span>
          <span style="font-weight:700;font-size:16px;">${stepName}</span>
        </div>
        ${autoMode ? '<span class="badge badge-green">Auto Mode</span>' : ''}
      </div>
      <div id="step-run-area">
        <button id="step-run-btn" class="btn btn-primary" onclick="runTransformStep(${stepId})">
          <svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polygon points="5 3 19 12 5 21 5 3"/></svg>
          Run ${stepName} Agent
        </button>
      </div>
      <div id="step-loading" style="display:none;color:var(--muted);font-size:13px;padding:16px 0;">
        <span class="spin" style="display:inline-block;margin-right:8px;">⟳</span> Agent running…
      </div>
      <div id="step-output-area" style="display:none;margin-top:16px;">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;">
          <div>
            <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;">Output</div>
            <div id="step-output" style="background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:14px;max-height:400px;overflow:auto;font-size:12px;white-space:pre-wrap;line-height:1.6;"></div>
          </div>
          <div>
            <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px;">Rationale</div>
            <div id="step-rationale" style="background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:14px;max-height:400px;overflow:auto;font-size:12px;color:var(--muted);white-space:pre-wrap;line-height:1.6;"></div>
          </div>
        </div>
        <div style="display:flex;gap:10px;align-items:center;">
          <button class="btn btn-success" onclick="approveTransformStep(${stepId})">✓ Approve &amp; Continue</button>
          <button class="btn btn-danger" onclick="rejectTransformStep(${stepId})">✗ Reject &amp; Re-run</button>
          <div id="hitl-feedback-row" style="flex:1;display:flex;gap:8px;display:none;">
            <input id="hitl-feedback" type="text" placeholder="Feedback for re-run…" style="flex:1;">
          </div>
          ${autoMode ? '<span class="badge badge-green" style="margin-left:auto;">LLM will auto-approve</span>' : ''}
        </div>
      </div>
    </div>
  `;
}

async function runTransformStep(stepId: number): Promise<void> {
  const runArea  = $<HTMLElement>('step-run-area');
  const loading  = $<HTMLElement>('step-loading');
  const outArea  = $<HTMLElement>('step-output-area');
  if (runArea)  runArea.style.display  = 'none';
  if (loading)  loading.style.display  = '';
  if (outArea)  outArea.style.display  = 'none';

  try {
    const result = await apiFetch<any>(
      `/transform/sessions/${_transformSessionId}/steps/${stepId}/run`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }
    );
    const outEl  = $<HTMLElement>('step-output');
    const ratEl  = $<HTMLElement>('step-rationale');
    if (outEl)  outEl.textContent  = result.output   ?? '(no output)';
    if (ratEl)  ratEl.textContent  = result.rationale ?? '(no rationale)';
    if (loading) loading.style.display = 'none';
    if (outArea) outArea.style.display = '';
    updateStepBubble(stepId, 'awaiting');

    // Auto-approve if auto_mode was set server-side
    if (result.auto_approved) {
      await new Promise(r => setTimeout(r, 800));
      await approveTransformStep(stepId);
    }
  } catch(e) {
    if (!isAbort(e)) {
      if (loading) loading.style.display = 'none';
      if (runArea) runArea.style.display = '';
      showToast(`Step failed: ${(e as Error).message}`, 'error');
    }
  }
}

async function approveTransformStep(stepId: number): Promise<void> {
  try {
    const res = await apiFetch<any>(
      `/transform/sessions/${_transformSessionId}/steps/${stepId}/approve`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }
    );
    updateStepBubble(stepId, 'approved');
    const autoMode = ($<HTMLInputElement>('transform-auto'))?.checked ?? false;
    const nextStep = res.next_step;
    if (nextStep !== null && nextStep !== undefined) {
      _currentTransformStep = nextStep;
      renderStepCard(nextStep, autoMode);
      if (autoMode) {
        await new Promise(r => setTimeout(r, 400));
        await runTransformStep(nextStep);
      }
    } else {
      // All done
      const panel    = $<HTMLElement>('transform-step-panel');
      const complete = $<HTMLElement>('transform-complete');
      if (panel)    panel.style.display    = 'none';
      if (complete) complete.style.display = '';
      showToast('All steps complete! Download your output.', 'ok');
    }
  } catch(e) {
    if (!isAbort(e)) showToast(`Approve failed: ${(e as Error).message}`, 'error');
  }
}

async function rejectTransformStep(stepId: number): Promise<void> {
  const fb = ($<HTMLInputElement>('hitl-feedback'))?.value ?? '';
  const fbRow = $<HTMLElement>('hitl-feedback-row');
  if (!fb) {
    if (fbRow) fbRow.style.display = 'flex';
    showToast('Enter feedback before rejecting', 'error');
    return;
  }
  try {
    await apiFetch<any>(
      `/transform/sessions/${_transformSessionId}/steps/${stepId}/reject`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ feedback: fb }) }
    );
    updateStepBubble(stepId, 'rejected');
    const autoMode = ($<HTMLInputElement>('transform-auto'))?.checked ?? false;
    renderStepCard(stepId, autoMode);
    showToast('Step rejected — edit feedback and re-run', 'error');
  } catch(e) {
    if (!isAbort(e)) showToast(`Reject failed: ${(e as Error).message}`, 'error');
  }
}

function updateStepBubble(stepId: number, state: 'awaiting'|'approved'|'rejected'|'running'): void {
  const bubble = $<HTMLElement>(`step-bubble-${stepId}`);
  const conn   = $<HTMLElement>(`step-connector-${stepId}`);
  if (!bubble) return;
  const colors: Record<string, string> = {
    running: '#fbbf24', awaiting: '#009ddc', approved: '#4ade80', rejected: '#f87171'
  };
  bubble.style.background   = colors[state] ?? 'var(--surface2)';
  bubble.style.borderColor  = colors[state] ?? 'var(--border)';
  bubble.style.color        = '#fff';
  if (state === 'approved' && conn) conn.style.background = '#4ade80';
}

function resetTransform(): void {
  _transformSessionId = '';
  _transformSteps = [];
  _currentTransformStep = 0;
  const progressBar = $<HTMLElement>('transform-progress-bar');
  const stepPanel   = $<HTMLElement>('transform-step-panel');
  const complete    = $<HTMLElement>('transform-complete');
  const startBtn    = $<HTMLButtonElement>('transform-start-btn');
  if (progressBar) progressBar.style.display = 'none';
  if (stepPanel)   stepPanel.style.display   = 'none';
  if (complete)    complete.style.display    = 'none';
  if (startBtn)    startBtn.disabled         = false;
}

async function downloadTransformOutput(format: 'md' | 'pdf'): Promise<void> {
  if (!_transformSessionId) return;
  try {
    const session = await apiFetch<any>(`/transform/sessions/${_transformSessionId}`);
    const combined = TRANSFORM_STEP_NAMES.map((name, i) => {
      const step = session.steps?.[i];
      return `# Step ${i+1}: ${name}\n\n${step?.output ?? '(pending)'}\n\n---\n\n`;
    }).join('');
    const title = `${session.program_name}_${session.framework.replace(/ /g,'_')}_Transform`;
    if (format === 'md') {
      const blob = new Blob([combined], { type: 'text/markdown' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${title}.md`;
      a.click();
    } else {
      const resp = await fetch('/specs/export/pdf', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: combined, title }),
      });
      if (!resp.ok) { showToast('PDF export failed', 'error'); return; }
      const blob = await resp.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${title}.pdf`;
      a.click();
    }
  } catch(e) {
    if (!isAbort(e)) showToast(`Download failed: ${(e as Error).message}`, 'error');
  }
}

// ── Pipeline ──────────────────────────────────────────────────────────────────
const STAGE_KEYWORDS: Record<string, string> = {
  preprocessing: 'preprocessing', 'copy/replace': 'preprocessing',
  'phase 1 cobol': 'parsing', proleap: 'parsing', jar: 'parsing',
  'layer 1 ast': 'layer1', 'layer 1': 'layer1',
  'layer 2 symbol': 'layer2', 'layer 2': 'layer2',
  'layer 3 cfg': 'layer3', 'layer 3': 'layer3', cfg: 'layer3', 'def-use': 'layer3',
  'layer 4 call': 'layer4', 'layer 4': 'layer4', 'call graph': 'layer4',
  'layer 5 business': 'layer5', 'layer 5': 'layer5', 'phase 7 coverage': 'layer5', coverage: 'layer5',
};
const STAGE_ORDER = ['preprocessing','parsing','layer1','layer2','layer3','layer4','layer5'];
const stagesComplete = new Set<string>();

function setPipelineUI(running: boolean): void {
  pipelineRunning = running;
  const runBtn = $<HTMLButtonElement>('run-btn');
  const cancelBtn = $<HTMLElement>('cancel-btn');
  const progressCard = $<HTMLElement>('pipeline-progress-card');
  if (runBtn) runBtn.disabled = running;
  if (cancelBtn) cancelBtn.style.display = running ? '' : 'none';
  if (progressCard) progressCard.style.display = running ? '' : 'none';
  if (!running) { stagesComplete.clear(); updatePipelineProgress(0); }
}

function updatePipelineProgress(pct: number): void {
  const fill = $<HTMLElement>('pipeline-progress-fill');
  const label = $<HTMLElement>('pipeline-progress-pct');
  if (fill) fill.style.width = pct + '%';
  if (label) label.textContent = Math.round(pct) + '%';
}

// ── Source tab switching ──────────────────────────────────────────────────────

let _detectedCorpus = '';

function switchSourceTab(tab: 'github' | 'zip' | 'local'): void {
  ['github', 'zip', 'local'].forEach(t => {
    const el = $<HTMLElement>(`source-${t}`);
    if (el) el.style.display = t === tab ? '' : 'none';
  });
  document.querySelectorAll<HTMLElement>('#source-tabs .tab').forEach((el, i) => {
    const tabs = ['github', 'zip', 'local'];
    el.classList.toggle('active', tabs[i] === tab);
  });
}

async function cloneGithub(): Promise<void> {
  const urlEl = $<HTMLInputElement>('github-url');
  const url = urlEl?.value.trim() ?? '';
  if (!url) return;
  const logEl = $<HTMLElement>('clone-log');
  if (!logEl) return;
  logEl.style.display = '';
  logEl.innerHTML = `<div class="card-sm" style="font-size:12px;color:var(--muted);">Cloning…</div>`;

  try {
    const res = await fetch('/pipeline/clone-github', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    if (!res.body) throw new Error('No response body');
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    logEl.innerHTML = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop() ?? '';
      for (const part of parts) {
        if (!part.startsWith('data:')) continue;
        try {
          const ev = JSON.parse(part.slice(5));
          if (ev.kind === 'result') {
            const d = JSON.parse(ev.msg);
            _detectedCorpus = d.corpus || d.repo;
            const preview = $<HTMLElement>('pipeline-corpus-preview');
            const previewPath = $<HTMLElement>('corpus-preview-path');
            if (preview) preview.style.display = '';
            if (previewPath) previewPath.textContent = _detectedCorpus;
            logEl.innerHTML += `<div style="font-size:12px;color:#4ade80;margin-top:6px;">✓ Ready — corpus: ${d.corpus || 'auto-detect'}</div>`;
          } else {
            const div = document.createElement('div');
            div.className = `log-line log-${ev.kind}`;
            div.style.fontSize = '12px';
            div.textContent = ev.msg;
            logEl.appendChild(div);
          }
        } catch { /* skip */ }
      }
    }
  } catch(e) {
    logEl.innerHTML += `<div style="color:#f87171;font-size:12px;">${(e as Error).message}</div>`;
  }
}

async function uploadZip(file: File | null | undefined): Promise<void> {
  if (!file) return;
  const statusEl = $<HTMLElement>('zip-status');
  if (statusEl) statusEl.textContent = `Uploading ${file.name}…`;
  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('/pipeline/upload-zip', { method: 'POST', body: form });
    const d = await res.json();
    if (d.ok) {
      _detectedCorpus = d.corpus || d.repo;
      const preview = $<HTMLElement>('pipeline-corpus-preview');
      const previewPath = $<HTMLElement>('corpus-preview-path');
      if (preview) preview.style.display = '';
      if (previewPath) previewPath.textContent = _detectedCorpus;
      if (statusEl) statusEl.innerHTML = `<span style="color:#4ade80;">✓ Extracted to ${d.repo} — corpus: ${d.corpus || 'auto-detect'}</span>`;
    } else {
      if (statusEl) statusEl.innerHTML = `<span style="color:#f87171;">Error: ${d.detail ?? 'Upload failed'}</span>`;
    }
  } catch(e) {
    if (statusEl) statusEl.innerHTML = `<span style="color:#f87171;">${(e as Error).message}</span>`;
  }
}

function handleZipDrop(event: DragEvent): void {
  event.preventDefault();
  const el = document.getElementById('zip-drop-zone');
  if (el) el.style.borderColor = 'var(--border)';
  const file = event.dataTransfer?.files?.[0];
  if (file?.name.endsWith('.zip')) void uploadZip(file);
}

async function runPipeline(): Promise<void> {
  if (pipelineRunning) return;
  setPipelineUI(true);
  const log = $<HTMLElement>('pipeline-log')!;
  log.innerHTML = '';
  stagesComplete.clear();
  updatePipelineProgress(0);

  // Prefer auto-detected corpus from clone/upload, then local path input
  const corpus = _detectedCorpus
    || ($<HTMLInputElement>('corpus-path'))?.value
    || 'external/carddemo/app/cbl';
  try {
    const res = await fetch('/pipeline/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ corpus }), signal: sig(),
    });
    if (!res.body) throw new Error('No response body');
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop() ?? '';
      for (const part of parts) {
        if (!part.startsWith('data:')) continue;
        try {
          const ev = JSON.parse(part.slice(5));
          appendLog(log, ev);
          if (ev.kind === 'done') setPipelineUI(false);
        } catch { /* skip */ }
      }
    }
  } catch(e) {
    if (!isAbort(e)) appendLog(log, { kind: 'error', msg: (e as Error).message });
  }
  setPipelineUI(false);
}

function runSmoke(): void {
  const el = $<HTMLInputElement>('corpus-path');
  if (el) el.value = 'external/carddemo/app/cbl';
  void runPipeline();
}

function appendLog(log: HTMLElement, ev: { kind: string; msg: string; ts?: number }): void {
  const div = document.createElement('div');
  div.className = `log-line log-${ev.kind}`;
  const ts = ev.ts ? new Date(ev.ts * 1000).toLocaleTimeString() : '';
  div.textContent = `[${ts}] ${ev.msg}`;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;

  const msg = (ev.msg ?? '').toLowerCase();
  for (const [kw, stage] of Object.entries(STAGE_KEYWORDS)) {
    if (msg.includes(kw)) {
      const icon = document.querySelector<HTMLElement>(`.stage-item[data-stage="${stage}"] .stage-icon`);
      if (icon) {
        icon.textContent = ev.kind === 'error' ? '❌' : '✅';
        stagesComplete.add(stage);
        updatePipelineProgress((stagesComplete.size / STAGE_ORDER.length) * 100);
      }
    }
  }
}

async function cancelPipeline(): Promise<void> {
  try {
    await fetch('/pipeline/cancel', { method: 'POST' });
    abortCurrentPage();
    _ctrl = new AbortController();
    setPipelineUI(false);
    showToast('Pipeline cancelled');
  } catch(e) {
    showToast('Cancel failed: ' + (e as Error).message, 'error');
  }
}

// ── Coverage ──────────────────────────────────────────────────────────────────
async function loadCoverage(): Promise<void> {
  try {
    const d = await apiFetch<{ ok_files: number; total_files: number; coverage_pct: number; files: CoverageRow[] }>('/reports/coverage');
    coverageAllRows = d.files ?? [];
    const okEl = $<HTMLElement>('cov-ok');
    const failEl = $<HTMLElement>('cov-fail');
    const pctEl = $<HTMLElement>('cov-pct');
    if (okEl) okEl.textContent = String(d.ok_files);
    if (failEl) failEl.textContent = String(d.total_files - d.ok_files);
    if (pctEl) pctEl.textContent = d.coverage_pct + '%';

    const ctx1 = ($<HTMLCanvasElement>('cov-donut'))?.getContext('2d');
    if (ctx1) {
      if (covDonut) covDonut.destroy();
      covDonut = new Chart(ctx1, {
        type: 'doughnut',
        data: { labels: ['OK','Failed'], datasets: [{ data: [d.ok_files, d.total_files - d.ok_files], backgroundColor: ['#4ade80','#f87171'], borderWidth: 0 }] },
        options: { plugins: { legend: { labels: { color: '#7BA8D4' } } }, cutout: '70%' },
      });
    }
    const failures = coverageAllRows.filter(f => f.status !== 'OK');
    const counts: Record<string, number> = {};
    failures.forEach(f => { counts[f.status ?? 'unknown'] = (counts[f.status ?? 'unknown'] ?? 0) + 1; });
    const ctx2 = ($<HTMLCanvasElement>('cov-bar'))?.getContext('2d');
    if (ctx2) {
      if (covBar2) covBar2.destroy();
      covBar2 = new Chart(ctx2, {
        type: 'bar',
        data: { labels: Object.keys(counts), datasets: [{ data: Object.values(counts), backgroundColor: '#006e74', borderRadius: 4, borderWidth: 0 }] },
        options: { plugins: { legend: { display: false } }, indexAxis: 'y',
          scales: { x: { ticks: { color: '#7BA8D4' }, grid: { color: '#0A3A80' } },
                   y: { ticks: { color: '#7BA8D4', font: { size: 11 } }, grid: { display: false } } } },
      });
    }
    renderCovTable(coverageAllRows);
  } catch(e) {
    if (!isAbort(e)) ['cov-ok','cov-fail','cov-pct'].forEach(id => { const el = $<HTMLElement>(id); if (el) el.textContent = '—'; });
  }
}

function renderCovTable(rows: CoverageRow[]): void {
  const el = $<HTMLElement>('cov-tbody');
  if (!el) return;
  el.innerHTML = rows.map(r => `
    <tr>
      <td style="font-size:12px;color:var(--muted);">${r.source_file.split('/').pop()}</td>
      <td><span class="badge ${r.status === 'OK' ? 'badge-green' : 'badge-red'}">${r.status}</span></td>
      <td style="font-size:12px;color:var(--muted);">${r.error_class ?? r.status ?? ''}</td>
      <td>${r.parse_time_ms ?? ''}</td>
    </tr>`).join('') || '<tr><td colspan="4" style="color:var(--muted);text-align:center;padding:30px;">No coverage data yet.</td></tr>';
}

function filterCovTable(): void {
  const q = ($<HTMLInputElement>('cov-filter'))?.value.toLowerCase() ?? '';
  renderCovTable(coverageAllRows.filter(r => r.source_file.toLowerCase().includes(q) || (r.status ?? '').toLowerCase().includes(q)));
}

// ── Risk Register ─────────────────────────────────────────────────────────────
async function loadRisks(): Promise<void> {
  try {
    const rows = await apiFetch<RiskRow[]>('/reports/risk-register');
    riskAllRows = rows ?? [];
    const high = riskAllRows.filter(r => r.severity === 'HIGH').length;
    const med  = riskAllRows.filter(r => r.severity === 'MEDIUM').length;
    const low  = riskAllRows.filter(r => r.severity === 'LOW').length;
    const hEl = $<HTMLElement>('risk-high');
    const mEl = $<HTMLElement>('risk-med');
    const lEl = $<HTMLElement>('risk-low');
    if (hEl) hEl.textContent = String(high);
    if (mEl) mEl.textContent = String(med);
    if (lEl) lEl.textContent = String(low);

    const ctx1 = ($<HTMLCanvasElement>('risk-donut'))?.getContext('2d');
    if (ctx1) {
      if (riskDonut) riskDonut.destroy();
      riskDonut = new Chart(ctx1, {
        type: 'doughnut',
        data: { labels: ['HIGH','MEDIUM','LOW'], datasets: [{ data: [high, med, low], backgroundColor: ['#f87171','#fbbf24','#4ade80'], borderWidth: 0 }] },
        options: { plugins: { legend: { labels: { color: '#7BA8D4' } } }, cutout: '70%' },
      });
    }
    const kinds: Record<string, number> = {};
    riskAllRows.forEach(r => { kinds[r.kind] = (kinds[r.kind] ?? 0) + 1; });
    const ctx2 = ($<HTMLCanvasElement>('risk-bar'))?.getContext('2d');
    if (ctx2) {
      if (riskBar2) riskBar2.destroy();
      riskBar2 = new Chart(ctx2, {
        type: 'bar',
        data: { labels: Object.keys(kinds), datasets: [{ data: Object.values(kinds), backgroundColor: '#006e74', borderRadius: 4, borderWidth: 0 }] },
        options: { plugins: { legend: { display: false } }, indexAxis: 'y',
          scales: { x: { ticks: { color: '#7BA8D4' }, grid: { color: '#0A3A80' } },
                   y: { ticks: { color: '#7BA8D4', font: { size: 11 } }, grid: { display: false } } } },
      });
    }
    renderRiskTable(riskAllRows);
  } catch(e) {
    if (!isAbort(e)) ['risk-high','risk-med','risk-low'].forEach(id => { const el = $<HTMLElement>(id); if (el) el.textContent = '—'; });
  }
}

function renderRiskTable(rows: RiskRow[]): void {
  const el = $<HTMLElement>('risk-tbody-full');
  if (!el) return;
  el.innerHTML = rows.map(r => `
    <tr>
      <td style="font-weight:600;color:var(--ust-sky);">${r.program_name ?? '—'}</td>
      <td><span class="badge badge-orange">${r.kind}</span></td>
      <td><span class="sev-${r.severity}" style="font-weight:700;">${r.severity}</span></td>
      <td style="font-size:12px;color:var(--muted);max-width:300px;">${r.note ?? ''}</td>
      <td>${r.line ?? ''}</td>
    </tr>`).join('') || '<tr><td colspan="5" style="color:var(--muted);text-align:center;padding:30px;">No risks detected. Run the pipeline first.</td></tr>';
}

function filterRiskTable(): void {
  const q = ($<HTMLInputElement>('risk-filter'))?.value.toLowerCase() ?? '';
  renderRiskTable(riskAllRows.filter(r =>
    (r.program_name ?? '').toLowerCase().includes(q) ||
    (r.kind ?? '').toLowerCase().includes(q) ||
    (r.severity ?? '').toLowerCase().includes(q)
  ));
}

// ── Settings ──────────────────────────────────────────────────────────────────
async function loadSettings(): Promise<void> {
  try {
    const s = await apiFetch<{
      provider: string; llm_provider: string; openai_model: string; gemini_model: string;
      openai_key_set: boolean; gemini_key_set: boolean;
    }>('/settings');

    const provEl = $<HTMLSelectElement>('settings-provider');
    if (provEl) provEl.value = s.llm_provider || s.provider;

    const curEl = $<HTMLElement>('settings-current-info');
    if (curEl) curEl.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:10px;">
        <div><span style="color:var(--muted);font-size:12px;">Provider</span><br><span style="font-weight:600;">${s.provider}</span></div>
        <div><span style="color:var(--muted);font-size:12px;">OpenAI Model</span><br><span style="font-weight:600;">${s.openai_model}</span></div>
        <div><span style="color:var(--muted);font-size:12px;">Gemini Model</span><br><span style="font-weight:600;">${s.gemini_model}</span></div>
        <div><span style="color:var(--muted);font-size:12px;">OpenAI Key</span><br><span class="badge ${s.openai_key_set ? 'badge-green' : 'badge-red'}">${s.openai_key_set ? '✓ Set' : 'Not set'}</span></div>
        <div><span style="color:var(--muted);font-size:12px;">Gemini Key</span><br><span class="badge ${s.gemini_key_set ? 'badge-green' : 'badge-red'}">${s.gemini_key_set ? '✓ Set' : 'Not set'}</span></div>
      </div>`;

    const activeProvider = s.llm_provider || s.provider;
    await loadModelsForProvider(activeProvider, activeProvider === 'gemini' ? s.gemini_model : s.openai_model);
  } catch(e) {
    if (!isAbort(e)) console.warn('Failed to load settings:', e);
  }
}

async function loadModelsForProvider(provider: string, currentModel: string): Promise<void> {
  const sel = $<HTMLSelectElement>('settings-model');
  if (!sel) return;
  sel.innerHTML = '<option>Loading…</option>';
  sel.disabled = true;
  try {
    const data = await apiFetch<{ provider: string; models: string[]; current_model: string }>('/models');
    sel.innerHTML = data.models.map(m => `<option value="${m}" ${m === currentModel ? 'selected' : ''}>${m}</option>`).join('');
    if (!sel.value && data.models.length) sel.value = data.models[0];
    sel.disabled = false;
  } catch {
    sel.innerHTML = `<option value="${currentModel}">${currentModel}</option>`;
    sel.disabled = false;
  }
}

async function onProviderChange(): Promise<void> {
  const provEl = $<HTMLSelectElement>('settings-provider');
  const provider = provEl?.value ?? 'openai';
  const openaiWrap = $<HTMLElement>('settings-openai-key-wrap');
  const geminiWrap = $<HTMLElement>('settings-gemini-key-wrap');
  if (openaiWrap) openaiWrap.style.display = provider === 'openai' ? '' : 'none';
  if (geminiWrap) geminiWrap.style.display = provider === 'gemini' ? '' : 'none';
  await loadModelsForProvider(provider, '');
}

async function saveSettings(): Promise<void> {
  const provider = ($<HTMLSelectElement>('settings-provider'))?.value ?? 'openai';
  const model    = ($<HTMLSelectElement>('settings-model'))?.value ?? '';
  const oaKey    = ($<HTMLInputElement>('settings-openai-key'))?.value?.trim() ?? '';
  const gmKey    = ($<HTMLInputElement>('settings-gemini-key'))?.value?.trim() ?? '';

  const body: Record<string, string> = { llm_provider: provider };
  if (provider === 'openai') body.openai_model = model;
  else body.gemini_model = model;
  if (oaKey) body.openai_api_key = oaKey;
  if (gmKey) body.gemini_api_key = gmKey;

  try {
    await apiFetch('/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const savedEl = $<HTMLElement>('settings-saved');
    if (savedEl) { savedEl.style.display = ''; setTimeout(() => { savedEl.style.display = 'none'; }, 3000); }
    showToast('Settings saved');
    await loadSettings();
  } catch(e) {
    if (!isAbort(e)) showToast('Save failed: ' + (e as Error).message, 'error');
  }
}

// ── Visualizations ────────────────────────────────────────────────────────────
async function loadVizProgramDropdown(): Promise<void> {
  try {
    const data = await apiFetch<{ items: Program[] }>('/programs?limit=500');
    const sel = $<HTMLSelectElement>('viz-program');
    if (!sel) return;
    sel.innerHTML = '<option value="">— select program —</option>' +
      (data.items ?? []).map(p => `<option value="${p.name}">${p.name}</option>`).join('');
  } catch(e) {
    if (!isAbort(e)) console.warn('Failed to load programs:', e);
  }
}

async function loadViz(): Promise<void> {
  const prog = ($<HTMLSelectElement>('viz-program'))?.value ?? '';
  if (!prog) return;
  await loadVizTab(currentVizTab, prog);
}

function switchVizTab(tab: string): void {
  currentVizTab = tab;
  document.querySelectorAll<HTMLElement>('#page-visualizations .tab-panel').forEach(p => { p.style.display = 'none'; });
  document.querySelectorAll('#page-visualizations .tab').forEach(t => t.classList.remove('active'));
  const panel = $<HTMLElement>(`viz-tab-${tab}`);
  if (panel) panel.style.display = '';
  const tabEl = document.querySelector<HTMLElement>(`#page-visualizations .tab[onclick="switchVizTab('${tab}')"]`);
  if (tabEl) tabEl.classList.add('active');
  const prog = ($<HTMLSelectElement>('viz-program'))?.value ?? '';
  if (prog) void loadVizTab(tab, prog);
}

async function loadVizTab(tab: string, prog: string): Promise<void> {
  if (tab === 'ast') await loadASTTree(prog);
  else if (tab === 'cfg') await loadCFG(prog);
  else if (tab === 'symbols') await loadSymbolTable(prog);
  else if (tab === 'complexity') await loadComplexity(prog);
  else if (tab === 'source') await loadSourceCode(prog);
}

// AST Tree
async function loadASTTree(prog: string): Promise<void> {
  const container = $<HTMLElement>('ast-container');
  if (!container) return;
  container.innerHTML = '<div style="color:var(--muted);text-align:center;padding:40px;">Loading AST…</div>';
  try {
    const data = await apiFetch<{ root: ASTNode }>(`/programs/${encodeURIComponent(prog)}/ast`);
    if (!data.root) {
      container.innerHTML = '<div style="color:var(--muted);text-align:center;padding:40px;">No AST data. Run the pipeline first.</div>';
      return;
    }
    container.innerHTML = `<div style="font-size:12px;margin-bottom:12px;color:var(--muted);">
      Click nodes to expand/collapse · ${prog}</div>
      <div id="ast-tree">${renderASTNode(data.root, 0)}</div>`;
  } catch(e) {
    if (!isAbort(e)) container.innerHTML = `<div style="color:#f87171;padding:20px;">Error: ${(e as Error).message}</div>`;
  }
}

function renderASTNode(node: ASTNode, depth: number): string {
  if (!node) return '';
  const indent = depth * 18;
  const hasChildren = (node.children?.length ?? 0) > 0;
  const kindColors: Record<string, string> = {
    program: '#5ecdd1', paragraph: '#4ade80', section: '#fbbf24',
    statement: '#60c8fa', data_division: '#0097ab', procedure_division: '#009ddc',
  };
  const color = kindColors[node.kind] ?? '#7e8c9a';
  const toggle = hasChildren ? `▶` : '·';
  const childrenHtml = hasChildren
    ? `<div id="ast-c-${node.uuid}" style="display:none;">${node.children!.map(c => renderASTNode(c, depth + 1)).join('')}</div>`
    : '';
  return `
    <div style="margin-left:${indent}px;">
      <div style="display:flex;align-items:center;gap:6px;padding:3px 0;cursor:${hasChildren ? 'pointer' : 'default'};"
           onclick="toggleASTNode('${node.uuid}')">
        <span style="color:var(--muted);font-size:11px;width:12px;text-align:center;" id="ast-toggle-${node.uuid}">${toggle}</span>
        <span style="color:${color};font-weight:600;font-size:12px;">${node.kind}</span>
        ${node.name ? `<span style="color:var(--text);font-size:12px;">${node.name}</span>` : ''}
        ${node.start_line ? `<span style="color:var(--muted);font-size:11px;">L${node.start_line}${node.end_line && node.end_line !== node.start_line ? '–' + node.end_line : ''}</span>` : ''}
        ${hasChildren ? `<span style="color:var(--muted);font-size:11px;">(${node.children!.length})</span>` : ''}
      </div>
      ${childrenHtml}
    </div>`;
}

function toggleASTNode(uuid: string): void {
  const container = document.getElementById(`ast-c-${uuid}`);
  const toggle = document.getElementById(`ast-toggle-${uuid}`);
  if (!container) return;
  const open = container.style.display !== 'none';
  container.style.display = open ? 'none' : '';
  if (toggle) toggle.textContent = open ? '▶' : '▼';
}

// CFG Visualization
async function loadCFG(prog: string): Promise<void> {
  const container = $<HTMLElement>('cfg-container');
  if (!container) return;
  container.innerHTML = '<div style="color:var(--muted);text-align:center;padding:40px;">Loading CFG…</div>';
  try {
    const data = await apiFetch<{ mermaid: string; nodes: any[]; edges: any[] }>(`/programs/${encodeURIComponent(prog)}/cfg`);
    if (!data.mermaid) {
      container.innerHTML = '<div style="color:var(--muted);text-align:center;padding:40px;">No CFG data. Run the pipeline first.</div>';
      return;
    }
    container.innerHTML = '';
    const id = 'cfg-mmd-' + Date.now();
    const { svg } = await mermaid.render(id, data.mermaid);
    container.innerHTML = svg;
    const svgEl = container.querySelector('svg');
    if (svgEl) (svgEl as HTMLElement).style.maxWidth = '100%';
    const info = document.createElement('div');
    info.style.cssText = 'font-size:11px;color:var(--muted);margin-top:8px;';
    info.textContent = `${data.nodes?.length ?? 0} nodes · ${data.edges?.length ?? 0} edges`;
    container.appendChild(info);
  } catch(e) {
    if (!isAbort(e)) container.innerHTML = `<div style="color:#f87171;padding:20px;">Error: ${(e as Error).message}</div>`;
  }
}

// Symbol Table
async function loadSymbolTable(prog: string): Promise<void> {
  const container = $<HTMLElement>('symbols-container');
  if (!container) return;
  container.innerHTML = '<div style="color:var(--muted);text-align:center;padding:40px;">Loading symbol table…</div>';
  try {
    const data = await apiFetch<{ items: any[] }>(`/programs/${encodeURIComponent(prog)}/symbol-table`);
    const items = data.items ?? [];
    if (!items.length) {
      container.innerHTML = '<div style="color:var(--muted);text-align:center;padding:40px;">No symbol table data. Run the pipeline first.</div>';
      return;
    }
    container.innerHTML = `
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">
        <div style="font-weight:600;font-size:14px;">Data Dictionary — ${prog}</div>
        <input type="text" placeholder="Filter…" style="flex:1;max-width:280px;"
          oninput="filterSymbolTable(this.value)" id="sym-filter" />
        <span style="font-size:12px;color:var(--muted);">${items.length} items</span>
      </div>
      <div style="overflow:auto;max-height:520px;">
        <table id="sym-table">
          <thead><tr>
            <th>Name</th><th>Level</th><th>PIC</th><th>Usage</th>
            <th>Canonical Type</th><th>Precision</th><th>Scale</th><th>Scope</th>
          </tr></thead>
          <tbody id="sym-tbody">
            ${items.map(r => renderSymbolRow(r)).join('')}
          </tbody>
        </table>
      </div>`;
    (window as any)._symItems = items;
  } catch(e) {
    if (!isAbort(e)) container.innerHTML = `<div style="color:#f87171;padding:20px;">Error: ${(e as Error).message}</div>`;
  }
}

function renderSymbolRow(r: any): string {
  const indent = Math.max(0, (parseInt(r.level || '1') - 1) * 14);
  const conds = (r.conditions_88 ?? []).map((c: any) =>
    `<tr style="background:rgba(0,110,116,.04);">
      <td style="padding-left:${indent + 28}px;font-size:11px;color:#4ade80;">88 ${c.name}</td>
      <td><span class="badge badge-gray">88</span></td>
      <td colspan="4" style="font-size:11px;color:var(--muted);">VALUE ${c.value_raw||''}</td>
      <td></td><td></td>
    </tr>`
  ).join('');
  return `<tr>
    <td style="font-weight:500;padding-left:${indent}px;">${r.name ?? ''}</td>
    <td><span class="badge badge-gray">${r.level ?? ''}</span></td>
    <td><code style="font-size:11px;color:var(--ust-sky);">${r.pic ?? ''}</code></td>
    <td>${r.usage ?? 'DISPLAY'}</td>
    <td><span class="badge ${r.canonical_kind === 'decimal' ? 'badge-orange' : r.canonical_kind === 'alpha' ? 'badge-sky' : 'badge-gray'}">${r.canonical_kind ?? ''}</span></td>
    <td>${r.precision ?? ''}</td>
    <td>${r.scale ?? ''}</td>
    <td style="font-size:11px;color:var(--muted);">${r.scope ?? ''}</td>
  </tr>${conds}`;
}

function filterSymbolTable(q: string): void {
  const items: any[] = (window as any)._symItems ?? [];
  const tbody = $<HTMLElement>('sym-tbody');
  if (!tbody) return;
  const lower = q.toLowerCase();
  tbody.innerHTML = items
    .filter(r => (r.name ?? '').toLowerCase().includes(lower) || (r.pic ?? '').toLowerCase().includes(lower) || (r.scope ?? '').toLowerCase().includes(lower))
    .map(r => renderSymbolRow(r))
    .join('');
}

// Complexity Metrics
async function loadComplexity(prog: string): Promise<void> {
  const container = $<HTMLElement>('complexity-container');
  if (!container) return;
  container.innerHTML = '<div style="color:var(--muted);text-align:center;padding:40px;">Loading complexity…</div>';
  try {
    const data = await apiFetch<{ paragraphs: { name: string; cyclomatic: number; loc: number }[] }>(`/programs/${encodeURIComponent(prog)}/complexity`);
    const paras = data.paragraphs ?? [];
    if (!paras.length) {
      container.innerHTML = '<div style="color:var(--muted);text-align:center;padding:40px;">No complexity data. Run the pipeline first.</div>';
      return;
    }
    const sorted = [...paras].sort((a, b) => b.cyclomatic - a.cyclomatic).slice(0, 20);
    container.innerHTML = `
      <div style="font-weight:600;margin-bottom:16px;font-size:14px;">Cyclomatic Complexity per Paragraph — ${prog}</div>
      <canvas id="complexity-chart-canvas" height="350"></canvas>
      <div style="margin-top:20px;overflow:auto;max-height:300px;">
        <table><thead><tr><th>Paragraph</th><th>Cyclomatic</th><th>LoC</th><th>Risk</th></tr></thead>
        <tbody>${sorted.map(p => `
          <tr>
            <td style="font-weight:500;color:var(--ust-sky);">${p.name}</td>
            <td><span class="badge ${p.cyclomatic > 10 ? 'badge-red' : p.cyclomatic > 5 ? 'badge-amber' : 'badge-green'}">${p.cyclomatic}</span></td>
            <td>${p.loc}</td>
            <td><span class="sev-${p.cyclomatic > 10 ? 'HIGH' : p.cyclomatic > 5 ? 'MEDIUM' : 'LOW'}" style="font-weight:700;">${p.cyclomatic > 10 ? 'HIGH' : p.cyclomatic > 5 ? 'MEDIUM' : 'LOW'}</span></td>
          </tr>`).join('')}
        </tbody></table>
      </div>`;
    const ctx = ($<HTMLCanvasElement>('complexity-chart-canvas'))?.getContext('2d');
    if (ctx) {
      if (complexityChart) complexityChart.destroy();
      complexityChart = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: sorted.map(p => p.name),
          datasets: [{
            label: 'Cyclomatic Complexity',
            data: sorted.map(p => p.cyclomatic),
            backgroundColor: sorted.map(p => p.cyclomatic > 10 ? '#f87171' : p.cyclomatic > 5 ? '#fbbf24' : '#4ade80'),
            borderRadius: 4, borderWidth: 0,
          }],
        },
        options: {
          indexAxis: 'y',
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: '#7e8c9a' }, grid: { color: '#2b333f' } },
            y: { ticks: { color: '#7e8c9a', font: { size: 11 } }, grid: { display: false } },
          },
        },
      });
    }
  } catch(e) {
    if (!isAbort(e)) container.innerHTML = `<div style="color:#f87171;padding:20px;">Error: ${(e as Error).message}</div>`;
  }
}

// Source Code Viewer
async function loadSourceCode(prog: string): Promise<void> {
  const container = $<HTMLElement>('source-container');
  if (!container) return;
  container.innerHTML = '<div style="color:var(--muted);text-align:center;padding:40px;">Loading source…</div>';
  try {
    const data = await apiFetch<{ content: string; line_count: number; source_file: string }>(
      `/programs/${encodeURIComponent(prog)}/source`
    );
    if (!data.content) {
      container.innerHTML = '<div style="color:var(--muted);text-align:center;padding:40px;">Source file not found.</div>';
      return;
    }
    container.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
        <div>
          <span style="font-weight:600;font-size:14px;">${prog}.cbl</span>
          <span style="font-size:12px;color:var(--muted);margin-left:12px;">${data.line_count} lines · ${data.source_file.split('/').slice(-3).join('/')}</span>
        </div>
        <button class="btn btn-secondary" style="font-size:12px;padding:5px 10px;"
          onclick="navigator.clipboard.writeText(document.getElementById('source-code-pre')?.textContent||'').then(()=>showToast('Copied!'))">
          Copy source
        </button>
      </div>
      <pre id="source-code-pre" style="max-height:580px;overflow:auto;border-radius:8px;margin:0;">
        <code class="language-cobol" style="font-size:11.5px;line-height:1.6;">${escapeHtml(data.content)}</code>
      </pre>`;
    const codeEl = container.querySelector('code');
    if (codeEl) hljs.highlightElement(codeEl as HTMLElement);
  } catch(e) {
    if (!isAbort(e)) container.innerHTML = `<div style="color:#f87171;padding:20px;">Error: ${(e as Error).message}</div>`;
  }
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Layer Explorer ────────────────────────────────────────────────────────────

interface LayerSummary {
  layer1: { programs: number; paragraphs: number; statements: number };
  layer2: { data_items: number; conditions_88: number; copybook_refs: number };
  layer3: { cfg_edges: number; branch_edges: number; perform_edges: number; fallthru_edges: number; def_use_entries: number; def_use_writes: number };
  layer4: { call_edges: number; resolved: number; resolved_pct: number; file_io: number; tx_flow: number; jcl_bindings: number };
  layer5: { business_rules: number; if_rules: number; evaluate_rules: number; arith_specs: number };
  layer6: { bms_maps: number; csd_entries: number };
  layer7: { coverage_pct: number; ok_files: number; total_files: number; risk_high: number; risk_medium: number; risk_low: number };
}

async function loadLayersPage(): Promise<void> {
  const loading = document.getElementById('lx-loading');
  const content = document.getElementById('lx-content');
  if (!loading || !content) return;

  loading.style.display = '';
  content.style.display = 'none';

  try {
    const d = await apiFetch<LayerSummary>('/layers/summary');

    const set = (id: string, val: number | string) => {
      const el = document.getElementById(id);
      if (el) el.textContent = String(val);
    };

    // L1
    set('lx-l1-programs',   d.layer1.programs);
    set('lx-l1-paragraphs', d.layer1.paragraphs);
    set('lx-l1-statements', d.layer1.statements);

    // L2
    set('lx-l2-items',      d.layer2.data_items);
    set('lx-l2-cond88',     d.layer2.conditions_88);
    set('lx-l2-copybooks',  d.layer2.copybook_refs);

    // L3
    set('lx-l3-cfg',    d.layer3.cfg_edges);
    set('lx-l3-branch', d.layer3.branch_edges);
    set('lx-l3-du',     d.layer3.def_use_entries);

    const breakdown = document.getElementById('lx-l3-breakdown');
    if (breakdown) {
      const types = [
        { label: 'PERFORM',     val: d.layer3.perform_edges,  color: '#5ecdd1' },
        { label: 'BRANCH',      val: d.layer3.branch_edges,   color: '#34d399' },
        { label: 'FALLTHROUGH', val: d.layer3.fallthru_edges, color: '#fbbf24' },
      ];
      breakdown.innerHTML = types.map(t =>
        `<span class="badge" style="background:#1c2a2c;color:${t.color};">${t.label} <strong>${t.val.toLocaleString()}</strong></span>`
      ).join('');
    }

    // L4
    set('lx-l4-calls',  d.layer4.call_edges);
    set('lx-l4-fileio', d.layer4.file_io);
    set('lx-l4-tx',     d.layer4.tx_flow);
    set('lx-l4-jcl',    d.layer4.jcl_bindings);
    const resolvedEl = document.getElementById('lx-l4-resolved');
    if (resolvedEl) resolvedEl.textContent = `${d.layer4.resolved_pct}% resolved`;

    // L5
    set('lx-l5-total', d.layer5.business_rules);
    set('lx-l5-if',    d.layer5.if_rules);
    set('lx-l5-eval',  d.layer5.evaluate_rules);
    set('lx-l5-arith', d.layer5.arith_specs);

    // L6
    set('lx-l6-bms', d.layer6.bms_maps);
    set('lx-l6-csd', d.layer6.csd_entries);

    // L7
    set('lx-l7-cov',  `${d.layer7.coverage_pct}%`);
    set('lx-l7-high', d.layer7.risk_high);
    set('lx-l7-med',  d.layer7.risk_medium);
    set('lx-l7-low',  d.layer7.risk_low);

    loading.style.display = 'none';
    content.style.display = '';
  } catch (e) {
    if (!isAbort(e)) {
      if (loading) loading.textContent = 'Run the pipeline first to populate layer artifacts.';
    }
  }
}

function scrollToLayer(id: string): void {
  const el = document.getElementById(`lx-${id}`);
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── Layer Explorer Drill-Down ─────────────────────────────────────────────────

let _lxActiveLayer = 0;
let _lxActiveProgram = '';

async function lxBrowse(layer: number, filter: string = ''): Promise<void> {
  _lxActiveLayer = layer;
  const panel = $<HTMLElement>('lx-drilldown-panel');
  const title = $<HTMLElement>('lx-drilldown-title');
  const body  = $<HTMLElement>('lx-drilldown-body');
  if (!panel || !title || !body) return;
  panel.style.display = '';
  body.innerHTML = '<div style="color:var(--muted);text-align:center;padding:40px;">Loading…</div>';

  const layerTitles: Record<number, string> = {
    1: 'Layer 1 — Programs & Paragraphs',
    2: 'Layer 2 — Data Items',
    3: 'Layer 3 — CFG Edges',
    4: 'Layer 4 — Call Graph',
    5: 'Layer 5 — Business Rules',
    6: 'Layer 6 — BMS Maps',
    7: 'Layer 7 — Risk Register',
  };
  if (title) title.textContent = layerTitles[layer] ?? `Layer ${layer}`;

  try {
    switch (layer) {
      case 1: {
        const rows = await apiFetch<any[]>('/layers/1/programs?limit=200');
        body.innerHTML = `
          <div style="overflow:auto;max-height:500px;">
            <table><thead><tr><th>Program</th><th>Source File</th><th>Paragraphs</th><th>Statements</th><th></th></tr></thead>
            <tbody>${(rows ?? []).map(r => `
              <tr>
                <td style="font-weight:600;color:#5ecdd1;">${r.name}</td>
                <td style="font-size:11px;color:var(--muted);">${(r.source_file||'').split('/').pop()}</td>
                <td><span class="badge badge-sky">${r.para_count ?? 0}</span></td>
                <td>${r.stmt_count ?? 0}</td>
                <td><button class="btn btn-secondary" style="font-size:11px;padding:3px 8px;"
                    onclick="navigate('visualizations');setTimeout(()=>{const s=document.getElementById('viz-program');if(s)s.value='${r.name}';switchVizTab('source');loadSourceCode('${r.name}');},200)">
                  View Source</button></td>
              </tr>`).join('')}
            </tbody></table>
          </div>`;
        break;
      }
      case 2: {
        const progParam = _lxActiveProgram ? `?program=${encodeURIComponent(_lxActiveProgram)}&limit=200` : '?limit=200';
        const rows = await apiFetch<any[]>(`/layers/2/data-items${progParam}`);
        body.innerHTML = `
          <div style="overflow:auto;max-height:500px;">
            <table><thead><tr><th>Name</th><th>Level</th><th>PIC</th><th>Type</th><th>Precision</th><th>Program</th></tr></thead>
            <tbody>${(rows ?? []).map(r => `
              <tr>
                <td style="font-weight:500;padding-left:${Math.max(0,(parseInt(r.level||0)-1)*8)}px">${r.name}</td>
                <td><span class="badge badge-gray">${r.level}</span></td>
                <td><code style="font-size:11px;color:var(--ust-sky);">${r.pic||''}</code></td>
                <td><span class="badge ${r.canonical_kind==='decimal'?'badge-orange':r.canonical_kind==='alpha'?'badge-sky':'badge-gray'}">${r.canonical_kind||''}</span></td>
                <td>${r.precision||''}</td>
                <td style="font-size:11px;color:var(--muted);">${r.program_name||''}</td>
              </tr>`).join('')}
            </tbody></table>
          </div>`;
        break;
      }
      case 3: {
        const progParam = _lxActiveProgram ? `?program=${encodeURIComponent(_lxActiveProgram)}&limit=300` : '?limit=300';
        const rows = await apiFetch<any[]>(`/layers/3/cfg-edges${progParam}`);
        const typeCounts: Record<string,number> = {};
        (rows ?? []).forEach(r => { typeCounts[r.edge_type] = (typeCounts[r.edge_type]||0) + 1; });
        body.innerHTML = `
          <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;">
            ${Object.entries(typeCounts).map(([t,c]) => `<span class="badge badge-sky">${t} <strong>${c}</strong></span>`).join('')}
          </div>
          <div style="overflow:auto;max-height:460px;">
            <table><thead><tr><th>From Paragraph</th><th>Edge Type</th><th>To Paragraph</th><th>Program</th></tr></thead>
            <tbody>${(rows ?? []).map(r => `
              <tr>
                <td style="font-weight:500;color:var(--ust-sky);">${r.from_para}</td>
                <td><span class="badge ${r.edge_type?.includes('BRANCH')?'badge-green':r.edge_type==='PERFORM'?'badge-sky':r.edge_type==='FALLTHROUGH'?'badge-gray':'badge-amber'}">${r.edge_type}</span></td>
                <td style="font-weight:500;color:var(--ust-sky);">${r.to_para}</td>
                <td style="font-size:11px;color:var(--muted);">${r.program_name}</td>
              </tr>`).join('')}
            </tbody></table>
          </div>`;
        break;
      }
      case 4: {
        const rows = await apiFetch<any[]>('/layers/4/call-graph?limit=200');
        body.innerHTML = `
          <div style="overflow:auto;max-height:500px;">
            <table><thead><tr><th>Caller</th><th>Callee</th><th>Type</th><th>Resolved</th></tr></thead>
            <tbody>${(rows ?? []).map(r => `
              <tr>
                <td style="font-weight:500;color:var(--ust-sky);">${r.caller_name}</td>
                <td style="font-weight:500;">${r.callee_name}</td>
                <td><span class="badge badge-sky">${r.call_type}</span></td>
                <td>${r.is_resolved ? '<span class="badge badge-green">✓ resolved</span>' : '<span class="badge badge-amber">unresolved</span>'}</td>
              </tr>`).join('')}
            </tbody></table>
          </div>`;
        break;
      }
      case 5: {
        const progParam = _lxActiveProgram ? `?program=${encodeURIComponent(_lxActiveProgram)}&limit=200` : '?limit=200';
        const rows = await apiFetch<any[]>(`/layers/5/business-rules${progParam}`);
        body.innerHTML = `
          <div style="overflow:auto;max-height:500px;">
            <table><thead><tr><th>Program</th><th>Line</th><th>Kind</th><th>Predicate</th><th>Then</th><th>Else</th></tr></thead>
            <tbody>${(rows ?? []).map(r => `
              <tr>
                <td style="font-size:11px;color:var(--muted);">${r.program_name}</td>
                <td>${r.line||''}</td>
                <td><span class="badge badge-orange">${r.kind}</span></td>
                <td style="font-size:11px;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${(r.predicate_raw||'').replace(/"/g,'&quot;')}">${(r.predicate_raw||'').slice(0,60)}</td>
                <td style="font-size:11px;color:var(--muted);max-width:150px;overflow:hidden;text-overflow:ellipsis;">${(r.then_summary||'').slice(0,40)}</td>
                <td style="font-size:11px;color:var(--muted);max-width:150px;overflow:hidden;text-overflow:ellipsis;">${(r.else_summary||'').slice(0,40)}</td>
              </tr>`).join('')}
            </tbody></table>
          </div>`;
        break;
      }
      case 6: {
        const maps = await apiFetch<any[]>('/layers/6/bms-maps?limit=200');
        const csd  = await apiFetch<any[]>('/layers/6/csd?limit=100');
        body.innerHTML = `
          <div style="font-weight:600;margin-bottom:8px;font-size:13px;">BMS Screen Maps (${maps.length} fields)</div>
          <div style="overflow:auto;max-height:280px;margin-bottom:16px;">
            <table><thead><tr><th>Map</th><th>Mapset</th><th>Field</th><th>Row</th><th>Col</th><th>Length</th><th>Attrs</th></tr></thead>
            <tbody>${(maps ?? []).map(r => `
              <tr>
                <td style="font-weight:500;color:var(--ust-sky);">${r.map_name}</td>
                <td style="font-size:11px;color:var(--muted);">${r.mapset_name}</td>
                <td>${r.field_name}</td>
                <td>${r.position_row}</td><td>${r.position_col}</td><td>${r.length}</td>
                <td style="font-size:11px;color:var(--muted);">${r.attributes||''}</td>
              </tr>`).join('')}
            </tbody></table>
          </div>
          <div style="font-weight:600;margin-bottom:8px;font-size:13px;">CSD Catalog (${csd.length} entries)</div>
          <div style="overflow:auto;max-height:200px;">
            <table><thead><tr><th>Name</th><th>Type</th><th>Program</th><th>Transaction</th></tr></thead>
            <tbody>${(csd ?? []).map(r => `
              <tr>
                <td style="font-weight:500;">${r.name||''}</td>
                <td><span class="badge badge-sky">${r.resource_type||''}</span></td>
                <td style="font-size:11px;">${r.program_name||''}</td>
                <td style="font-size:11px;">${r.transaction_id||''}</td>
              </tr>`).join('')}
            </tbody></table>
          </div>`;
        break;
      }
      case 7: {
        const rows = await apiFetch<any[]>('/layers/7/risks?limit=500');
        body.innerHTML = `
          <div style="overflow:auto;max-height:500px;">
            <table><thead><tr><th>Program</th><th>Kind</th><th>Severity</th><th>Note</th><th>Line</th></tr></thead>
            <tbody>${(rows ?? []).map(r => `
              <tr>
                <td style="font-weight:500;color:var(--ust-sky);">${r.program_name||'—'}</td>
                <td><span class="badge badge-orange">${r.kind}</span></td>
                <td><span class="sev-${r.severity}" style="font-weight:700;">${r.severity}</span></td>
                <td style="font-size:11px;color:var(--muted);">${r.note||''}</td>
                <td>${r.line||''}</td>
              </tr>`).join('')}
            </tbody></table>
          </div>`;
        break;
      }
    }
  } catch(e) {
    if (!isAbort(e)) body.innerHTML = `<div style="color:#f87171;padding:20px;">Error: ${(e as Error).message}</div>`;
  }
}

function lxClose(): void {
  const panel = $<HTMLElement>('lx-drilldown-panel');
  if (panel) panel.style.display = 'none';
}

function zoomDiagram(factor: number): void {
  const wrap = document.querySelector<HTMLElement>('.mermaid-wrap');
  if (!wrap) return;
  const svg = wrap.querySelector<SVGElement>('svg');
  if (!svg) return;
  if (factor === 1) {
    svg.style.transform = '';
    svg.style.transformOrigin = 'top left';
  } else {
    const current = parseFloat(svg.style.transform?.replace('scale(', '').replace(')', '') || '1');
    const next = Math.max(0.3, Math.min(current * factor, 5));
    svg.style.transform = `scale(${next})`;
    svg.style.transformOrigin = 'top left';
    svg.style.display = 'block';
    wrap.style.overflow = 'auto';
    wrap.style.minHeight = '400px';
  }
}

// ── Platform Recommender ─────────────────────────────────────────────────────

const HYPERSCALER_COLORS: Record<string, string> = {
  aws: '#ff9900', azure: '#0078d4', gcp: '#4285f4', 'on-prem': '#5ecdd1',
};

async function loadPlatformPage(): Promise<void> {
  // Highlight default AWS radio
  const defaultLabel = $<HTMLElement>('hs-aws-label');
  if (defaultLabel) defaultLabel.style.borderColor = '#ff9900';
  // No program dropdown — platform recommender always uses full portfolio scope
}

function onHyperscalerChange(radio: HTMLInputElement): void {
  document.querySelectorAll<HTMLElement>('[id^="hs-"]').forEach(el => {
    el.style.borderColor = 'var(--border)';
  });
  const label = document.getElementById(`hs-${radio.value}-label`);
  if (label) label.style.borderColor = HYPERSCALER_COLORS[radio.value] ?? 'var(--ust-teal)';
}

function onPlatProgramChange(): void {
  const prog = ($<HTMLSelectElement>('plat-program'))?.value;
  const scopeEl = $<HTMLSelectElement>('plat-scope');
  if (scopeEl) scopeEl.value = prog ? 'program' : 'portfolio';
}

async function runPlatformRecommender(): Promise<void> {
  const btn = $<HTMLButtonElement>('plat-run-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Generating…'; }
  $<HTMLElement>('plat-placeholder')!.style.display = 'none';
  $<HTMLElement>('plat-result')!.style.display = 'none';
  $<HTMLElement>('plat-loading')!.style.display = '';

  const hyperscaler = (document.querySelector<HTMLInputElement>('input[name="hyperscaler"]:checked'))?.value ?? 'aws';
  const runtime = ($<HTMLSelectElement>('plat-runtime'))?.value ?? 'microservices';
  const data_strategy = ($<HTMLSelectElement>('plat-data'))?.value ?? 'managed-sql';
  const priority = ($<HTMLSelectElement>('plat-priority'))?.value ?? 'speed';
  const scope = 'portfolio'; // always portfolio — covers all programs, JCL, copybooks, CICS

  const msgs: string[] = [];
  const loadingMsg = $<HTMLElement>('plat-loading-msg');

  try {
    const res = await fetch('/platform/recommend', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hyperscaler, runtime, data_strategy, priority, scope }),
    });
    if (!res.body) throw new Error('No response body');
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    const stages = ['Analysing artifact store…', 'Building context slice…', 'Mapping to cloud services…', 'Generating recommendation…'];
    let stageIdx = 0;
    const stageTimer = setInterval(() => {
      if (loadingMsg && stageIdx < stages.length) loadingMsg.textContent = stages[stageIdx++];
    }, 1200);
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop() ?? '';
      for (const part of parts) {
        if (!part.startsWith('data:')) continue;
        try {
          const ev = JSON.parse(part.slice(5));
          if (ev.kind === 'result') msgs.push(ev.msg);
          else if (ev.kind === 'error') msgs.push(`**Error:** ${ev.msg}`);
        } catch { /* skip */ }
      }
    }
    clearInterval(stageTimer);
  } catch(e) {
    msgs.push(`**Error:** ${(e as Error).message}`);
  }

  const resultEl = $<HTMLElement>('plat-result')!;
  $<HTMLElement>('plat-loading')!.style.display = 'none';
  resultEl.style.display = '';

  const hs = hyperscaler;
  const hsColor = HYPERSCALER_COLORS[hs] ?? '#5ecdd1';
  const markdown = msgs.join('\n\n') || 'No recommendation generated.';
  resultEl.innerHTML = `
    <div class="card" style="border-top:3px solid ${hsColor};">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:8px;">
        <div style="display:flex;align-items:center;gap:10px;">
          <div style="width:4px;height:24px;background:${hsColor};border-radius:2px;"></div>
          <div style="font-weight:700;font-size:15px;">Architecture Recommendation — ${hs.toUpperCase()}</div>
        </div>
        <div style="display:flex;gap:8px;">
          <button class="btn btn-secondary" style="font-size:12px;" onclick="exportPlatformMd()">⬇ .md</button>
          <button class="btn btn-secondary" style="font-size:12px;" onclick="exportPlatformPdf()">⬇ PDF</button>
          <button class="btn btn-secondary" style="font-size:12px;" onclick="runPlatformRecommender()">↺ Regenerate</button>
        </div>
      </div>
      <div id="plat-markdown-body" style="font-size:13px;line-height:1.8;color:var(--text);max-height:680px;overflow:auto;"></div>
    </div>`;

  const mdBody = $<HTMLElement>('plat-markdown-body')!;
  mdBody.innerHTML = _renderMarkdown(markdown);

  if (btn) { btn.disabled = false; btn.textContent = 'Generate Recommendation'; }
  (btn as any).textContent = 'Generate Recommendation';
  if (btn) btn.disabled = false;
  const btnEl = $<HTMLButtonElement>('plat-run-btn');
  if (btnEl) { btnEl.disabled = false; btnEl.innerHTML = '<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 1v4M12 19v4"/></svg> Generate Recommendation'; }
}

function _renderMarkdown(md: string): string {
  return md
    .replace(/^## (.+)$/gm, '<h2 style="color:#5ecdd1;font-size:14px;font-weight:700;margin:20px 0 8px;border-bottom:1px solid var(--border);padding-bottom:6px;">$1</h2>')
    .replace(/^### (.+)$/gm, '<h3 style="color:#60c8fa;font-size:13px;font-weight:600;margin:14px 0 6px;">$1</h3>')
    .replace(/\*\*(.+?)\*\*/g, '<strong style="color:var(--text);">$1</strong>')
    .replace(/`([^`]+)`/g, '<code style="background:var(--surface2);padding:2px 5px;border-radius:3px;font-size:12px;">$1</code>')
    .replace(/^- (.+)$/gm, '<div style="display:flex;gap:8px;margin:4px 0;"><span style="color:#5ecdd1;flex-shrink:0;">•</span><span>$1</span></div>')
    .replace(/^(\d+)\. (.+)$/gm, '<div style="display:flex;gap:8px;margin:4px 0;"><span style="color:#fbbf24;min-width:20px;">$1.</span><span>$2</span></div>')
    .replace(/\n\n/g, '<br style="line-height:2;"><br>')
    .replace(/\n/g, '<br>');
}

async function exportPlatformMd(): Promise<void> {
  const el = $<HTMLElement>('plat-markdown-body');
  if (!el) return;
  const text = el.innerText;
  const blob = new Blob([text], { type: 'text/markdown' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'platform-recommendation.md';
  a.click();
}

async function exportPlatformPdf(): Promise<void> {
  const el = $<HTMLElement>('plat-markdown-body');
  if (!el) return;
  try {
    const res = await fetch('/specs/export/pdf', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: el.innerText, title: 'Platform Architecture Recommendation' }),
    });
    if (!res.ok) throw new Error(`PDF export failed: ${res.status}`);
    const blob = await res.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'platform-recommendation.pdf';
    a.click();
  } catch(e) {
    alert(`PDF export error: ${(e as Error).message}`);
  }
}

// ── Expose to window for onclick handlers ─────────────────────────────────────
Object.assign(window as any, {
  navigate,
  checkHealth,
  loadDashboard,
  loadPrograms,
  openProgram,
  closeDetail,
  switchTab,
  loadDiagram,
  copyDiagramSource,
  loadProgramDropdowns,
  onSpecProgramChange,
  onSpecScopeChange,
  generateSpecPersonas,
  toggleAllPersonas,
  switchPersonaTab,
  exportSpecMd,
  exportSpecPdf,
  copySpec,
  generateModernizationReport,
  viewModernizationReport,
  copyModernizationReport,
  loadTransformPage,
  startTransform,
  runTransformStep,
  approveTransformStep,
  rejectTransformStep,
  resetTransform,
  downloadTransformOutput,
  runPipeline,
  runSmoke,
  cancelPipeline,
  switchSourceTab,
  cloneGithub,
  uploadZip,
  handleZipDrop,
  loadPlatformPage,
  onHyperscalerChange,
  onPlatProgramChange,
  runPlatformRecommender,
  exportPlatformMd,
  exportPlatformPdf,
  filterCovTable,
  filterRiskTable,
  loadVizProgramDropdown,
  loadViz,
  switchVizTab,
  toggleASTNode,
  filterSymbolTable,
  onProviderChange,
  saveSettings,
  loadLayersPage,
  scrollToLayer,
  loadSourceCode,
  lxBrowse,
  lxClose,
  zoomDiagram,
});

// ── Init ──────────────────────────────────────────────────────────────────────
void checkHealth();
setInterval(() => { void checkHealth(); }, 30_000);
void loadDashboard();
