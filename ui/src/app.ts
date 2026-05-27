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
  active_run?: { id: string; started_at: string; completed_at: string; corpus: string } | null;
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
// Pipeline runs independently of navigation — dedicated controller and log buffer
let _pipelineCtrl: AbortController | null = null;
const _pipelineLogBuffer: Array<{ kind: string; msg: string; ts?: number }> = [];
let _kgSelectedNode: { id: string; label: string; kind: string; title: string } | null = null;

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
    copybooks: 'Copybook Browser',
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
  if (page === 'pipeline')       { _replayPipelineLog(); void loadRunHistory(); }
  if (page === 'copybooks')      void loadCopybooks();
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

    // Active run indicator
    const activeRunEl = $<HTMLElement>('active-run-info');
    if (activeRunEl) {
      if (s.active_run) {
        const ar = s.active_run;
        const ts = ar.completed_at ? new Date(ar.completed_at * 1000).toLocaleString() : '—';
        const corpus = ar.corpus ? ar.corpus.split('/').slice(-3).join('/') : '—';
        activeRunEl.innerHTML = `<span style="color:#4ade80;font-size:11px;">● Active dataset:</span>
          <span style="font-size:11px;color:var(--fg);margin-left:6px;">${corpus}</span>
          <span style="font-size:11px;color:var(--muted);margin-left:10px;">Last run: ${ts}</span>`;
      } else {
        activeRunEl.innerHTML = `<span style="color:var(--muted);font-size:11px;">No pipeline run yet — click Run Pipeline to ingest a corpus.</span>`;
      }
    }

    // Render data sections immediately (before charts so a chart error doesn't block them)
    renderBirdsEyeView(s);
    renderKnowledgeGraph(s);

    // Charts — isolated so failures don't affect the data cards above
    try {
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
    } catch(chartErr) { console.warn('Chart render error:', chartErr); }
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
  // Auto-init graph when data is present
  if ((s as any).programs > 0) void initKnowledgeGraph();
}

async function initKnowledgeGraph(): Promise<void> {
  const container = $<HTMLElement>('kg-network');
  const loadingEl = $<HTMLElement>('kg-loading');
  if (!container) return;
  if (loadingEl) loadingEl.style.display = 'flex';

  try {
    const data = await apiFetch<{ nodes: any[]; edges: any[]; stats: any }>('/knowledge-graph');
    const countEl = $<HTMLElement>('kg-node-count');
    if (countEl) countEl.textContent = `${data.nodes.length} nodes · ${data.edges.length} edges`;
    if (loadingEl) loadingEl.style.display = 'none';

    const vis = (window as any).vis;
    if (!vis) {
      container.innerHTML = '<div style="padding:40px;text-align:center;color:var(--muted);">vis.js not loaded — check network connection.</div>';
      return;
    }
    if (data.nodes.length === 0) {
      container.innerHTML = '<div style="padding:40px;text-align:center;color:var(--muted);">Run the pipeline first to populate the knowledge graph.</div>';
      return;
    }

    const groupColors: Record<string, string> = { program: '#5ecdd1', copybook: '#60c8fa', jcl: '#fbbf24' };
    // font color inside each shape: dark on light fills (program/copybook/jcl), white on dark defaults
    const groupFontColors: Record<string, string> = { program: '#001a28', copybook: '#001a28', jcl: '#001a28' };
    const visNodes = new vis.DataSet(data.nodes.map((n: any) => ({
      id: n.id,
      label: n.label.length > 14 ? n.label.slice(0, 14) + '…' : n.label,
      title: n.title || n.label,
      group: n.group,
      color: { background: groupColors[n.group] ?? '#7e8c9a', border: groupColors[n.group] ?? '#7e8c9a',
               highlight: { background: '#fff', border: groupColors[n.group] ?? '#5ecdd1' } },
      font: { color: groupFontColors[n.group] ?? '#ffffff', size: 10, bold: true },
      // programs → ellipse, JCL → box (yellow rectangle), copybooks → box (cyan rectangle)
      shape: n.group === 'program' ? 'ellipse' : 'box',
      size: n.group === 'program' ? 18 : 12,
    })));

    const edgeColors: Record<string, string> = {
      call: '#5ecdd1', tx: '#d876d6', nav: '#a78bfa', jcl: '#fbbf24', file: '#34d399', copy: '#60c8fa',
    };
    const visEdges = new vis.DataSet(data.edges.map((e: any, i: number) => ({
      id: i, from: e.from, to: e.to, title: `${e.kind.toUpperCase()}: ${e.label}`,
      arrows: { to: { enabled: true, scaleFactor: 0.6 } },
      dashes: e.kind === 'copy' || e.kind === 'file',
      color: { color: edgeColors[e.kind] ?? '#7e8c9a', highlight: '#ffffff', opacity: 0.85 },
      width: e.kind === 'call' || e.kind === 'tx' ? 2 : 1.2,
      smooth: { type: 'dynamic' },
    })));

    container.innerHTML = '';
    const network = new vis.Network(container, { nodes: visNodes, edges: visEdges }, {
      physics: { enabled: true, solver: 'forceAtlas2Based',
        forceAtlas2Based: { gravitationalConstant: -80, centralGravity: 0.01, springLength: 120, springConstant: 0.08, damping: 0.4 },
        stabilization: { iterations: 200 } },
      interaction: { hover: true, tooltipDelay: 200, navigationButtons: false },
      layout: { improvedLayout: false },
    });
    network.on('click', (params: any) => {
      if (params.nodes.length > 0) {
        const nodeId = params.nodes[0];
        const nodeData = data.nodes.find((n: any) => n.id === nodeId);
        if (nodeData) onKGNodeClick(nodeData);
      } else {
        closeKGPane();
      }
    });
    network.once('stabilizationIterationsDone', () => network.setOptions({ physics: false }));
  } catch(e) {
    if (loadingEl) loadingEl.style.display = 'none';
    if (!isAbort(e) && container) container.innerHTML = `<div style="padding:40px;text-align:center;color:var(--muted);">Graph unavailable: ${(e as Error).message}</div>`;
  }
}

function onKGNodeClick(node: { id: string; label: string; kind: string; title: string }): void {
  _kgSelectedNode = node;
  const pane  = $<HTMLElement>('kg-side-pane');
  const title = $<HTMLElement>('kg-pane-title');
  const meta  = $<HTMLElement>('kg-pane-meta');
  const explain = $<HTMLElement>('kg-pane-explain');
  const btn   = $<HTMLButtonElement>('kg-explain-btn');
  if (pane)    pane.style.width = '320px';
  if (title)   title.textContent = node.label;
  if (meta)    meta.innerHTML = (node.title ?? '').replace(/\n/g, '<br>');
  if (explain) explain.textContent = '';
  if (btn)     btn.textContent = '✦ Explain with AI';
}

function closeKGPane(): void {
  const pane = $<HTMLElement>('kg-side-pane');
  if (pane) pane.style.width = '0';
  _kgSelectedNode = null;
}

async function explainKGNode(): Promise<void> {
  if (!_kgSelectedNode) return;
  const explain = $<HTMLElement>('kg-pane-explain');
  const btn     = $<HTMLButtonElement>('kg-explain-btn');
  if (!explain) return;
  if (btn) { btn.disabled = true; btn.textContent = '⟳ Generating…'; }
  explain.innerHTML = '';

  const { id, label, kind } = _kgSelectedNode;

  if (kind === 'program') {
    try {
      explain.innerHTML = '<em style="color:var(--muted);">⟳ Fetching program context…</em>';
      const prog = await apiFetch<any>(`/programs/${encodeURIComponent(label)}`);
      const uuid = prog?.uuid ?? id;
      explain.innerHTML = '<em style="color:var(--muted);">⟳ Generating explanation…</em>';
      const result = await apiFetch<{ spec: string }>('/generate-spec', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ uuid, scope: 'program' }),
      });
      explain.innerHTML = _renderMarkdown(result?.spec ?? '(no explanation returned)');
    } catch(e) {
      if (!isAbort(e)) {
        explain.innerHTML = _renderMarkdown((_kgSelectedNode.title ?? label) +
          '\n\n⚠ LLM explanation unavailable — configure an API key in Settings.');
      }
    }
  } else if (kind === 'copybook') {
    try {
      explain.innerHTML = '<em style="color:var(--muted);">⟳ Fetching copybook context…</em>';
      const result = await apiFetch<{ spec: string }>('/explain-copybook', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: label }),
      });
      explain.innerHTML = _renderMarkdown(result?.spec ?? '(no explanation returned)');
    } catch(e) {
      if (!isAbort(e)) {
        explain.innerHTML = _renderMarkdown((_kgSelectedNode.title ?? label) +
          '\n\n⚠ LLM explanation unavailable — configure an API key in Settings.');
      }
    }
  } else {
    // JCL nodes: show metadata summary
    explain.innerHTML = _renderMarkdown(_kgSelectedNode.title ?? label);
  }

  if (btn) { btn.disabled = false; btn.textContent = '✦ Explain with AI'; }
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
    renderProgCopybooks(d.copybooks ?? []);
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

function renderProgCopybooks(rows: any[]): void {
  const el = $<HTMLElement>('prog-cpy-tbody');
  if (!el) return;
  const typeColor: Record<string, string> = { COPYBOOK: '#5ecdd1', BMS_COPYBOOK: '#fbbf24', STUB: '#94a3b8' };
  el.innerHTML = (rows ?? []).map((r: any) => {
    const typ = r.source_type ?? 'COPYBOOK';
    const col = typeColor[typ] ?? '#5ecdd1';
    const replParsed = (() => { try { const a = JSON.parse(r.replacing_json ?? '[]'); return Array.isArray(a) && a.length ? `${a.length} pair(s)` : '—'; } catch { return '—'; } })();
    return `<tr>
      <td style="color:#60c8fa;font-weight:600;cursor:pointer;" onclick="navigate('copybooks');setTimeout(()=>openCopybookDetail('${r.copybook_name}'),200)">${r.copybook_name}</td>
      <td><span style="color:${col};font-size:11px;">${typ}</span></td>
      <td>${r.data_item_count ?? '—'}</td>
      <td>${r.line ?? '—'}</td>
      <td style="font-size:11px;color:var(--muted);">${replParsed}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="5" style="color:var(--muted);">No COPY statements</td></tr>';
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
    // Deduplicate by name (server-side GROUP BY handles it, but guard client-side too)
    const seen = new Set<string>();
    programs = (data.items ?? []).filter(p => {
      const k = (p.name ?? '').toUpperCase();
      if (seen.has(k)) return false;
      seen.add(k); return true;
    });
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
  // For paragraph scope, prefer the paragraph dropdown value over spec-uuid
  const paraUuid = scope === 'paragraph'
    ? (($<HTMLSelectElement>('spec-paragraph'))?.value ?? ($<HTMLInputElement>('spec-uuid'))?.value ?? '')
    : (($<HTMLInputElement>('spec-uuid'))?.value ?? '');
  const uuid_ = paraUuid;
  if (!prog && !uuid_) { showToast('Select a program first', 'error'); return; }
  if (scope === 'paragraph' && !uuid_) { showToast('Select a paragraph first', 'error'); return; }

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

async function generateComprehensiveSpec(): Promise<void> {
  const btn = $<HTMLButtonElement>('spec-comp-btn');
  const progressEl = $<HTMLElement>('spec-progress');
  const progressMsg = $<HTMLElement>('spec-progress-msg');
  const progressFill = $<HTMLElement>('spec-progress-fill');
  const tabsEl = $<HTMLElement>('spec-tabs');
  const contentEl = $<HTMLElement>('spec-tab-content');
  const emptyState = $<HTMLElement>('spec-empty-state');

  if (btn) { btn.disabled = true; btn.innerHTML = '⟳ Building portfolio report…'; }
  if (progressEl) progressEl.style.display = '';
  if (progressMsg) progressMsg.textContent = 'Assembling all 7 artifact layers across entire corpus…';
  if (progressFill) progressFill.style.width = '0%';
  if (emptyState) emptyState.style.display = 'none';

  // Navigate to spec page and clear old tabs
  navigate('spec');
  _personaResults = {};
  if (tabsEl) tabsEl.innerHTML = '';
  if (contentEl) contentEl.innerHTML = '';

  const COMP_PERSONA = 'comprehensive';
  addPersonaTab(COMP_PERSONA, '📄 Portfolio Report', 'pending', tabsEl, contentEl);
  switchPersonaTab(COMP_PERSONA);

  let fullMarkdown = '';
  let sectionCount = 0;

  try {
    const resp = await fetch('/generate-spec/comprehensive', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
      signal: sig(),
    });
    if (!resp.ok) throw new Error(await resp.text());

    const reader = resp.body?.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    // 2 static + 7 LLM sections + 1 static appendix = 10
    const TOTAL_SECTIONS = 10;

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
          if (evt.event === 'section_done') {
            sectionCount++;
            fullMarkdown += '\n\n---\n\n' + (evt.content ?? '');
            _personaResults[COMP_PERSONA] = fullMarkdown;
            const pct = Math.round((sectionCount / TOTAL_SECTIONS) * 100);
            if (progressFill) progressFill.style.width = pct + '%';
            if (progressMsg) progressMsg.textContent = `Section ${sectionCount}/${TOTAL_SECTIONS}: ${evt.title ?? ''}`;
            const div = $<HTMLElement>(`persona-content-${COMP_PERSONA}`);
            if (div) {
              div.innerHTML = _renderMarkdown(fullMarkdown);
              try { await (window as any).mermaid?.run({ querySelector: '#persona-content-comprehensive .mermaid' }); } catch {}
            }
          } else if (evt.event === 'all_done') {
            if (progressMsg) progressMsg.textContent = `Complete — ${sectionCount} sections generated`;
            if (progressFill) progressFill.style.width = '100%';
          }
        } catch { /* partial JSON */ }
      }
    }
  } catch(e) {
    if (!isAbort(e)) showToast(`Comprehensive report failed: ${(e as Error).message}`, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = '<svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg> Generate Comprehensive Report';
    }
    if (progressEl) setTimeout(() => { if (progressEl) progressEl.style.display = 'none'; }, 3000);
    const mdBtn  = $<HTMLButtonElement>('spec-export-md');
    const pdfBtn = $<HTMLButtonElement>('spec-export-pdf');
    if (mdBtn)  mdBtn.disabled  = !fullMarkdown;
    if (pdfBtn) pdfBtn.disabled = !fullMarkdown;
    const dot = $<HTMLElement>(`persona-tab-dot-${COMP_PERSONA}`);
    if (dot) dot.style.background = fullMarkdown ? '#4ade80' : '#f87171';
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
    div.style.cssText = 'display:none;font-size:13px;line-height:1.8;color:var(--text);padding:4px 0;overflow-x:auto;max-width:100%;word-break:break-word;overflow-wrap:break-word;';
    div.innerHTML = '<em style="color:var(--muted);">Generating…</em>';
    contentEl.appendChild(div);
  }
}

function updatePersonaTab(persona: string, content: string,
                          tabsEl: HTMLElement|null, contentEl: HTMLElement|null,
                          labels: Record<string,string>): void {
  const dot = $<HTMLElement>(`persona-tab-dot-${persona}`);
  if (dot) dot.style.background = '#4ade80';
  const div = $<HTMLElement>(`persona-content-${persona}`);
  if (div) div.innerHTML = _renderMarkdown(content);
  // Auto-switch to first completed tab
  if (_activePersonaTab === '') switchPersonaTab(persona);
}

function updatePersonaTabError(persona: string, error: string, tabsEl: HTMLElement|null): void {
  const dot = $<HTMLElement>(`persona-tab-dot-${persona}`);
  if (dot) dot.style.background = '#f87171';
  const div = $<HTMLElement>(`persona-content-${persona}`);
  if (div) { div.innerHTML = `<span style="color:#f87171;">Error: ${escapeHtml(error)}</span>`; }
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
  try {
    const s = await apiFetch<any>('/stats');
    const set = (id: string, v: any) => { const el = $<HTMLElement>(id); if (el) el.textContent = String(v ?? '—'); };
    set('tx-port-programs', (s.programs ?? 0).toLocaleString());
    set('tx-port-rules', (s.business_rules ?? 0).toLocaleString());
    set('tx-port-risks-high', 0);
    set('tx-port-jcl', (s.jcl_files ?? 0).toLocaleString());
  } catch { /* DB not ready */ }
  try {
    const cov = await apiFetch<any>('/coverage');
    const el = $<HTMLElement>('tx-port-risks-high');
    if (el) el.textContent = String(cov.risk_summary?.HIGH ?? 0);
  } catch { /* ignore */ }
  // Initial render of the agent cards and codegen panel so tabs feel populated
  try { renderSpecAgentCards(); } catch { /* not on page */ }
  try { renderCodegenServicesPanel(); } catch { /* not on page */ }
  // Default to tab 1 on each fresh load
  switchTxTab(1);
}

async function startTransform(): Promise<void> {
  const fw     = ($<HTMLSelectElement>('transform-framework'))?.value ?? 'Spring Boot';
  const cloud  = ($<HTMLSelectElement>('transform-cloud'))?.value ?? 'AWS';
  const decomp = ($<HTMLSelectElement>('transform-decomposition'))?.value ?? 'Strangler Fig';

  const btn = $<HTMLButtonElement>('transform-start-btn');
  if (btn) btn.disabled = true;

  const outputArea = $<HTMLElement>('transform-output-area');
  const sectionsEl = $<HTMLElement>('transform-sections');
  const completeEl = $<HTMLElement>('transform-complete');
  if (outputArea) outputArea.style.display = '';
  if (sectionsEl) sectionsEl.innerHTML = '<div style="color:var(--muted);font-size:13px;padding:8px 0;">Analyzing portfolio…</div>';
  if (completeEl) completeEl.style.display = 'none';

  let currentCardBody: HTMLElement | null = null;
  let portfolioMd = `# Portfolio Transformation: ${fw} on ${cloud}\n_Pattern: ${decomp}_\n\n`;

  try {
    const res = await fetch('/transform/portfolio', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ framework: fw, cloud, decomposition: decomp }),
      signal: sig(),
    });
    if (!res.body) throw new Error('No response body');
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let firstSection = true;
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
          if (ev.kind === 'section' && sectionsEl) {
            if (firstSection) { sectionsEl.innerHTML = ''; firstSection = false; }
            const sectionColors = ['#5ecdd1','#60c8fa','#34d399','#fbbf24','#d876d6','#a78bfa','#f97316','#f87171'];
            const idx = sectionsEl.children.length;
            const color = sectionColors[idx % sectionColors.length];
            const card = document.createElement('div');
            card.className = 'card';
            card.style.cssText = `border-left:3px solid ${color};margin-bottom:0;`;
            card.innerHTML = `
              <div style="font-weight:700;font-size:14px;margin-bottom:10px;color:${color};">${ev.section ?? ''}</div>
              <div style="font-size:13px;color:var(--muted);line-height:1.8;white-space:pre-wrap;"></div>
            `;
            sectionsEl.appendChild(card);
            currentCardBody = card.querySelector<HTMLElement>('div:last-child');
            if (currentCardBody) currentCardBody.textContent = ev.content ?? '';
            portfolioMd += `## ${ev.section}\n\n${ev.content ?? ''}\n\n`;
          } else if (ev.kind === 'token' && currentCardBody) {
            currentCardBody.textContent = (currentCardBody.textContent ?? '') + (ev.token ?? '');
            portfolioMd += ev.token ?? '';
          } else if (ev.kind === 'done') {
            if (completeEl) completeEl.style.display = '';
            (window as any)._portfolioTransformMd = portfolioMd;
            showToast('Portfolio analysis complete', 'ok');
          } else if (ev.kind === 'error') {
            showToast(ev.msg ?? 'Error generating analysis', 'error');
          }
        } catch { /* skip malformed SSE */ }
      }
    }
  } catch(e) {
    if (!isAbort(e)) showToast(`Analysis failed: ${(e as Error).message}`, 'error');
  }
  if (btn) btn.disabled = false;
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
  const combined = (window as any)._portfolioTransformMd ?? '';
  if (!combined) { showToast('No analysis output yet — run portfolio analysis first', 'error'); return; }
  try {
    const fw = ($<HTMLSelectElement>('transform-framework'))?.value ?? 'transform';
    const cloud = ($<HTMLSelectElement>('transform-cloud'))?.value ?? 'cloud';
    const title = `Portfolio_Transform_${fw.replace(/ /g,'_')}_${cloud}`;
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
  if (!running) { updatePipelineProgress(0); }
  _updatePipelineTopbar();
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

  // Clear the log buffer for a fresh run
  _pipelineLogBuffer.length = 0;
  stagesComplete.clear();
  setPipelineUI(true);

  // Replay any buffered events to the log panel (in case it was hidden before)
  _replayPipelineLog();

  // Prefer auto-detected corpus from clone/upload, then local path input
  const corpus = _detectedCorpus
    || ($<HTMLInputElement>('corpus-path'))?.value
    || 'external/carddemo/app/cbl';

  // Use a dedicated controller that navigation will NOT abort
  _pipelineCtrl = new AbortController();

  try {
    const res = await fetch('/pipeline/run', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ corpus }), signal: _pipelineCtrl.signal,
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
          _pipelineLogBuffer.push(ev);
          _updatePipelineLiveIfVisible(ev);
          if (ev.kind === 'done') {
            setPipelineUI(false);
            _pipelineCtrl = null;
            // Refresh dashboard stats after pipeline completes
            void loadDashboard();
            showToast('Pipeline completed', 'ok');
          }
        } catch { /* skip */ }
      }
    }
  } catch(e) {
    if (_pipelineCtrl && (e as Error).name !== 'AbortError') {
      const errEv = { kind: 'error', msg: (e as Error).message };
      _pipelineLogBuffer.push(errEv);
      _updatePipelineLiveIfVisible(errEv);
    }
  }
  setPipelineUI(false);
  _pipelineCtrl = null;
}

function _updatePipelineLiveIfVisible(ev: { kind: string; msg: string; ts?: number }): void {
  const log = $<HTMLElement>('pipeline-log');
  if (!log) return;  // pipeline page not visible — event buffered, will replay on return
  appendLog(log, ev);
  // Also update stage indicators
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
  // Update topbar running badge
  _updatePipelineTopbar();
}

function _replayPipelineLog(): void {
  const log = $<HTMLElement>('pipeline-log');
  if (!log) return;
  log.innerHTML = '';
  stagesComplete.clear();
  for (const ev of _pipelineLogBuffer) {
    _updatePipelineLiveIfVisible(ev);
  }
}

function _updatePipelineTopbar(): void {
  const badge = $<HTMLElement>('pipeline-running-badge');
  if (!badge) return;
  if (pipelineRunning) {
    badge.style.display = 'flex';
    const pct = STAGE_ORDER.length > 0
      ? Math.round((stagesComplete.size / STAGE_ORDER.length) * 100) : 0;
    const label = badge.querySelector('span:last-child');
    if (label) label.textContent = `Pipeline running… ${pct}%`;
  } else {
    badge.style.display = 'none';
  }
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
}

async function cancelPipeline(): Promise<void> {
  try {
    await fetch('/pipeline/cancel', { method: 'POST' });
    if (_pipelineCtrl) { _pipelineCtrl.abort(); _pipelineCtrl = null; }
    setPipelineUI(false);
    showToast('Pipeline cancelled');
  } catch(e) {
    showToast('Cancel failed: ' + (e as Error).message, 'error');
  }
}

// ── Copybooks ─────────────────────────────────────────────────────────────────
let _allCopybooks: Record<string, unknown>[] = [];

async function loadCopybooks(): Promise<void> {
  try {
    const rows = await apiFetch<Record<string, unknown>[]>('/copybooks');
    _allCopybooks = rows;
    const countEl = $<HTMLElement>('cpy-count');
    if (countEl) countEl.textContent = `${rows.length} copybooks`;
    renderCopybookTable(rows);
  } catch (e) {
    const tb = $<HTMLElement>('cpy-tbody');
    if (tb) tb.innerHTML = `<tr><td colspan="6" style="color:var(--muted);">No copybook catalog — run the pipeline first.</td></tr>`;
  }
}

function filterCopybooks(): void {
  const q = ($<HTMLInputElement>('cpy-search')?.value ?? '').toLowerCase();
  renderCopybookTable(_allCopybooks.filter(r => String(r['name'] ?? '').toLowerCase().includes(q)));
}

function renderCopybookTable(rows: Record<string, unknown>[]): void {
  const tb = $<HTMLElement>('cpy-tbody');
  if (!tb) return;
  const typeColor: Record<string, string> = { COPYBOOK: '#5ecdd1', BMS_COPYBOOK: '#fbbf24', STUB: '#94a3b8' };
  tb.innerHTML = rows.map(r => {
    const typ = String(r['source_type'] ?? 'COPYBOOK');
    const col = typeColor[typ] ?? '#5ecdd1';
    const statusBadge = r['parse_status'] === 'OK'
      ? '<span style="color:#22c55e;font-size:11px;">✓ OK</span>'
      : `<span style="color:#f87171;font-size:11px;">✗ ${r['parse_status']}</span>`;
    return `<tr style="cursor:pointer;" onclick="openCopybookDetail('${r['name']}')">
      <td style="font-weight:600;color:#60c8fa;">${r['name']}</td>
      <td><span style="color:${col};font-size:11px;background:#004b5c;padding:2px 8px;border-radius:10px;">${typ}</span></td>
      <td>${r['data_item_count'] ?? 0}</td>
      <td>${r['consumer_count'] ?? 0}</td>
      <td>${statusBadge}</td>
      <td><button class="btn btn-secondary" style="font-size:11px;padding:3px 10px;" onclick="event.stopPropagation();openCopybookDetail('${r['name']}')">View</button></td>
    </tr>`;
  }).join('');
  const countEl = $<HTMLElement>('cpy-count');
  if (countEl) countEl.textContent = `${rows.length} copybooks`;
}

async function openCopybookDetail(name: string): Promise<void> {
  const listWrap = $<HTMLElement>('cpy-table')?.parentElement;
  const detail = $<HTMLElement>('cpy-detail');
  if (listWrap) listWrap.style.display = 'none';
  if (detail) { detail.style.display = ''; detail.classList.add('fade-in'); }

  const nameEl = $<HTMLElement>('cpy-detail-name');
  const typeEl = $<HTMLElement>('cpy-detail-type');
  if (nameEl) nameEl.textContent = name;

  try {
    const d = await apiFetch<{ catalog: Record<string, unknown>; consumers: Record<string, unknown>[]; data_item_sample: Record<string, unknown>[]; data_item_total: number }>(`/copybooks/${encodeURIComponent(name)}`);

    if (typeEl) typeEl.textContent = String(d.catalog?.['source_type'] ?? 'COPYBOOK');
    const itemCount = $<HTMLElement>('cpy-item-count');
    if (itemCount) itemCount.textContent = String(d.data_item_total ?? 0);
    const consCount = $<HTMLElement>('cpy-consumer-count');
    if (consCount) consCount.textContent = String(d.consumers?.length ?? 0);

    const itemsTb = $<HTMLElement>('cpy-items-tbody');
    if (itemsTb) {
      itemsTb.innerHTML = (d.data_item_sample ?? []).map((item: Record<string, unknown>) =>
        `<tr><td style="color:#60c8fa;font-weight:600;">${item['name']}</td><td>${item['level']}</td><td style="font-family:monospace;">${item['pic'] ?? ''}</td><td style="color:#5ecdd1;">${item['canonical_kind'] ?? ''}</td></tr>`
      ).join('');
    }

    const consTb = $<HTMLElement>('cpy-consumers-tbody');
    if (consTb) {
      consTb.innerHTML = (d.consumers ?? []).map((c: Record<string, unknown>) => {
        const repl = (() => { try { const a = JSON.parse(String(c['replacing_json'] ?? '[]')); return Array.isArray(a) && a.length ? `${a.length} REPLACING` : '—'; } catch { return '—'; } })();
        return `<tr><td style="color:#5ecdd1;font-weight:600;">${c['program_name']}</td><td>${c['line'] ?? '—'}</td><td style="color:var(--muted);font-size:11px;">${repl}</td></tr>`;
      }).join('');
    }
  } catch (e) {
    const itemsTb = $<HTMLElement>('cpy-items-tbody');
    if (itemsTb) itemsTb.innerHTML = `<tr><td colspan="4" style="color:var(--muted);">Error loading detail</td></tr>`;
  }
}

function closeCpyDetail(): void {
  const listWrap = $<HTMLElement>('cpy-table')?.parentElement;
  const detail = $<HTMLElement>('cpy-detail');
  if (listWrap) listWrap.style.display = '';
  if (detail) detail.style.display = 'none';
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
      llm_provider: string; model: string;
      openai_model: string; gemini_model: string; anthropic_model: string;
      openai_key_set: boolean; gemini_key_set: boolean; anthropic_key_set: boolean;
    }>('/settings');

    const activeProvider = s.llm_provider || 'openai';
    const provEl = $<HTMLSelectElement>('settings-provider');
    if (provEl) provEl.value = activeProvider;

    // Show the correct API key field
    const openaiWrap = $<HTMLElement>('settings-openai-key-wrap');
    const geminiWrap = $<HTMLElement>('settings-gemini-key-wrap');
    const anthropicWrap = $<HTMLElement>('settings-anthropic-key-wrap');
    if (openaiWrap) openaiWrap.style.display = activeProvider === 'openai' ? '' : 'none';
    if (geminiWrap) geminiWrap.style.display = activeProvider === 'gemini' ? '' : 'none';
    if (anthropicWrap) anthropicWrap.style.display = activeProvider === 'anthropic' ? '' : 'none';

    // Resolve currently saved model for this provider
    const savedModel = s.model ||
      (activeProvider === 'gemini' ? s.gemini_model :
       activeProvider === 'anthropic' ? s.anthropic_model : s.openai_model) || '';

    const curEl = $<HTMLElement>('settings-current-info');
    if (curEl) curEl.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:10px;">
        <div><span style="color:var(--muted);font-size:12px;">Provider</span><br><span style="font-weight:600;">${activeProvider}</span></div>
        <div><span style="color:var(--muted);font-size:12px;">Model</span><br><span style="font-weight:600;">${savedModel || '—'}</span></div>
        <div><span style="color:var(--muted);font-size:12px;">OpenAI Key</span><br><span class="badge ${s.openai_key_set ? 'badge-green' : 'badge-red'}">${s.openai_key_set ? '✓ Set' : 'Not set'}</span></div>
        <div><span style="color:var(--muted);font-size:12px;">Gemini Key</span><br><span class="badge ${s.gemini_key_set ? 'badge-green' : 'badge-red'}">${s.gemini_key_set ? '✓ Set' : 'Not set'}</span></div>
        <div><span style="color:var(--muted);font-size:12px;">Anthropic Key</span><br><span class="badge ${s.anthropic_key_set ? 'badge-green' : 'badge-red'}">${s.anthropic_key_set ? '✓ Set' : 'Not set'}</span></div>
      </div>`;

    await loadModelsForProvider(activeProvider, savedModel);
  } catch(e) {
    if (!isAbort(e)) console.warn('Failed to load settings:', e);
  }
  // Agent LLM config card
  await loadAgentLlms();
}

async function loadModelsForProvider(provider: string, currentModel: string): Promise<void> {
  const sel = $<HTMLSelectElement>('settings-model');
  if (!sel) return;
  sel.innerHTML = '<option value="">— select model —</option>';
  sel.disabled = true;
  try {
    const data = await apiFetch<{ models: string[] }>(`/models?provider=${encodeURIComponent(provider.toLowerCase())}`);
    const models = data.models ?? [];
    sel.innerHTML = '<option value="">— select model —</option>' +
      models.map(m => `<option value="${m}" ${m === currentModel ? 'selected' : ''}>${m}</option>`).join('');
    sel.disabled = false;
  } catch {
    // On error, keep placeholder and show saved model if any
    sel.innerHTML = '<option value="">— select model —</option>' +
      (currentModel ? `<option value="${currentModel}" selected>${currentModel}</option>` : '');
    sel.disabled = false;
  }
}

async function onProviderChange(): Promise<void> {
  const provEl = $<HTMLSelectElement>('settings-provider');
  const provider = provEl?.value ?? 'openai';
  const openaiWrap = $<HTMLElement>('settings-openai-key-wrap');
  const geminiWrap = $<HTMLElement>('settings-gemini-key-wrap');
  const anthropicWrap = $<HTMLElement>('settings-anthropic-key-wrap');
  if (openaiWrap) openaiWrap.style.display = provider === 'openai' ? '' : 'none';
  if (geminiWrap) geminiWrap.style.display = provider === 'gemini' ? '' : 'none';
  if (anthropicWrap) anthropicWrap.style.display = provider === 'anthropic' ? '' : 'none';
  // Load models for the newly selected provider immediately — no save needed
  await loadModelsForProvider(provider, '');
}

async function saveSettings(): Promise<void> {
  const provider = ($<HTMLSelectElement>('settings-provider'))?.value ?? '';
  const model    = ($<HTMLSelectElement>('settings-model'))?.value ?? '';
  const errEl    = $<HTMLElement>('settings-model-error');

  if (!model) {
    if (errEl) errEl.style.display = '';
    showToast('Please select a model before saving', 'error');
    return;
  }
  if (errEl) errEl.style.display = 'none';

  const oaKey  = ($<HTMLInputElement>('settings-openai-key'))?.value?.trim() ?? '';
  const gmKey  = ($<HTMLInputElement>('settings-gemini-key'))?.value?.trim() ?? '';
  const anKey  = ($<HTMLInputElement>('settings-anthropic-key'))?.value?.trim() ?? '';

  const body: Record<string, string> = { llm_provider: provider, model };
  if (oaKey) body.openai_api_key = oaKey;
  if (gmKey) body.gemini_api_key = gmKey;
  if (anKey) body.anthropic_api_key = anKey;

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

// ── Agent LLM Configuration (per-agent provider/model) ────────────────────────
interface AgentLlmRow { role: string; provider: string; model: string; notes?: string; }

// Fallback model lists (used until API responds)
const _PROVIDER_MODELS_FALLBACK: Record<string, string[]> = {
  OpenAI:    ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo'],
  Anthropic: ['claude-opus-4-7', 'claude-sonnet-4-6', 'claude-haiku-4-5-20251001', 'claude-opus-4-5', 'claude-sonnet-4-5'],
  Gemini:    ['gemini-2.5-pro', 'gemini-2.0-flash', 'gemini-1.5-pro', 'gemini-1.5-flash'],
};

// Runtime cache populated from /models?provider=X
const _providerModelsCache: Record<string, string[]> = {};

async function fetchModelsForProvider(provider: string): Promise<string[]> {
  const key = provider.toLowerCase();
  if (_providerModelsCache[key]) return _providerModelsCache[key];
  try {
    const data = await apiFetch<{ models: string[] }>(`/models?provider=${encodeURIComponent(key)}`);
    const models = data.models ?? [];
    if (models.length) _providerModelsCache[key] = models;
    return models.length ? models : (_PROVIDER_MODELS_FALLBACK[provider] ?? []);
  } catch {
    return _PROVIDER_MODELS_FALLBACK[provider] ?? [];
  }
}

function _cachedModels(provider: string): string[] {
  return _providerModelsCache[provider.toLowerCase()] ?? _PROVIDER_MODELS_FALLBACK[provider] ?? [];
}

let _agentLlms: AgentLlmRow[] = [];

async function loadAgentLlms(): Promise<void> {
  const tbody = $<HTMLElement>('agent-llms-body');
  if (!tbody) return;
  try {
    const data = await apiFetch<{ agents: AgentLlmRow[] }>('/settings/agent-llms');
    _agentLlms = data.agents ?? [];
    // Prefetch models for all distinct providers used in the config
    const providers = [...new Set(_agentLlms.map(a => a.provider))];
    await Promise.all(providers.map(p => fetchModelsForProvider(p)));
    renderAgentLlmsTable();
  } catch (e) {
    if (!isAbort(e)) tbody.innerHTML = `<tr><td colspan="4" style="color:var(--muted);">Failed to load: ${(e as Error).message}</td></tr>`;
  }
}

function renderAgentLlmsTable(): void {
  const tbody = $<HTMLElement>('agent-llms-body');
  if (!tbody) return;
  if (!_agentLlms.length) {
    tbody.innerHTML = `<tr><td colspan="4" style="color:var(--muted);">No agents configured.</td></tr>`;
    return;
  }
  tbody.innerHTML = _agentLlms.map((row, i) => {
    const providers = ['OpenAI', 'Anthropic', 'Gemini'];
    const providerOpts = providers.map(p => `<option value="${p}" ${p === row.provider ? 'selected' : ''}>${p}</option>`).join('');
    const models = _cachedModels(row.provider);
    const isCustom = models.length > 0 && !models.includes(row.model);
    const modelOpts = models.map(m => `<option value="${m}" ${m === row.model ? 'selected' : ''}>${m}</option>`).join('');
    return `
      <tr>
        <td style="font-weight:600;color:var(--text);">${escapeHtml(row.role)}</td>
        <td>
          <select onchange="onAgentLlmProviderChange(${i}, this.value)" style="width:100%;">
            ${providerOpts}
          </select>
        </td>
        <td>
          <select onchange="onAgentLlmModelChange(${i}, this.value)" style="width:100%;" id="agent-llm-model-${i}">
            ${modelOpts}
            <option value="__custom" ${isCustom ? 'selected' : ''}>Custom…</option>
          </select>
          <input type="text" value="${escapeHtml(row.model)}" oninput="onAgentLlmModelChange(${i}, this.value)" style="width:100%;margin-top:6px;font-size:12px;" placeholder="Custom model id" />
        </td>
        <td style="color:var(--muted);font-size:12px;">${escapeHtml(row.notes ?? '')}</td>
      </tr>
    `;
  }).join('');
}

async function onAgentLlmProviderChange(idx: number, provider: string): Promise<void> {
  if (!_agentLlms[idx]) return;
  _agentLlms[idx].provider = provider;
  // Fetch models from API for the newly selected provider
  const models = await fetchModelsForProvider(provider);
  if (models.length) _agentLlms[idx].model = models[0];
  renderAgentLlmsTable();
}

function onAgentLlmModelChange(idx: number, model: string): void {
  if (!_agentLlms[idx]) return;
  if (model === '__custom') return; // wait for input box
  _agentLlms[idx].model = model;
}

async function saveAgentLlms(): Promise<void> {
  try {
    await apiFetch('/settings/agent-llms', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agents: _agentLlms }),
    });
    const saved = $<HTMLElement>('agent-llms-saved');
    if (saved) { saved.style.display = ''; setTimeout(() => { saved.style.display = 'none'; }, 3000); }
    showToast('Agent LLMs saved');
  } catch (e) {
    if (!isAbort(e)) showToast('Save failed: ' + (e as Error).message, 'error');
  }
}

async function resetAgentLlms(): Promise<void> {
  _agentLlms = [];
  await loadAgentLlms();
}

// escapeHtml() is defined later in this file — reuse it.

// ── Transform 4-Phase Workflow ────────────────────────────────────────────────

let _txCurrentTab = 1;
let _txCurrentSubTab: 'specs' | 'code' = 'specs';
let _txArchData: any = null;
let _txPlanState: Record<string, { status: 'pending' | 'accepted' | 'rejected'; description: string }> = {};
let _txPlanTotalSteps = 0;
let _txSpecsSections: Record<string, { label: string; content: string; words: number }> = {};
let _txServicesFromArch: { name: string; programs: string[] }[] = [];

// Architecture diagram zoom + level state
type ArchPanel = 'source' | 'target' | 'svc-src' | 'svc-tgt';
const _archZoomLevel: Record<ArchPanel, number> = {
  'source': 1, 'target': 1, 'svc-src': 1, 'svc-tgt': 1,
};
const _archLevel: Record<'source' | 'target', 'hl' | 'll'> = { source: 'hl', target: 'hl' };
let _txCurrentSvcTab: 'src' | 'tgt' | 'api' | 'ent' = 'src';
let _txCurrentServiceName = '';

function switchTxTab(tabId: number): void {
  _txCurrentTab = tabId;
  for (let i = 1; i <= 4; i++) {
    const tab = $<HTMLElement>(`tx-tab-${i}`);
    const pane = $<HTMLElement>(`tx-pane-${i}`);
    if (tab) tab.classList.toggle('active', i === tabId);
    if (pane) pane.style.display = i === tabId ? '' : 'none';
  }
  // Re-render mermaid when switching to tab 2 if data is already loaded
  if (tabId === 2 && _txArchData) {
    try { void (window as any).mermaid?.run({ querySelector: '#tx-pane-2 .mermaid' }); } catch { /* ignore */ }
  }
}

function switchTxSubTab(sub: 'specs' | 'code'): void {
  _txCurrentSubTab = sub;
  const specsTab = $<HTMLElement>('tx-sub-tab-specs');
  const codeTab  = $<HTMLElement>('tx-sub-tab-code');
  const specsPane = $<HTMLElement>('tx-sub-specs');
  const codePane  = $<HTMLElement>('tx-sub-code');
  if (specsTab) specsTab.classList.toggle('active', sub === 'specs');
  if (codeTab)  codeTab.classList.toggle('active',  sub === 'code');
  if (specsPane) specsPane.style.display = sub === 'specs' ? '' : 'none';
  if (codePane)  codePane.style.display  = sub === 'code'  ? '' : 'none';
}

// ── Architecture Mapping (Tab 2) ──────────────────────────────────────────────
async function loadTxArchitecture(): Promise<void> {
  const fw     = ($<HTMLSelectElement>('transform-framework'))?.value ?? 'Spring Boot';
  const cloud  = ($<HTMLSelectElement>('transform-cloud'))?.value ?? 'AWS';
  const decomp = ($<HTMLSelectElement>('transform-decomposition'))?.value ?? 'Strangler Fig';
  const btn = $<HTMLButtonElement>('tx-arch-btn');
  if (btn) btn.disabled = true;
  try {
    const data = await apiFetch<any>('/transform/source-architecture', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ framework: fw, cloud, decomposition: decomp }),
    });
    _txArchData = data;
    _txServicesFromArch = data.services ?? [];

    const out = $<HTMLElement>('tx-arch-output');
    if (out) out.style.display = '';

    const ss = data.source_stats ?? {};
    const ts = data.target_stats ?? {};
    const sourceStats = $<HTMLElement>('tx-arch-source-stats');
    if (sourceStats) sourceStats.innerHTML = `
      <strong>${ss.programs ?? 0}</strong> programs &middot;
      <strong>${ss.jcl_jobs ?? 0}</strong> JCL jobs &middot;
      <strong>${ss.cics_verbs ?? 0}</strong> CICS verbs &middot;
      <strong>${ss.file_io ?? 0}</strong> files &middot;
      <strong>${ss.copybooks ?? 0}</strong> copybooks
    `;
    const targetStats = $<HTMLElement>('tx-arch-target-stats');
    if (targetStats) targetStats.innerHTML = `
      <strong>${ts.services ?? 0}</strong> microservices &middot;
      <strong>${escapeHtml(ts.framework ?? '')}</strong> on <strong>${escapeHtml(ts.cloud ?? '')}</strong> &middot;
      Pattern: <strong>${escapeHtml(ts.pattern ?? '')}</strong>
    `;

    const sm = $<HTMLElement>('tx-arch-source-mermaid');
    const tm = $<HTMLElement>('tx-arch-target-mermaid');
    if (sm) sm.textContent = data.source_mermaid ?? '';
    if (tm) tm.textContent = data.target_mermaid ?? '';
    // Re-render mermaid
    try {
      // Reset the rendered flag so mermaid re-processes
      if (sm) sm.removeAttribute('data-processed');
      if (tm) tm.removeAttribute('data-processed');
      await (window as any).mermaid?.run({ querySelector: '#tx-pane-2 .mermaid' });
    } catch { /* ignore */ }

    // Mapping table
    const tbody = $<HTMLElement>('tx-arch-mapping-body');
    if (tbody) {
      type MappingRow = {
        source: string; target: string; strategy: string; effort: string; risk: string;
        business_description?: string;
        acceptance_criteria?: string[];
        cobol_to_oo_reasoning?: string;
      };
      const rows = (data.mapping ?? []) as MappingRow[];
      tbody.innerHTML = rows.map((r, idx) => {
        const riskColor = r.risk === 'High' ? '#f87171' : r.risk === 'Medium' ? '#fbbf24' : '#34d399';
        const riskBadge = r.risk === 'High' ? 'badge-red' : r.risk === 'Medium' ? 'badge-amber' : 'badge-green';
        const targetSafe = escapeHtml(r.target);
        const targetAttr = (r.target ?? '').replace(/'/g, "\\'");
        const hasDetail  = !!(r.business_description || r.acceptance_criteria?.length || r.cobol_to_oo_reasoning);
        const criteriaHtml = (r.acceptance_criteria ?? []).map(c => `<li style="margin-bottom:4px;">${escapeHtml(c)}</li>`).join('');
        const detailHtml = hasDetail ? `
          <tr id="mapping-detail-${idx}" style="display:none;">
            <td colspan="6" style="padding:12px 16px;background:rgba(94,205,209,0.04);border-top:1px solid rgba(94,205,209,0.12);">
              ${r.business_description ? `
              <div style="margin-bottom:10px;">
                <div style="font-size:11px;font-weight:700;color:#5ecdd1;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">Business Description</div>
                <div style="font-size:12px;color:var(--fg);line-height:1.6;">${escapeHtml(r.business_description)}</div>
              </div>` : ''}
              ${criteriaHtml ? `
              <div style="margin-bottom:10px;">
                <div style="font-size:11px;font-weight:700;color:#5ecdd1;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">Acceptance Criteria</div>
                <ul style="margin:0;padding-left:18px;font-size:12px;color:var(--fg);line-height:1.6;">${criteriaHtml}</ul>
              </div>` : ''}
              ${r.cobol_to_oo_reasoning ? `
              <div>
                <div style="font-size:11px;font-weight:700;color:#5ecdd1;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">COBOL&#x2192;Java OO Reasoning</div>
                <div style="font-size:12px;color:var(--fg);line-height:1.6;font-family:'JetBrains Mono',monospace;">${escapeHtml(r.cobol_to_oo_reasoning)}</div>
              </div>` : ''}
            </td>
          </tr>` : '';
        return `
          <tr>
            <td style="font-family:'JetBrains Mono',monospace;font-size:12px;">${escapeHtml(r.source)}</td>
            <td onclick="loadServiceDetail('${targetAttr}')" style="cursor:pointer;" title="Click to drill into ${targetSafe}">
              <span class="badge badge-teal" style="border-bottom:1px dashed #5ecdd1;">${targetSafe}</span>
              <span style="font-size:10px;color:#5ecdd1;margin-left:4px;">&#x21f2;</span>
            </td>
            <td style="color:var(--muted);font-size:12px;">${escapeHtml(r.strategy)}</td>
            <td><span class="badge badge-blue">${escapeHtml(r.effort)}</span></td>
            <td><span class="badge ${riskBadge}" style="color:${riskColor};">${escapeHtml(r.risk)}</span></td>
            <td style="text-align:center;">
              ${hasDetail ? `<button onclick="toggleMappingDetail(${idx})" title="Toggle details"
                style="background:none;border:none;cursor:pointer;color:#5ecdd1;font-size:14px;padding:2px 6px;"
                id="mapping-expand-btn-${idx}">&#x25B6;</button>` : ''}
            </td>
          </tr>
          ${detailHtml}`;
      }).join('') || `<tr><td colspan="6" style="color:var(--muted);">No mapping rows.</td></tr>`;
    }
    // Reset zoom/level state on each fresh load
    _archZoomLevel.source = 1;
    _archZoomLevel.target = 1;
    _archLevel.source = 'hl';
    _archLevel.target = 'hl';
    _updateArchLevelButtons('source');
    _updateArchLevelButtons('target');
    _updateArchZoomLabel('source');
    _updateArchZoomLabel('target');
    showToast('Architecture mapped');
  } catch (e) {
    if (!isAbort(e)) showToast('Architecture analysis failed: ' + (e as Error).message, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// ── Architecture Diagram Zoom / Level / Drill-down ────────────────────────────

function _archWrapId(panel: ArchPanel): string {
  return panel === 'source' ? 'tx-arch-source-wrap'
       : panel === 'target' ? 'tx-arch-target-wrap'
       : panel === 'svc-src' ? 'tx-svc-src-wrap'
       : 'tx-svc-tgt-wrap';
}

function _archCardId(panel: ArchPanel): string {
  return panel === 'source' ? 'tx-arch-source-card'
       : panel === 'target' ? 'tx-arch-target-card'
       : 'tx-service-detail-panel';
}

function _archZoomLabelId(panel: ArchPanel): string {
  return panel === 'source' ? 'arch-source-zoom-label'
       : panel === 'target' ? 'arch-target-zoom-label'
       : panel === 'svc-src' ? 'arch-svc-src-zoom-label'
       : 'arch-svc-tgt-zoom-label';
}

function _updateArchZoomLabel(panel: ArchPanel): void {
  const lab = $<HTMLElement>(_archZoomLabelId(panel));
  if (lab) lab.textContent = `${Math.round(_archZoomLevel[panel] * 100)}%`;
}

function _applyArchZoom(panel: ArchPanel): void {
  const wrap = $<HTMLElement>(_archWrapId(panel));
  if (!wrap) return;
  const scale = _archZoomLevel[panel];
  const target: HTMLElement | null = wrap.querySelector('svg') ?? wrap.querySelector('.mermaid');
  if (target) {
    target.style.transform = `scale(${scale})`;
    target.style.transformOrigin = 'top left';
    // Grow (or shrink) the wrapper so the scaled content is never clipped.
    // We read the element's natural (un-scaled) bounding height, then multiply.
    const naturalH = target.getBoundingClientRect().height / scale;
    const needed   = Math.round(naturalH * scale) + 40; // +40 padding
    wrap.style.minHeight = `${Math.max(needed, 400)}px`;
    // Remove max-height cap when zoomed in so the container can expand freely.
    wrap.style.maxHeight = scale > 1.05 ? 'none' : '';
  }
  _updateArchZoomLabel(panel);
}

function archZoom(panel: ArchPanel, factor: number): void {
  if (factor === -1) {
    // Toggle fullscreen on the parent card
    const card = $<HTMLElement>(_archCardId(panel));
    if (card) card.classList.toggle('arch-fullscreen');
    return;
  }
  if (factor === 0) {
    _archZoomLevel[panel] = 1;
  } else {
    _archZoomLevel[panel] = Math.min(5, Math.max(0.2, _archZoomLevel[panel] * factor));
  }
  _applyArchZoom(panel);
}

function _updateArchLevelButtons(panel: 'source' | 'target'): void {
  const lvl = _archLevel[panel];
  const hlBtn = $<HTMLElement>(`arch-${panel}-hl-btn`);
  const llBtn = $<HTMLElement>(`arch-${panel}-ll-btn`);
  if (hlBtn) {
    hlBtn.classList.toggle('btn-primary', lvl === 'hl');
    hlBtn.classList.toggle('btn-secondary', lvl !== 'hl');
  }
  if (llBtn) {
    llBtn.classList.toggle('btn-primary', lvl === 'll');
    llBtn.classList.toggle('btn-secondary', lvl !== 'll');
  }
}

async function archSetLevel(panel: 'source' | 'target', level: 'hl' | 'll'): Promise<void> {
  if (_archLevel[panel] === level) return;
  _archLevel[panel] = level;
  _updateArchLevelButtons(panel);
  const preId = panel === 'source' ? 'tx-arch-source-mermaid' : 'tx-arch-target-mermaid';
  const pre = $<HTMLElement>(preId);
  if (!pre) return;

  // Try to use cached LL mermaid first
  const cachedHl = panel === 'source' ? _txArchData?.source_mermaid : _txArchData?.target_mermaid;
  const cachedLl = panel === 'source' ? _txArchData?.source_ll_mermaid : _txArchData?.target_ll_mermaid;
  let mmd = level === 'hl' ? cachedHl : cachedLl;

  // If LL not cached, fetch from API
  if (level === 'll' && !mmd) {
    pre.textContent = 'graph TD\n  Loading["Loading low-level view…"]';
    pre.removeAttribute('data-processed');
    try {
      await (window as any).mermaid?.run({ querySelector: `#${preId}` });
    } catch { /* ignore */ }
    try {
      const fw     = ($<HTMLSelectElement>('transform-framework'))?.value ?? 'Spring Boot';
      const cloud  = ($<HTMLSelectElement>('transform-cloud'))?.value ?? 'AWS';
      const decomp = ($<HTMLSelectElement>('transform-decomposition'))?.value ?? 'Strangler Fig';
      const data = await apiFetch<any>('/transform/source-architecture', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ framework: fw, cloud, decomposition: decomp, level: 'll' }),
      });
      if (_txArchData) {
        _txArchData.source_ll_mermaid = data.source_ll_mermaid ?? _txArchData.source_ll_mermaid;
        _txArchData.target_ll_mermaid = data.target_ll_mermaid ?? _txArchData.target_ll_mermaid;
      }
      mmd = panel === 'source' ? data.source_ll_mermaid : data.target_ll_mermaid;
    } catch (e) {
      if (!isAbort(e)) showToast('Low-level view failed: ' + (e as Error).message, 'error');
      _archLevel[panel] = 'hl';
      _updateArchLevelButtons(panel);
      return;
    }
  }
  pre.textContent = mmd ?? `graph TD\n  Empty["No ${level === 'hl' ? 'high-level' : 'low-level'} data"]`;
  pre.removeAttribute('data-processed');
  try {
    await (window as any).mermaid?.run({ querySelector: `#${preId}` });
  } catch { /* ignore */ }
  _archZoomLevel[panel] = 1;
  _applyArchZoom(panel);
}

function switchSvcTab(tab: 'src' | 'tgt' | 'api' | 'ent'): void {
  _txCurrentSvcTab = tab;
  const map: Record<string, string> = { src: 'tx-svc-src-pane', tgt: 'tx-svc-tgt-pane', api: 'tx-svc-api-pane', ent: 'tx-svc-ent-pane' };
  for (const k of Object.keys(map)) {
    const pane = $<HTMLElement>(map[k]);
    if (pane) pane.style.display = k === tab ? '' : 'none';
    const tabEl = $<HTMLElement>(`tx-svc-tab-${k}`);
    if (tabEl) tabEl.classList.toggle('active', k === tab);
  }
  if (tab === 'src') {
    _applyArchZoom('svc-src');
  }
  if (tab === 'tgt') {
    // The target pane was hidden when loadServiceDetail ran, so Mermaid rendered
    // at zero size. Force a clean re-render now that the pane is visible.
    const tgtM = $<HTMLElement>('tx-svc-tgt-mermaid');
    if (tgtM) {
      // If Mermaid already replaced content with SVG, restore the source text first.
      const existingSvg = tgtM.querySelector('svg');
      const storedSrc   = (tgtM as any)._mmdSource as string | undefined;
      if (existingSvg && storedSrc) {
        tgtM.textContent = storedSrc;
        tgtM.removeAttribute('data-processed');
      } else if (!existingSvg) {
        tgtM.removeAttribute('data-processed');
      }
      void (window as any).mermaid?.run({ querySelector: '#tx-svc-tgt-mermaid' })
        .then(() => _applyArchZoom('svc-tgt'))
        .catch(() => { /* ignore render errors */ });
    } else {
      _applyArchZoom('svc-tgt');
    }
  }
}

function closeServiceDetail(): void {
  const panel = $<HTMLElement>('tx-service-detail-panel');
  if (panel) {
    panel.style.display = 'none';
    panel.classList.remove('arch-fullscreen');
  }
  _txCurrentServiceName = '';
}

async function loadServiceDetail(svcName: string): Promise<void> {
  if (!svcName) return;
  _txCurrentServiceName = svcName;
  const panel = $<HTMLElement>('tx-service-detail-panel');
  const title = $<HTMLElement>('tx-svc-detail-title');
  if (title) title.textContent = `${svcName} — Detailed Architecture`;
  if (panel) {
    panel.style.display = '';
    try { panel.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch { /* ignore */ }
  }
  // Default to src tab
  switchSvcTab('src');

  const srcM = $<HTMLElement>('tx-svc-src-mermaid');
  const tgtM = $<HTMLElement>('tx-svc-tgt-mermaid');
  if (srcM) { srcM.textContent = 'graph TD\n  Loading["Loading source detail…"]'; srcM.removeAttribute('data-processed'); }
  if (tgtM) { tgtM.textContent = 'graph TD\n  Loading["Loading target detail…"]'; tgtM.removeAttribute('data-processed'); }
  try {
    await (window as any).mermaid?.run({ querySelector: '#tx-service-detail-panel .mermaid' });
  } catch { /* ignore */ }

  const fw    = ($<HTMLSelectElement>('transform-framework'))?.value ?? 'Spring Boot';
  const cloud = ($<HTMLSelectElement>('transform-cloud'))?.value ?? 'AWS';

  try {
    const data = await apiFetch<any>('/transform/service-detail', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ service: svcName, framework: fw, cloud }),
    });

    const srcSrc = data.source_ll_mermaid ?? 'graph TD\n  Empty["No source data"]';
    const tgtSrc = data.target_ll_mermaid ?? 'flowchart LR\n  Empty["No target data"]';
    if (srcM) { srcM.textContent = srcSrc; srcM.removeAttribute('data-processed'); }
    if (tgtM) {
      tgtM.textContent = tgtSrc;
      (tgtM as any)._mmdSource = tgtSrc;  // store so tab-switch can restore after SVG replace
      tgtM.removeAttribute('data-processed');
    }

    const srcStats = $<HTMLElement>('tx-svc-src-stats');
    if (srcStats) {
      const np = (data.source_programs ?? []).length;
      srcStats.innerHTML = `<strong>${np}</strong> programs &middot; <strong>${data.total_paragraphs ?? 0}</strong> paragraphs &middot; <strong>${data.total_statements ?? 0}</strong> statements &middot; <strong>${data.total_cc ?? 0}</strong> total cyclomatic complexity`;
    }
    const tgtStats = $<HTMLElement>('tx-svc-tgt-stats');
    if (tgtStats) {
      const apis = (data.api_contracts ?? []).length;
      const ents = (data.entities ?? []).length;
      tgtStats.innerHTML = `<strong>${apis}</strong> REST endpoints &middot; <strong>${ents}</strong> entities &middot; ${escapeHtml(fw)} on ${escapeHtml(cloud)}`;
    }

    const apiBody = $<HTMLElement>('tx-svc-api-body');
    if (apiBody) {
      const contracts = (data.api_contracts ?? []) as Array<{ method: string; path: string; description: string; source_program?: string; cics_verb?: string }>;
      apiBody.innerHTML = contracts.map(c => {
        const badge = c.method === 'GET' ? 'badge-green'
                    : c.method === 'POST' ? 'badge-teal'
                    : c.method === 'PUT' ? 'badge-amber'
                    : c.method === 'DELETE' ? 'badge-red'
                    : 'badge-blue';
        return `<tr>
          <td><span class="badge ${badge}">${escapeHtml(c.method ?? '')}</span></td>
          <td style="font-family:'JetBrains Mono',monospace;font-size:12px;">${escapeHtml(c.path ?? '')}</td>
          <td style="font-size:12px;">${escapeHtml(c.description ?? '')}</td>
          <td style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--muted);">${escapeHtml(c.source_program ?? '')}</td>
          <td style="font-size:11px;color:var(--muted);">${escapeHtml(c.cics_verb ?? '')}</td>
        </tr>`;
      }).join('') || '<tr><td colspan="5" style="color:var(--muted);">No API contracts.</td></tr>';
    }

    const entBody = $<HTMLElement>('tx-svc-ent-body');
    if (entBody) {
      const entities = (data.entities ?? []) as Array<{ name: string; source_record?: string; field_count?: number; table_name?: string; key_type?: string }>;
      entBody.innerHTML = entities.map(e =>
        `<tr>
          <td style="font-weight:600;">${escapeHtml(e.name ?? '')}</td>
          <td style="font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--muted);">${escapeHtml(e.source_record ?? '')}</td>
          <td>${e.field_count ?? 0}</td>
          <td style="font-family:'JetBrains Mono',monospace;font-size:12px;">${escapeHtml(e.table_name ?? '')}</td>
          <td><span class="badge badge-blue">${escapeHtml(e.key_type ?? '')}</span></td>
        </tr>`
      ).join('') || '<tr><td colspan="5" style="color:var(--muted);">No entities.</td></tr>';
    }

    // Only render the visible (source) pane — target is hidden and would render at 0px.
    // switchSvcTab('tgt') will trigger its render when the user opens that tab.
    try {
      await (window as any).mermaid?.run({ querySelector: '#tx-svc-src-mermaid' });
    } catch { /* ignore */ }
    _archZoomLevel['svc-src'] = 1;
    _archZoomLevel['svc-tgt'] = 1;
    _updateArchZoomLabel('svc-src');
    _updateArchZoomLabel('svc-tgt');
    _applyArchZoom('svc-src');
  } catch (e) {
    if (!isAbort(e)) showToast('Service detail failed: ' + (e as Error).message, 'error');
  }
}

// ── Migration Plan (Tab 3) ────────────────────────────────────────────────────
async function loadTxPlan(): Promise<void> {
  const fw     = ($<HTMLSelectElement>('transform-framework'))?.value ?? 'Spring Boot';
  const cloud  = ($<HTMLSelectElement>('transform-cloud'))?.value ?? 'AWS';
  const decomp = ($<HTMLSelectElement>('transform-decomposition'))?.value ?? 'Strangler Fig';
  const btn = $<HTMLButtonElement>('tx-plan-btn');
  if (btn) btn.disabled = true;

  const container = $<HTMLElement>('tx-plan-phases');
  const summary   = $<HTMLElement>('tx-plan-summary');
  if (container) container.innerHTML = '<div style="color:var(--muted);font-size:13px;">Generating plan…</div>';
  if (summary) summary.style.display = '';
  _txPlanState = {};
  _txPlanTotalSteps = 0;

  try {
    const res = await fetch('/transform/migration-plan', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ framework: fw, cloud, decomposition: decomp }),
      signal: sig(),
    });
    if (!res.body) throw new Error('No body');
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    if (container) container.innerHTML = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split('\n\n'); buf = parts.pop() ?? '';
      for (const p of parts) {
        if (!p.startsWith('data:')) continue;
        try {
          const ev = JSON.parse(p.slice(5));
          if (ev.kind === 'phase' && container) {
            renderPlanPhase(ev, container);
            for (const st of (ev.steps ?? [])) {
              _txPlanState[st.id] = { status: 'pending', description: st.description };
              _txPlanTotalSteps += 1;
            }
            updatePlanSummary();
          } else if (ev.kind === 'done') {
            showToast(`Plan ready — ${ev.total_steps} steps across ${ev.total_phases} phases`);
          } else if (ev.kind === 'error') {
            showToast(ev.msg ?? 'Plan error', 'error');
          }
        } catch { /* skip */ }
      }
    }
  } catch (e) {
    if (!isAbort(e)) showToast('Plan failed: ' + (e as Error).message, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

function renderPlanPhase(phase: any, container: HTMLElement): void {
  const card = document.createElement('div');
  card.className = 'card';
  const stepsHtml = (phase.steps ?? []).map((st: any) => {
    const riskBadge = st.risk === 'High' ? 'badge-red' : st.risk === 'Medium' ? 'badge-amber' : 'badge-green';
    return `
      <div class="card-sm" id="plan-step-${st.id}" data-step="${st.id}">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px;">
          <div style="flex:1;">
            <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px;">
              <strong style="font-size:13px;">${escapeHtml(st.title)}</strong>
              <span class="badge badge-blue">${escapeHtml(st.effort)}</span>
              <span class="badge ${riskBadge}">${escapeHtml(st.risk)}</span>
              <span class="badge badge-gray">${escapeHtml(st.owner)}</span>
            </div>
            <div id="plan-step-desc-${st.id}" style="font-size:12px;color:var(--muted);line-height:1.6;">${escapeHtml(st.description)}</div>
            <div id="plan-step-edit-${st.id}" style="display:none;margin-top:8px;">
              <textarea id="plan-step-textarea-${st.id}" style="width:100%;min-height:80px;font-size:12px;">${escapeHtml(st.description)}</textarea>
              <div style="display:flex;gap:8px;margin-top:6px;">
                <button class="btn btn-success" style="padding:4px 12px;font-size:12px;" onclick="saveEditPlanStep('${st.id}')">Save</button>
                <button class="btn btn-secondary" style="padding:4px 12px;font-size:12px;" onclick="cancelEditPlanStep('${st.id}')">Cancel</button>
              </div>
            </div>
          </div>
          <div style="display:flex;flex-direction:column;gap:6px;flex-shrink:0;">
            <button class="btn btn-success" style="padding:4px 10px;font-size:12px;" onclick="acceptPlanStep('${st.id}')" title="Accept">Accept</button>
            <button class="btn btn-danger"  style="padding:4px 10px;font-size:12px;" onclick="rejectPlanStep('${st.id}')" title="Reject">Reject</button>
            <button class="btn btn-secondary" style="padding:4px 10px;font-size:12px;" onclick="editPlanStep('${st.id}')" title="Edit">Edit</button>
          </div>
        </div>
      </div>
    `;
  }).join('');
  card.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
      <div>
        <div style="font-weight:700;font-size:15px;color:#5ecdd1;">${escapeHtml(phase.name)}</div>
        <div style="font-size:12px;color:var(--muted);margin-top:2px;">${escapeHtml(phase.duration ?? '')} &middot; Lead: ${escapeHtml(phase.owner ?? '')}</div>
      </div>
    </div>
    <div style="display:flex;flex-direction:column;gap:10px;">${stepsHtml}</div>
  `;
  container.appendChild(card);
}

function acceptPlanStep(id: string): void {
  const st = _txPlanState[id]; if (!st) return;
  st.status = 'accepted';
  applyPlanStepStyle(id);
  updatePlanSummary();
}

function rejectPlanStep(id: string): void {
  const st = _txPlanState[id]; if (!st) return;
  st.status = 'rejected';
  applyPlanStepStyle(id);
  updatePlanSummary();
}

function editPlanStep(id: string): void {
  const desc = $<HTMLElement>(`plan-step-desc-${id}`);
  const edit = $<HTMLElement>(`plan-step-edit-${id}`);
  if (desc) desc.style.display = 'none';
  if (edit) edit.style.display = '';
}

function cancelEditPlanStep(id: string): void {
  const desc = $<HTMLElement>(`plan-step-desc-${id}`);
  const edit = $<HTMLElement>(`plan-step-edit-${id}`);
  if (desc) desc.style.display = '';
  if (edit) edit.style.display = 'none';
}

function saveEditPlanStep(id: string): void {
  const ta = $<HTMLTextAreaElement>(`plan-step-textarea-${id}`);
  const desc = $<HTMLElement>(`plan-step-desc-${id}`);
  const edit = $<HTMLElement>(`plan-step-edit-${id}`);
  if (!ta || !desc) return;
  const newText = ta.value.trim();
  if (_txPlanState[id]) _txPlanState[id].description = newText;
  desc.textContent = newText;
  desc.style.display = '';
  if (edit) edit.style.display = 'none';
  showToast('Step updated');
}

function applyPlanStepStyle(id: string): void {
  const el = $<HTMLElement>(`plan-step-${id}`);
  const desc = $<HTMLElement>(`plan-step-desc-${id}`);
  const st = _txPlanState[id]; if (!el || !st) return;
  el.style.borderColor = st.status === 'accepted' ? '#34d399' : st.status === 'rejected' ? '#f87171' : 'var(--border)';
  el.style.background  = st.status === 'accepted' ? 'rgba(52,211,153,.08)' : st.status === 'rejected' ? 'rgba(248,113,113,.08)' : 'var(--surface2)';
  if (desc) {
    desc.style.textDecoration = st.status === 'rejected' ? 'line-through' : 'none';
    desc.style.opacity = st.status === 'rejected' ? '0.6' : '1';
  }
}

function updatePlanSummary(): void {
  let acc = 0, rej = 0, pen = 0;
  for (const id of Object.keys(_txPlanState)) {
    const s = _txPlanState[id].status;
    if (s === 'accepted') acc++;
    else if (s === 'rejected') rej++;
    else pen++;
  }
  const total = _txPlanTotalSteps || 1;
  const accEl = $<HTMLElement>('tx-plan-acc-count'); if (accEl) accEl.textContent = String(acc);
  const rejEl = $<HTMLElement>('tx-plan-rej-count'); if (rejEl) rejEl.textContent = String(rej);
  const penEl = $<HTMLElement>('tx-plan-pen-count'); if (penEl) penEl.textContent = String(pen);
  const fill = $<HTMLElement>('tx-plan-progress'); if (fill) fill.style.width = ((acc / total) * 100) + '%';
  const next = $<HTMLButtonElement>('tx-plan-next-btn');
  if (next) next.disabled = (acc / total) < 0.5;
}

// ── Comprehensive Specs (Tab 4 — sub-tab A) ───────────────────────────────────
const _TX_AGENT_DEFS: { id: string; label: string; icon: string }[] = [
  { id: 'executive',        label: 'Executive Summary',         icon: 'EXEC' },
  { id: 'business_analyst', label: 'Current State Analysis',    icon: 'BA' },
  { id: 'system_architect', label: 'Target Architecture Design', icon: 'ARC' },
  { id: 'tech_lead',        label: 'Technical Specification',   icon: 'TECH' },
  { id: 'data_architect',   label: 'Data Architecture & Migration', icon: 'DATA' },
  { id: 'api_designer',     label: 'API Design & Contracts',    icon: 'API' },
  { id: 'security_analyst', label: 'Security Architecture',     icon: 'SEC' },
  { id: 'devops_engineer',  label: 'Infrastructure & DevOps',   icon: 'OPS' },
  { id: 'qa_lead',          label: 'Test Strategy & QA Plan',   icon: 'QA' },
  { id: 'pm',               label: 'Project Plan & Risk Register', icon: 'PM' },
];

function renderSpecAgentCards(): void {
  const grid = $<HTMLElement>('tx-specs-grid');
  if (!grid) return;
  grid.innerHTML = _TX_AGENT_DEFS.map(a => `
    <div class="card-sm" id="spec-agent-${a.id}" style="border-left:3px solid var(--border);">
      <div style="display:flex;align-items:center;gap:10px;">
        <div style="width:42px;height:42px;border-radius:8px;background:var(--surface);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:11px;color:#5ecdd1;border:1px solid var(--border);">${a.icon}</div>
        <div style="flex:1;">
          <div style="font-weight:600;font-size:13px;">${escapeHtml(a.label)}</div>
          <div id="spec-agent-status-${a.id}" style="font-size:11px;color:var(--muted);margin-top:2px;">Queued</div>
        </div>
        <div id="spec-agent-words-${a.id}" style="font-size:11px;color:var(--muted);">—</div>
      </div>
      <div class="progress-bar" style="margin-top:10px;height:5px;"><div id="spec-agent-progress-${a.id}" class="progress-fill" style="width:0%;"></div></div>
    </div>
  `).join('');
}

async function loadTxComprehensiveSpecs(): Promise<void> {
  const fw     = ($<HTMLSelectElement>('transform-framework'))?.value ?? 'Spring Boot';
  const cloud  = ($<HTMLSelectElement>('transform-cloud'))?.value ?? 'AWS';
  const decomp = ($<HTMLSelectElement>('transform-decomposition'))?.value ?? 'Strangler Fig';

  const btn = $<HTMLButtonElement>('tx-specs-btn');
  if (btn) btn.disabled = true;
  _txSpecsSections = {};
  renderSpecAgentCards();
  const actions = $<HTMLElement>('tx-specs-actions');
  const preview = $<HTMLElement>('tx-specs-preview');
  if (actions) actions.style.display = 'none';
  if (preview) preview.style.display = 'none';

  try {
    const res = await fetch('/transform/specs/comprehensive', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ framework: fw, cloud, decomposition: decomp }),
      signal: sig(),
    });
    if (!res.body) throw new Error('No body');
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split('\n\n'); buf = parts.pop() ?? '';
      for (const p of parts) {
        if (!p.startsWith('data:')) continue;
        try {
          const ev = JSON.parse(p.slice(5));
          handleSpecEvent(ev);
        } catch { /* skip */ }
      }
    }
  } catch (e) {
    if (!isAbort(e)) showToast('Spec generation failed: ' + (e as Error).message, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

function handleSpecEvent(ev: any): void {
  if (ev.kind === 'agent_start') {
    const card   = $<HTMLElement>(`spec-agent-${ev.agent}`);
    const status = $<HTMLElement>(`spec-agent-status-${ev.agent}`);
    const prog   = $<HTMLElement>(`spec-agent-progress-${ev.agent}`);
    if (card) card.style.borderLeftColor = '#fbbf24';
    if (status) { status.textContent = 'Running…'; status.style.color = '#fbbf24'; }
    if (prog) prog.style.width = '15%';
    _txSpecsSections[ev.agent] = { label: ev.label, content: '', words: 0 };
  } else if (ev.kind === 'agent_chunk') {
    if (!_txSpecsSections[ev.agent]) _txSpecsSections[ev.agent] = { label: ev.agent, content: '', words: 0 };
    _txSpecsSections[ev.agent].content += ev.chunk;
    const prog = $<HTMLElement>(`spec-agent-progress-${ev.agent}`);
    if (prog) {
      const cur = parseFloat(prog.style.width || '15') || 15;
      prog.style.width = Math.min(90, cur + 3) + '%';
    }
  } else if (ev.kind === 'agent_done') {
    const card   = $<HTMLElement>(`spec-agent-${ev.agent}`);
    const status = $<HTMLElement>(`spec-agent-status-${ev.agent}`);
    const words  = $<HTMLElement>(`spec-agent-words-${ev.agent}`);
    const prog   = $<HTMLElement>(`spec-agent-progress-${ev.agent}`);
    if (card) card.style.borderLeftColor = '#34d399';
    if (status) { status.textContent = 'Done'; status.style.color = '#34d399'; }
    if (words) words.textContent = `${(ev.word_count ?? 0).toLocaleString()} words`;
    if (prog) prog.style.width = '100%';
    if (_txSpecsSections[ev.agent]) _txSpecsSections[ev.agent].words = ev.word_count ?? 0;
  } else if (ev.kind === 'all_done') {
    const actions = $<HTMLElement>('tx-specs-actions');
    const preview = $<HTMLElement>('tx-specs-preview');
    const totals  = $<HTMLElement>('tx-specs-totals');
    if (actions) actions.style.display = '';
    if (preview) preview.style.display = '';
    if (totals) totals.textContent = `${(ev.sections ?? 0)} sections · ${(ev.total_words ?? 0).toLocaleString()} words total`;
    const assembled = assembleComprehensiveSpec();
    (window as any)._comprehensiveSpecMd = assembled;
    const body = $<HTMLElement>('tx-specs-preview-body');
    if (body) body.textContent = assembled.slice(0, 12000) + (assembled.length > 12000 ? '\n\n…(truncated for preview)' : '');
    showToast('All specs generated');
  } else if (ev.kind === 'error') {
    showToast(ev.msg ?? 'Spec error', 'error');
  }
}

function assembleComprehensiveSpec(): string {
  const fw    = ($<HTMLSelectElement>('transform-framework'))?.value ?? 'Spring Boot';
  const cloud = ($<HTMLSelectElement>('transform-cloud'))?.value ?? 'AWS';
  const decomp = ($<HTMLSelectElement>('transform-decomposition'))?.value ?? 'Strangler Fig';
  const header = `# Comprehensive Modernization Specification\n\n` +
                 `**Framework:** ${fw}  |  **Cloud:** ${cloud}  |  **Pattern:** ${decomp}\n\n` +
                 `_Generated by ten parallel modernization agents. Each section is grounded in the artifact pipeline output._\n\n---\n\n`;
  const parts: string[] = [header];
  for (const def of _TX_AGENT_DEFS) {
    const s = _txSpecsSections[def.id];
    if (!s) continue;
    parts.push(s.content.trim());
    parts.push('\n\n---\n\n');
  }
  return parts.join('');
}

async function downloadComprehensiveSpec(format: 'md' | 'pdf'): Promise<void> {
  const md = (window as any)._comprehensiveSpecMd ?? assembleComprehensiveSpec();
  if (!md) { showToast('No spec yet — generate first', 'error'); return; }
  const fw    = ($<HTMLSelectElement>('transform-framework'))?.value ?? 'spec';
  const cloud = ($<HTMLSelectElement>('transform-cloud'))?.value ?? 'cloud';
  const title = `Modernization_Spec_${fw.replace(/ /g, '_')}_${cloud}`;
  if (format === 'md') {
    const blob = new Blob([md], { type: 'text/markdown' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = `${title}.md`; a.click();
    return;
  }
  try {
    const resp = await fetch('/specs/export/pdf', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: md, title }),
    });
    if (!resp.ok) { showToast('PDF export failed', 'error'); return; }
    const blob = await resp.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = `${title}.pdf`; a.click();
  } catch (e) {
    if (!isAbort(e)) showToast('PDF failed: ' + (e as Error).message, 'error');
  }
}

// ── Code Generation (Tab 4 — sub-tab B) ───────────────────────────────────────
function renderCodegenServicesPanel(): void {
  const container = $<HTMLElement>('tx-code-services');
  if (!container) return;
  const services = _txServicesFromArch.length ? _txServicesFromArch : [
    { name: 'AuthService',        programs: [] },
    { name: 'UserService',        programs: [] },
    { name: 'AccountService',     programs: [] },
    { name: 'CardService',        programs: [] },
    { name: 'TransactionService', programs: [] },
    { name: 'ReportingService',   programs: [] },
  ];
  container.innerHTML = services.map(svc => {
    const progsTxt = svc.programs.length ? svc.programs.join(', ') : '(no programs mapped)';
    return `
      <div class="card" id="codegen-svc-${escapeHtml(svc.name)}" data-svc="${escapeHtml(svc.name)}">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;">
          <div style="flex:1;min-width:240px;">
            <div style="font-weight:700;font-size:14px;color:#5ecdd1;">${escapeHtml(svc.name)}</div>
            <div style="font-size:12px;color:var(--muted);margin-top:4px;">Source programs: ${escapeHtml(progsTxt)}</div>
          </div>
          <div style="display:flex;gap:8px;align-items:center;">
            <span id="codegen-status-${escapeHtml(svc.name)}" class="badge badge-gray">Idle</span>
            <button class="btn btn-secondary" onclick="toggleCodegenOutput('${escapeHtml(svc.name)}')">Toggle Output</button>
            <button class="btn btn-primary"   onclick="generateServiceCode('${escapeHtml(svc.name)}')">Generate</button>
          </div>
        </div>
        <div id="codegen-output-${escapeHtml(svc.name)}" style="display:none;margin-top:12px;">
          <div id="codegen-files-${escapeHtml(svc.name)}" style="display:flex;flex-direction:column;gap:10px;"></div>
        </div>
      </div>
    `;
  }).join('');

  // Also populate the export service selector
  const exportSel = $<HTMLSelectElement>('codegen-export-service');
  if (exportSel) {
    exportSel.innerHTML = '<option value="all">All Services</option>' +
      services.map(s => `<option value="${escapeHtml(s.name)}">${escapeHtml(s.name)}</option>`).join('');
  }
}

function toggleCodegenOutput(svc: string): void {
  const out = $<HTMLElement>(`codegen-output-${svc}`);
  if (out) out.style.display = out.style.display === 'none' ? '' : 'none';
}

async function generateServiceCode(svcName: string): Promise<void> {
  const fw    = ($<HTMLSelectElement>('transform-framework'))?.value ?? 'Spring Boot';
  const cloud = ($<HTMLSelectElement>('transform-cloud'))?.value ?? 'AWS';
  const programs = (_txServicesFromArch.find(s => s.name === svcName)?.programs) ?? [];
  const status = $<HTMLElement>(`codegen-status-${svcName}`);
  const out    = $<HTMLElement>(`codegen-output-${svcName}`);
  const files  = $<HTMLElement>(`codegen-files-${svcName}`);
  if (status) { status.textContent = 'Generating…'; status.className = 'badge badge-amber'; }
  if (out)   out.style.display = '';
  if (files) files.innerHTML = '';

  const fileBlocks: Record<string, HTMLElement> = {};

  try {
    const res = await fetch('/transform/codegen', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ service: svcName, programs, framework: fw, cloud }),
      signal: sig(),
    });
    if (!res.body) throw new Error('No body');
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split('\n\n'); buf = parts.pop() ?? '';
      for (const p of parts) {
        if (!p.startsWith('data:')) continue;
        try {
          const ev = JSON.parse(p.slice(5));
          if (ev.kind === 'file_start' && files) {
            const block = document.createElement('div');
            block.className = 'card-sm';
            block.innerHTML = `
              <div style="font-family:'JetBrains Mono',monospace;font-size:11px;color:#5ecdd1;margin-bottom:6px;">${escapeHtml(ev.path)}</div>
              <pre style="margin:0;background:#080e10;padding:10px;border-radius:6px;font-size:11px;line-height:1.5;max-height:340px;overflow:auto;"><code id="codegen-file-content-${cssId(ev.path)}"></code></pre>
            `;
            files.appendChild(block);
            const codeEl = block.querySelector<HTMLElement>(`#codegen-file-content-${cssId(ev.path)}`);
            if (codeEl) fileBlocks[ev.path] = codeEl;
          } else if (ev.kind === 'file_chunk') {
            const codeEl = fileBlocks[ev.path];
            if (codeEl) codeEl.textContent = (codeEl.textContent ?? '') + (ev.chunk ?? '');
          } else if (ev.kind === 'file_done') {
            /* nothing extra */
          } else if (ev.kind === 'service_done') {
            if (status) { status.textContent = `Done · ${ev.files} files`; status.className = 'badge badge-green'; }
            showToast(`${svcName} generated (${ev.files} files)`);
          }
        } catch { /* skip */ }
      }
    }
  } catch (e) {
    if (!isAbort(e)) {
      if (status) { status.textContent = 'Failed'; status.className = 'badge badge-red'; }
      showToast(`${svcName} failed: ${(e as Error).message}`, 'error');
    }
  }
}

function cssId(s: string): string {
  return s.replace(/[^a-zA-Z0-9]/g, '_');
}

async function generateAllServices(): Promise<void> {
  if (!_txServicesFromArch.length) {
    // Render defaults so the user has something to generate
    renderCodegenServicesPanel();
  }
  const services = _txServicesFromArch.length ? _txServicesFromArch : [
    { name: 'AuthService', programs: [] },
    { name: 'UserService', programs: [] },
    { name: 'AccountService', programs: [] },
    { name: 'CardService', programs: [] },
    { name: 'TransactionService', programs: [] },
    { name: 'ReportingService', programs: [] },
  ];
  await Promise.all(services.map(s => generateServiceCode(s.name)));
}

// ── Migration Mapping — expand/collapse detail rows ───────────────────────────
function toggleMappingDetail(idx: number): void {
  const row = document.getElementById(`mapping-detail-${idx}`);
  const btn = document.getElementById(`mapping-expand-btn-${idx}`);
  if (!row) return;
  const open = row.style.display === 'none' || row.style.display === '';
  // 'none' = hidden, '' = hidden (initial), anything else = visible
  const isHidden = row.style.display === 'none' || row.style.display === '';
  row.style.display = isHidden ? 'table-row' : 'none';
  if (btn) btn.innerHTML = isHidden ? '&#x25BC;' : '&#x25B6;';
}

// ── Codegen Export & GitHub Push ──────────────────────────────────────────────
async function exportCodegenZip(): Promise<void> {
  const select  = $<HTMLSelectElement>('codegen-export-service');
  const svcName = select?.value ?? 'all';
  const fw      = ($<HTMLSelectElement>('transform-framework'))?.value ?? 'Spring Boot';
  const cloud   = ($<HTMLSelectElement>('transform-cloud'))?.value ?? 'AWS';

  const defaultServices = [
    { name: 'AuthService', programs: [] as string[] },
    { name: 'UserService', programs: [] as string[] },
    { name: 'AccountService', programs: [] as string[] },
    { name: 'CardService', programs: [] as string[] },
    { name: 'TransactionService', programs: [] as string[] },
    { name: 'ReportingService', programs: [] as string[] },
  ];
  const allSvcs = _txServicesFromArch.length ? _txServicesFromArch : defaultServices;

  const toExport = svcName === 'all'
    ? allSvcs
    : [allSvcs.find(s => s.name === svcName) ?? { name: svcName, programs: [] as string[] }];

  showToast('Preparing ZIP archive…');
  for (const svc of toExport) {
    try {
      const res = await fetch('/transform/codegen/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service: svc.name, programs: svc.programs, framework: fw, cloud }),
        signal: sig(),
      });
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement('a');
      a.href = url;
      a.download = `${svc.name}-export.zip`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      if (!isAbort(e)) showToast(`Export failed for ${svc.name}: ${(e as Error).message}`, 'error');
    }
  }
  showToast('ZIP download started');
}

function openGithubPushModal(): void {
  const modal = document.getElementById('github-push-modal');
  if (modal) modal.style.display = 'flex';
  const select   = $<HTMLSelectElement>('codegen-export-service');
  const svc      = select?.value ?? 'all';
  const repoInput = $<HTMLInputElement>('gh-repo-name');
  if (repoInput && !repoInput.value) {
    repoInput.value = `carddemo-${svc === 'all' ? 'migration' : svc.toLowerCase()}`;
  }
  const statusEl = document.getElementById('gh-push-status');
  if (statusEl) statusEl.innerHTML = '';
}

function closeGithubPushModal(): void {
  const modal = document.getElementById('github-push-modal');
  if (modal) modal.style.display = 'none';
}

async function pushToGithub(): Promise<void> {
  const token     = ($<HTMLInputElement>('gh-token'))?.value?.trim() ?? '';
  const repoName  = ($<HTMLInputElement>('gh-repo-name'))?.value?.trim() ?? '';
  const repoDesc  = ($<HTMLInputElement>('gh-repo-desc'))?.value?.trim() ?? '';
  const isPrivate = ($<HTMLInputElement>('gh-private'))?.checked ?? true;
  const statusEl  = document.getElementById('gh-push-status');
  const select    = $<HTMLSelectElement>('codegen-export-service');
  const svcName   = select?.value ?? 'all';
  const fw        = ($<HTMLSelectElement>('transform-framework'))?.value ?? 'Spring Boot';
  const cloud     = ($<HTMLSelectElement>('transform-cloud'))?.value ?? 'AWS';

  if (!token)    { if (statusEl) statusEl.textContent = 'GitHub token is required.'; return; }
  if (!repoName) { if (statusEl) statusEl.textContent = 'Repository name is required.'; return; }

  const defaultServices = [
    { name: 'AuthService', programs: [] as string[] },
    { name: 'UserService', programs: [] as string[] },
    { name: 'AccountService', programs: [] as string[] },
    { name: 'CardService', programs: [] as string[] },
    { name: 'TransactionService', programs: [] as string[] },
    { name: 'ReportingService', programs: [] as string[] },
  ];
  const allSvcs = _txServicesFromArch.length ? _txServicesFromArch : defaultServices;
  const toExport = svcName === 'all'
    ? allSvcs
    : [allSvcs.find(s => s.name === svcName) ?? { name: svcName, programs: [] as string[] }];

  if (statusEl) statusEl.innerHTML = '<span style="color:#fbbf24;">Pushing…</span>';

  for (const svc of toExport) {
    try {
      const actualRepo = svcName === 'all' ? `${repoName}-${svc.name.toLowerCase()}` : repoName;
      const res = await fetch('/transform/codegen/github-push', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          service: svc.name,
          programs: svc.programs,
          framework: fw,
          cloud,
          github_token: token,
          repo_name: actualRepo,
          repo_description: repoDesc || `Migrated from COBOL CardDemo — ${svc.name}`,
          make_private: isPrivate,
        }),
        signal: sig(),
      });
      const data = await res.json() as { success: boolean; repo_url?: string; message?: string };
      if (data.success) {
        if (statusEl) statusEl.innerHTML += `<br><span style="color:#34d399;">&#x2713; ${escapeHtml(svc.name)}: <a href="${escapeHtml(data.repo_url ?? '')}" target="_blank" rel="noopener" style="color:#5ecdd1;">${escapeHtml(data.repo_url ?? '')}</a></span>`;
        showToast(`Pushed ${svc.name} to ${data.repo_url ?? ''}`);
      } else {
        if (statusEl) statusEl.innerHTML += `<br><span style="color:#f87171;">&#x2717; ${escapeHtml(svc.name)}: ${escapeHtml(data.message ?? 'Unknown error')}</span>`;
      }
    } catch (e) {
      if (!isAbort(e)) {
        if (statusEl) statusEl.innerHTML += `<br><span style="color:#f87171;">Error: ${escapeHtml((e as Error).message)}</span>`;
      }
    }
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

let _cfgZoomLevel = 1;

function cfgZoom(factor: number): void {
  const container = $<HTMLElement>('cfg-container');
  if (!container) return;
  const svgEl = container.querySelector<SVGSVGElement>('svg');
  if (!svgEl) return;
  if (factor === 0) {
    _cfgZoomLevel = 1;
  } else {
    _cfgZoomLevel = Math.min(5, Math.max(0.2, _cfgZoomLevel * factor));
  }
  svgEl.style.transform = `scale(${_cfgZoomLevel})`;
  svgEl.style.transformOrigin = 'top left';
  const info = $<HTMLElement>('cfg-info');
  if (info && info.dataset.base) {
    info.textContent = info.dataset.base + ` · zoom ${Math.round(_cfgZoomLevel * 100)}%`;
  }
}

// CFG Visualization
async function loadCFG(prog: string): Promise<void> {
  const container = $<HTMLElement>('cfg-container');
  if (!container) return;
  _cfgZoomLevel = 1;
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
    const wrapper = document.createElement('div');
    wrapper.style.cssText = 'display:block;transform-origin:top left;width:100%;';
    wrapper.innerHTML = svg;
    container.appendChild(wrapper);
    const svgEl = wrapper.querySelector<SVGSVGElement>('svg');
    if (svgEl) {
      svgEl.style.maxWidth = 'none';
      svgEl.style.width = '100%';
      svgEl.style.height = 'auto';
      svgEl.style.minHeight = '500px';
    }
    const infoEl = $<HTMLElement>('cfg-info');
    const base = `${prog} — ${data.nodes?.length ?? 0} nodes · ${data.edges?.length ?? 0} edges`;
    if (infoEl) { infoEl.textContent = base + ' · zoom 100%'; infoEl.dataset.base = base; }
  } catch(e) {
    if (!isAbort(e)) container.innerHTML = `<div style="color:#f87171;padding:20px;">Error: ${(e as Error).message}</div>`;
  }
}

// ── Pipeline Run History ──────────────────────────────────────────────────────
async function loadRunHistory(): Promise<void> {
  const el = $<HTMLElement>('run-history-table');
  if (!el) return;
  try {
    const data = await apiFetch<{ runs: any[] }>('/pipeline/runs');
    const runs = data.runs ?? [];
    if (!runs.length) {
      el.innerHTML = '<div style="color:var(--muted);font-size:13px;text-align:center;padding:24px;">No pipeline runs recorded yet.</div>';
      return;
    }
    el.innerHTML = `<table style="width:100%;border-collapse:collapse;">
      <thead><tr>
        <th style="text-align:left;padding:8px 10px;font-size:11px;color:var(--muted);border-bottom:1px solid var(--border);">Run ID</th>
        <th style="text-align:left;padding:8px 10px;font-size:11px;color:var(--muted);border-bottom:1px solid var(--border);">Started</th>
        <th style="text-align:left;padding:8px 10px;font-size:11px;color:var(--muted);border-bottom:1px solid var(--border);">Duration</th>
        <th style="text-align:left;padding:8px 10px;font-size:11px;color:var(--muted);border-bottom:1px solid var(--border);">Status</th>
        <th style="text-align:left;padding:8px 10px;font-size:11px;color:var(--muted);border-bottom:1px solid var(--border);">Programs</th>
        <th style="text-align:left;padding:8px 10px;font-size:11px;color:var(--muted);border-bottom:1px solid var(--border);">Corpus</th>
        <th style="padding:8px 10px;border-bottom:1px solid var(--border);"></th>
      </tr></thead>
      <tbody>
        ${runs.map(r => {
          const started = r.started_at ? new Date(r.started_at * 1000).toLocaleString() : '—';
          const dur = (r.started_at && r.completed_at)
            ? `${Math.round(r.completed_at - r.started_at)}s` : '—';
          const statusColor = r.status === 'completed' ? '#4ade80' : r.status === 'running' ? '#fbbf24' : '#f87171';
          const programs = r.stats?.programs ?? '—';
          const corpus = (r.corpus ?? '').split('/').slice(-2).join('/') || r.corpus || '—';
          return `<tr style="border-bottom:1px solid var(--border);">
            <td style="padding:8px 10px;font-size:12px;font-family:monospace;color:var(--muted);">${r.id}</td>
            <td style="padding:8px 10px;font-size:12px;">${started}</td>
            <td style="padding:8px 10px;font-size:12px;color:var(--muted);">${dur}</td>
            <td style="padding:8px 10px;"><span style="font-size:11px;font-weight:600;color:${statusColor};">${r.status.toUpperCase()}</span></td>
            <td style="padding:8px 10px;font-size:12px;font-weight:600;color:#5ecdd1;">${programs}</td>
            <td style="padding:8px 10px;font-size:11px;color:var(--muted);" title="${r.corpus ?? ''}">${corpus}</td>
            <td style="padding:8px 10px;">
              <button onclick="deleteRun('${r.id}')" class="btn btn-secondary" style="font-size:11px;padding:3px 8px;color:#f87171;border-color:#f87171;">Remove</button>
            </td>
          </tr>`;
        }).join('')}
      </tbody>
    </table>`;
  } catch(e) {
    if (!isAbort(e) && el) el.innerHTML = '<div style="color:#f87171;padding:12px;">Failed to load run history.</div>';
  }
}

async function deleteRun(runId: string): Promise<void> {
  if (!confirm(`Remove run ${runId} from history? This only removes the log entry — artifact data is not deleted.`)) return;
  try {
    await fetch(`/pipeline/runs/${runId}`, { method: 'DELETE' });
    await loadRunHistory();
    showToast('Run removed from history');
  } catch(e) { showToast('Failed to remove run', 'error'); }
}

async function clearAllPipelineData(): Promise<void> {
  if (!confirm('⚠ This will DELETE ALL parsed artifact data (programs, call graphs, business rules, etc.) and clear run history. The dashboard will reset to zero. Are you sure?')) return;
  try {
    const r = await fetch('/pipeline/clear-db', { method: 'POST' });
    const data = await r.json();
    if (data.ok) {
      showToast('All artifact data cleared — dashboard will reset', 'ok');
      await loadRunHistory();
      void loadDashboard();
    } else {
      showToast('Clear failed: ' + (data.error ?? 'unknown'), 'error');
    }
  } catch(e) { showToast('Clear failed: ' + (e as Error).message, 'error'); }
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
  // Pass 1: Extract fenced code blocks (protect from further processing)
  const blocks: string[] = [];
  let s = md.replace(/```(\w*)\n?([\s\S]*?)```/g, (_m, lang: string, content: string) => {
    const idx = blocks.length;
    const trimmed = content.replace(/\n$/, '');
    if (lang.toLowerCase() === 'mermaid') {
      blocks.push(`<div class="mermaid" style="background:var(--surface);padding:16px;border-radius:8px;border:1px solid var(--border);margin:16px 0;overflow-x:auto;">${trimmed}</div>`);
    } else {
      blocks.push(`<pre style="background:var(--surface2);padding:14px;border-radius:6px;overflow-x:auto;border:1px solid var(--border);margin:12px 0;font-size:11px;"><code>${escapeHtml(trimmed)}</code></pre>`);
    }
    return `\x00BLOCK${idx}\x00`;
  });

  // Pass 2: Tables (pipe tables with separator row)
  s = s.replace(/(\|[^\n]+\|\n\|[-| :]+\|\n(?:\|[^\n]+\|\n?)+)/g, (tableMatch: string) => {
    const tableLines = tableMatch.trim().split('\n');
    const headers = tableLines[0].split('|').filter((_c, i, arr) => i > 0 && i < arr.length - 1).map(h => h.trim());
    const dataRows = tableLines.slice(2);
    let html = '<div style="overflow-x:auto;margin:12px 0;"><table style="border-collapse:collapse;width:100%;font-size:12px;">';
    html += '<thead><tr>' + headers.map(h => `<th style="background:var(--surface2);color:#5ecdd1;padding:6px 10px;border:1px solid var(--border);text-align:left;">${h}</th>`).join('') + '</tr></thead>';
    html += '<tbody>';
    for (const row of dataRows) {
      if (!row.trim()) continue;
      const cells = row.split('|').filter((_c, i, arr) => i > 0 && i < arr.length - 1).map(c => c.trim());
      html += '<tr>' + cells.map(c => `<td style="padding:6px 10px;border:1px solid var(--border);word-break:break-word;max-width:300px;">${c}</td>`).join('') + '</tr>';
    }
    html += '</tbody></table></div>';
    return html;
  });

  // Pass 3: Horizontal rules
  s = s.replace(/^---$/gm, '<hr style="border:none;border-top:1px solid var(--border);margin:20px 0;">');

  // Pass 4: Headings H1-H4 (most specific first)
  s = s.replace(/^#### (.+)$/gm, '<h4 style="color:#94a3b8;font-size:11px;font-weight:600;margin:10px 0 4px;text-transform:uppercase;letter-spacing:.06em;">$1</h4>');
  s = s.replace(/^### (.+)$/gm, '<h3 style="color:#60c8fa;font-size:13px;font-weight:600;margin:16px 0 6px;">$1</h3>');
  s = s.replace(/^## (.+)$/gm, '<h2 style="color:#5ecdd1;font-size:15px;font-weight:700;margin:22px 0 8px;border-bottom:1px solid var(--border);padding-bottom:5px;">$1</h2>');
  s = s.replace(/^# (.+)$/gm, '<h1 style="color:#5ecdd1;font-size:20px;font-weight:800;margin:28px 0 12px;padding-bottom:8px;border-bottom:2px solid var(--ust-teal);">$1</h1>');

  // Pass 5: Blockquotes
  s = s.replace(/^> (.+)$/gm, '<blockquote style="border-left:3px solid var(--ust-teal);margin:8px 0;padding:6px 14px;color:var(--muted);font-style:italic;background:rgba(0,110,116,.06);border-radius:0 4px 4px 0;">$1</blockquote>');

  // Pass 6: Bold/italic/inline-code
  s = s.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong style="color:var(--text);">$1</strong>');
  s = s.replace(/\*(.+?)\*/g, '<em style="color:var(--muted);">$1</em>');
  s = s.replace(/`([^`\x00]+)`/g, '<code style="background:var(--surface2);padding:2px 5px;border-radius:3px;font-size:12px;">$1</code>');

  // Pass 7: Unordered lists (wrap consecutive items)
  s = s.replace(/((?:^[*-] .+\n?)+)/gm, (block: string) => {
    const items = block.trim().split('\n').map(line => {
      const text = line.replace(/^[*-] /, '');
      return `<li style="display:flex;gap:8px;margin:3px 0;"><span style="color:#5ecdd1;flex-shrink:0;">•</span><span>${text}</span></li>`;
    }).join('');
    return `<ul style="list-style:none;padding-left:16px;margin:8px 0;">${items}</ul>`;
  });

  // Pass 7b: Ordered lists (wrap consecutive items)
  s = s.replace(/((?:^\d+\. .+\n?)+)/gm, (block: string) => {
    const items = block.trim().split('\n').map(line => {
      const m = line.match(/^(\d+)\. (.+)/);
      if (!m) return '';
      return `<li style="display:flex;gap:8px;margin:3px 0;"><span style="color:#fbbf24;min-width:18px;">${m[1]}.</span><span>${m[2]}</span></li>`;
    }).join('');
    return `<ol style="list-style:none;padding-left:16px;margin:8px 0;">${items}</ol>`;
  });

  // Pass 8: Paragraphs and line breaks
  s = s.replace(/\n\n/g, '</p><p style="margin:8px 0;line-height:1.7;">');
  s = s.replace(/\n/g, '<br>');
  s = `<p style="margin:8px 0;line-height:1.7;">${s}</p>`;

  // Pass 9: Re-inject code blocks
  s = s.replace(/\x00BLOCK(\d+)\x00/g, (_m: string, idx: string) => blocks[parseInt(idx, 10)]);

  return s;
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

// ── Theme toggle ──────────────────────────────────────────────────────────────
function toggleTheme(): void {
  const html = document.documentElement;
  const isLight = html.getAttribute('data-theme') === 'light';
  const next = isLight ? 'dark' : 'light';
  html.setAttribute('data-theme', next);
  localStorage.setItem('cobol-theme', next);
  // Swap hljs theme stylesheet
  const link = document.getElementById('hljs-theme') as HTMLLinkElement | null;
  if (link) {
    link.href = next === 'light'
      ? 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-light.min.css'
      : 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css';
  }
  // Update toggle button icon: moon in light mode, sun in dark mode
  const iconWrap = document.getElementById('theme-icon');
  if (iconWrap) {
    if (next === 'light') {
      iconWrap.innerHTML = `<svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`;
    } else {
      iconWrap.innerHTML = `<svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>`;
    }
  }
}

function applyStoredTheme(): void {
  const saved = localStorage.getItem('cobol-theme');
  if (saved === 'light') {
    document.documentElement.setAttribute('data-theme', 'light');
    const link = document.getElementById('hljs-theme') as HTMLLinkElement | null;
    if (link) link.href = 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-light.min.css';
    const iconWrap = document.getElementById('theme-icon');
    if (iconWrap) iconWrap.innerHTML = `<svg width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`;
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
  generateComprehensiveSpec,
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
  initKnowledgeGraph,
  onKGNodeClick,
  closeKGPane,
  explainKGNode,
  cfgZoom,
  loadRunHistory,
  deleteRun,
  clearAllPipelineData,
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
  loadCopybooks,
  filterCopybooks,
  openCopybookDetail,
  closeCpyDetail,
  loadAgentLlms,
  saveAgentLlms,
  resetAgentLlms,
  onAgentLlmProviderChange,
  onAgentLlmModelChange,
  switchTxTab,
  switchTxSubTab,
  loadTxArchitecture,
  archZoom,
  archSetLevel,
  loadServiceDetail,
  closeServiceDetail,
  switchSvcTab,
  loadTxPlan,
  acceptPlanStep,
  rejectPlanStep,
  editPlanStep,
  cancelEditPlanStep,
  saveEditPlanStep,
  loadTxComprehensiveSpecs,
  downloadComprehensiveSpec,
  generateServiceCode,
  generateAllServices,
  toggleCodegenOutput,
  toggleMappingDetail,
  exportCodegenZip,
  openGithubPushModal,
  closeGithubPushModal,
  pushToGithub,
  loadLayersPage,
  scrollToLayer,
  loadSourceCode,
  lxBrowse,
  lxClose,
  zoomDiagram,
  toggleTheme,
});

// ── Init ──────────────────────────────────────────────────────────────────────
applyStoredTheme();
void checkHealth();
setInterval(() => { void checkHealth(); }, 30_000);
void loadDashboard();
