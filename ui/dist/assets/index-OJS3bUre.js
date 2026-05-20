(function(){const a=document.createElement("link").relList;if(a&&a.supports&&a.supports("modulepreload"))return;for(const n of document.querySelectorAll('link[rel="modulepreload"]'))o(n);new MutationObserver(n=>{for(const r of n)if(r.type==="childList")for(const d of r.addedNodes)d.tagName==="LINK"&&d.rel==="modulepreload"&&o(d)}).observe(document,{childList:!0,subtree:!0});function e(n){const r={};return n.integrity&&(r.integrity=n.integrity),n.referrerPolicy&&(r.referrerPolicy=n.referrerPolicy),n.crossOrigin==="use-credentials"?r.credentials="include":n.crossOrigin==="anonymous"?r.credentials="omit":r.credentials="same-origin",r}function o(n){if(n.ep)return;n.ep=!0;const r=e(n);fetch(n.href,r)}})();let L=[],_="",O="",D="",C=[],v=[],W="ast",J=!1,S=null,R=null,H=null,P=null,I=null,j=null,A=null,T=new AbortController;function K(){T.abort(),T=new AbortController}function N(){return T.signal}async function c(t,a={}){const e=await fetch(t,{...a,signal:N()});if(!e.ok){const o=await e.text().catch(()=>e.statusText);throw new Error(o)}return e.json()}function y(t){return t instanceof Error&&t.name==="AbortError"}function s(t){return document.getElementById(t)}function m(t,a="ok"){const e=s("toast");e.textContent=t,e.style.background=a==="error"?"#ef4444":"var(--ust-teal)",e.style.display="block",setTimeout(()=>{e.style.display="none"},2200)}function ye(t){K(),document.querySelectorAll(".nav-item").forEach(r=>r.classList.remove("active"));const a=document.querySelector(`.nav-item[onclick="navigate('${t}')"]`);a&&a.classList.add("active"),document.querySelectorAll('section[id^="page-"]').forEach(r=>{r.style.display="none"});const e=document.getElementById(`page-${t}`);e&&(e.style.display="",e.classList.add("fade-in"));const o={dashboard:"Dashboard",pipeline:"Run Pipeline",programs:"Program Explorer",visualizations:"Visualizations",diagrams:"Diagrams",spec:"Spec Generator",emit:"Java Emitter",langgraph:"LangGraph",coverage:"Coverage Report",risks:"Risk Register",settings:"Settings",layers:"Layer Explorer"},n=s("page-title");n&&(n.textContent=o[t]??t),t==="dashboard"&&U(),t==="programs"&&(V(),E()),t==="visualizations"&&ie(),t==="diagrams"&&Q("call_graph",document.querySelector(".diag-btn")),t==="spec"&&(E(),ke()),t==="emit"&&Z(),t==="coverage"&&Ne(),t==="risks"&&Ue(),t==="settings"&&ne(),t==="layers"&&ge()}async function B(){try{const t=await c("/health"),a=s("api-status"),e=s("db-badge"),o=t.db_ready?"#4ade80":"#fbbf24",n=t.db_ready?"API + DB ready":"API ready — no DB";a&&(a.innerHTML=`<span style="width:7px;height:7px;border-radius:50%;background:${o};display:inline-block;flex-shrink:0;"></span> <span>${n}</span>`),e&&(e.textContent=t.db_ready?"DB: ready":"DB: run pipeline first",e.style.color=o)}catch{const t=s("api-status");t&&(t.innerHTML='<span style="width:7px;height:7px;border-radius:50%;background:#f87171;display:inline-block;flex-shrink:0;"></span> <span>API unreachable</span>')}}async function U(){var t,a;try{const e=await c("/stats");["programs","paragraphs","data_items","statements","business_rules","call_edges","risks"].forEach(d=>{const i=s(`s-${d}`);i&&(i.textContent=(e[d]??0).toLocaleString())});const o=s("s-coverage_pct");o&&(o.textContent=(e.coverage_pct??0)+"%");const n=(t=s("coverage-chart"))==null?void 0:t.getContext("2d");n&&(S&&S.destroy(),S=new Chart(n,{type:"doughnut",data:{labels:["Parsed OK","Failed"],datasets:[{data:[e.ok_files,e.total_files-e.ok_files],backgroundColor:["#4ade80","#f87171"],borderWidth:0}]},options:{plugins:{legend:{labels:{color:"#7e8c9a",font:{size:12}}}},cutout:"70%"}}));const r=(a=s("layer-chart"))==null?void 0:a.getContext("2d");r&&(R&&R.destroy(),R=new Chart(r,{type:"bar",data:{labels:["Programs","Paragraphs","Data Items","Stmts","Bus. Rules","Call Edges","Risks"],datasets:[{data:[e.programs,e.paragraphs,e.data_items,e.statements,e.business_rules,e.call_edges,e.risks],backgroundColor:["#006e74","#0097ab","#009ddc","#00afd9","#4ade80","#fbbf24","#f87171"],borderRadius:4,borderWidth:0}]},options:{plugins:{legend:{display:!1}},scales:{x:{ticks:{color:"#7e8c9a",font:{size:11}},grid:{color:"#2b333f"}},y:{ticks:{color:"#7e8c9a"},grid:{color:"#2b333f"}}}}})),G(e)}catch(e){y(e)||(console.warn("Dashboard unavailable:",e.message),G({}))}}function G(t){const a=[{pct:20,label:"Parse Coverage (honest reporting)",done:(t.total_files??0)>0},{pct:25,label:"Artifact Contract (Layers 1–7, UUID links)",done:(t.paragraphs??0)>0},{pct:15,label:"Spec Generation Demo (COTRN02C paragraph)",done:(t.business_rules??0)>0},{pct:15,label:"Forward Engineering (IR → Java, COUSR0xC)",done:(t.programs??0)>0},{pct:10,label:"Engineering Quality (tests, UUID stability)",done:!0},{pct:5,label:"Performance (parallel batch, WAL SQLite)",done:!0},{pct:5,label:"Migration Risk Register (severity-rated)",done:(t.risks??0)>0},{pct:5,label:"LangGraph Orchestration (bonus)",done:!0}],e=s("rubric-items");e&&(e.innerHTML=a.map(o=>`
    <div style="display:flex;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid var(--border);">
      <span style="width:38px;text-align:right;font-size:12px;color:var(--muted);font-weight:600;">${o.pct}%</span>
      <div style="flex:1;">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:5px;">
          <span style="font-size:13px;">${o.label}</span>
          <span class="badge ${o.done?"badge-green":"badge-amber"}">${o.done?"✓ Ready":"⏳ Pending"}</span>
        </div>
        <div class="progress-bar"><div class="progress-fill" style="width:${o.done?100:15}%"></div></div>
      </div>
    </div>`).join(""))}async function V(){var a;const t=((a=s("prog-search"))==null?void 0:a.value)??"";try{const e=await c(`/programs?q=${encodeURIComponent(t)}&limit=200`);L=e.items??[];const o=s("prog-count");o&&(o.textContent=`${e.total} programs`),fe(L)}catch(e){if(!y(e)){const o=s("programs-body");o&&(o.innerHTML='<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:40px;">Run the pipeline to populate programs.</td></tr>')}}}function fe(t){const a=s("programs-body");if(a){if(!t.length){a.innerHTML='<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:40px;">No programs found.</td></tr>';return}a.innerHTML=t.map(e=>`
    <tr style="cursor:pointer;" onclick="openProgram('${e.name}')">
      <td><span style="font-weight:700;color:#5ecdd1;">${e.name??"—"}</span></td>
      <td><span style="font-size:11px;color:var(--muted);">${(e.source_file??"").split("/").pop()}</span></td>
      <td><span class="badge badge-sky">${e.para_count??0}</span></td>
      <td>${e.item_count??0}</td>
      <td>${e.rule_count??0}</td>
      <td>${(e.risk_count??0)>0?`<span class="badge badge-red">${e.risk_count}</span>`:'<span class="badge badge-gray">0</span>'}</td>
      <td><span style="font-size:12px;color:#5ecdd1;">View →</span></td>
    </tr>`).join("")}}async function me(t){const a=s("programs-table-wrap"),e=s("prog-detail"),o=s("prog-search"),n=s("prog-count"),r=s("detail-name");a&&(a.style.display="none"),e&&(e.style.display=""),o&&(o.style.display="none"),n&&(n.style.display="none"),r&&(r.textContent=t);try{const d=await c(`/programs/${encodeURIComponent(t)}/detail`);he(d.paragraphs),ve(d.data_items),be(d.call_graph),xe(d.business_rules),we(d.file_io),$e(d.risks),_e(t)}catch(d){y(d)||console.warn(d)}}function ue(){const t=s("programs-table-wrap"),a=s("prog-detail"),e=s("prog-search"),o=s("prog-count");t&&(t.style.display=""),a&&(a.style.display="none"),e&&(e.style.display=""),o&&(o.style.display=""),Y("paragraphs")}function Y(t){document.querySelectorAll(".tab-panel").forEach(r=>{r.style.display="none"}),document.querySelectorAll(".tab").forEach(r=>r.classList.remove("active"));const a=s(`tab-${t}`);a&&(a.style.display="");const e={paragraphs:0,dataitems:1,callgraph:2,bizrules:3,fileio:4,progrisk:5,source:6},o=document.querySelectorAll(".tab"),n=e[t];n!==void 0&&o[n]&&o[n].classList.add("active")}function he(t){const a=s("para-tbody");a&&(a.innerHTML=(t??[]).map(e=>`<tr><td style="font-weight:600;color:var(--ust-sky);">${e.name}</td><td>${e.start_line}</td><td>${e.end_line}</td></tr>`).join("")||'<tr><td colspan="3" style="color:var(--muted);">None</td></tr>')}function ve(t){const a=s("di-tbody");a&&(a.innerHTML=(t??[]).map(e=>`
    <tr>
      <td style="font-weight:500;">${e.name}</td>
      <td><span class="badge badge-gray">${e.level}</span></td>
      <td><code style="font-size:11px;color:var(--ust-sky);">${e.pic??""}</code></td>
      <td>${e.usage??"DISPLAY"}</td>
      <td><span class="badge ${e.canonical_kind==="decimal"?"badge-orange":e.canonical_kind==="alpha"?"badge-sky":"badge-gray"}">${e.canonical_kind??""}</span></td>
      <td>${e.precision??""}</td><td>${e.scale??""}</td>
    </tr>`).join("")||'<tr><td colspan="7" style="color:var(--muted);">None</td></tr>')}function be(t){const a=s("cg-tbody");a&&(a.innerHTML=(t??[]).map(e=>`
    <tr><td style="font-weight:500;">${e.callee_name}</td>
    <td><span class="badge badge-sky">${e.call_type}</span></td>
    <td>${e.is_resolved?'<span class="badge badge-green">✓ resolved</span>':'<span class="badge badge-amber">unresolved</span>'}</td>
    </tr>`).join("")||'<tr><td colspan="3" style="color:var(--muted);">No external calls</td></tr>')}function xe(t){const a=s("br-tbody");a&&(a.innerHTML=(t??[]).map(e=>`<tr><td>${e.line}</td><td><span class="badge badge-orange">${e.kind}</span></td>
    <td style="max-width:300px;font-size:12px;color:var(--muted);">${(e.predicate_raw??"").slice(0,80)}</td>
    <td style="font-size:12px;">${(e.then_summary??"").slice(0,60)}</td>
    <td style="font-size:12px;">${(e.else_summary??"").slice(0,60)}</td></tr>`).join("")||'<tr><td colspan="5" style="color:var(--muted);">None</td></tr>')}function we(t){const a=s("fio-tbody");a&&(a.innerHTML=(t??[]).map(e=>`<tr><td style="font-weight:500;">${e.file_name}</td>
    <td><span class="badge ${e.operation==="WRITE"||e.operation==="REWRITE"?"badge-amber":"badge-sky"}">${e.operation}</span></td>
    <td style="font-size:12px;color:var(--muted);">${e.record_copybook??""}</td></tr>`).join("")||'<tr><td colspan="3" style="color:var(--muted);">No file I/O</td></tr>')}function $e(t){const a=s("risk-tbody");a&&(a.innerHTML=(t??[]).map(e=>`<tr><td><span class="badge badge-orange">${e.kind}</span></td>
    <td><span class="sev-${e.severity}" style="font-weight:700;">${e.severity}</span></td>
    <td style="font-size:12px;color:var(--muted);">${e.note??""}</td>
    <td>${e.line??""}</td></tr>`).join("")||'<tr><td colspan="4" style="color:var(--muted);">No risks detected</td></tr>')}async function _e(t){const a=s("prog-source-container");if(a){a.innerHTML='<div style="color:var(--muted);text-align:center;padding:20px;">Loading source…</div>';try{const e=await c(`/programs/${encodeURIComponent(t)}/source`);a.innerHTML=`
      <div style="margin-bottom:8px;font-size:12px;color:var(--muted);">${e.line_count} lines</div>
      <pre style="margin:0;"><code class="language-cobol" style="font-size:11px;line-height:1.5;">${pe(e.content)}</code></pre>`;const o=a.querySelector("code");o&&hljs.highlightElement(o)}catch{}}}mermaid.initialize({startOnLoad:!1,theme:"dark",darkMode:!0,themeVariables:{primaryColor:"#003c51",primaryTextColor:"#f0f4f4",lineColor:"#0097ab",secondaryColor:"#1c242c",tertiaryColor:"#252c32"}});async function Q(t,a){document.querySelectorAll(".diag-btn").forEach(i=>i.classList.remove("active")),a&&a.classList.add("active");const e={call_graph:"Call Graph",transaction_flow:"Transaction Flow",jcl_job_chain:"JCL Job Chain",file_io_graph:"File I/O Graph"},o=s("diag-title");o&&(o.textContent=e[t]??t);const n=s("diag-loading"),r=s("diag-empty"),d=s("diag-render");n&&(n.style.display=""),r&&(r.style.display="none"),d&&(d.innerHTML="");try{_=(await c(`/diagrams/${t}`)).content,n&&(n.style.display="none");const l=s("diag-source");if(l&&(l.textContent=_,hljs.highlightElement(l)),d){const p="mmd-"+Date.now(),{svg:g}=await mermaid.render(p,_);d.innerHTML=g;const f=d.querySelector("svg");f&&(f.style.maxWidth="100%")}}catch(i){y(i)||(n&&(n.style.display="none"),r&&(r.style.display=""))}}function Ce(){navigator.clipboard.writeText(_).then(()=>m("Copied!"))}async function E(){try{L=(await c("/programs?limit=500")).items??[],["spec-program","emit-program"].forEach(a=>{const e=s(a);if(!e)return;const o=e.value;e.innerHTML='<option value="">— select program —</option>'+L.map(n=>`<option value="${n.name}" ${n.name===o?"selected":""}>${n.name}</option>`).join("")})}catch(t){y(t)||console.warn("loadProgramDropdowns failed:",t)}}async function Z(){await E()}async function ke(){try{const t=await c("/settings"),a=t.provider==="gemini"||t.llm_provider==="gemini"?t.gemini_model:t.openai_model,e=s("spec-model-badge");e&&(e.textContent=`${t.llm_provider||t.provider} / ${a}`)}catch{}}async function X(){var e,o;const t=((e=s("spec-program"))==null?void 0:e.value)??"",a=((o=s("spec-scope"))==null?void 0:o.value)??"program";if(!t){const n=s("spec-uuid");n&&(n.value="");return}if(a==="program")try{const n=await c(`/programs/${encodeURIComponent(t)}`),r=s("spec-uuid");r&&(r.value=n.uuid??"")}catch{}else await Te(t)}async function Le(){var e;const t=((e=s("spec-scope"))==null?void 0:e.value)??"program",a=s("spec-para-wrap");a&&(a.style.display=t==="paragraph"?"":"none"),await X()}async function Te(t){try{const a=await c(`/programs/${encodeURIComponent(t)}/detail`),e=s("spec-paragraph");if(!e)return;e.innerHTML=(a.paragraphs??[]).map(n=>`<option value="${n.uuid}">${n.name} (L${n.start_line})</option>`).join("");const o=s("spec-uuid");o&&e.options[0]&&(o.value=e.options[0].value),e.onchange=()=>{o&&(o.value=e.value)}}catch{}}async function Ee(){var d,i;const t=((d=s("spec-uuid"))==null?void 0:d.value)??"",a=((i=s("spec-scope"))==null?void 0:i.value)??"program";if(!t){m("Select a program first","error");return}const e=s("spec-loading"),o=s("spec-output"),n=s("spec-btn"),r=s("spec-grounding");e&&(e.style.display=""),o&&(o.innerHTML=""),r&&(r.textContent=""),n&&(n.disabled=!0);try{const l=await c("/generate-spec",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({uuid:t,scope:a})});D=l.spec??"",o&&(o.textContent=D),r&&l.grounding_score!==void 0&&(r.textContent=`Grounding: ${Math.round(l.grounding_score*100)}%`)}catch(l){!y(l)&&o&&(o.innerHTML=`<span style="color:#f87171;">Error: ${l.message}</span>`)}finally{e&&(e.style.display="none"),n&&(n.disabled=!1)}}function Me(){navigator.clipboard.writeText(D).then(()=>m("Copied!"))}let x="";async function ze(){var d,i;const t=s("mod-btn"),a=s("mod-progress"),e=s("mod-progress-msg"),o=s("mod-result"),n=s("mod-preview"),r=((d=s("mod-use-llm"))==null?void 0:d.checked)??!1;t&&(t.disabled=!0),o&&(o.style.display="none"),a&&(a.style.display=""),e&&(e.textContent="Building holistic modernization report from ANTLR artifacts…");try{const l=await fetch("/generate-modernization-report",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({use_llm:r}),signal:N()});if(!l.ok)throw new Error(await l.text());const p=(i=l.body)==null?void 0:i.getReader(),g=new TextDecoder;let f=!1,u={};for(;!f&&p;){const $=await p.read();if(f=$.done,$.value){const z=g.decode($.value);for(const w of z.split(`
`))if(w.startsWith("data:"))try{const h=JSON.parse(w.slice(5).trim());u=h,h.event==="start"&&e&&(e.textContent=String(h.message??"Generating…")),h.event==="done"&&(f=!0)}catch{}}}if(u.event==="done"){x=(await c("/specs/MODERNIZATION_REPORT")).markdown??"";const z=u.size_kb??0,w=s("mod-result-title"),h=s("mod-result-meta");w&&(w.textContent="Report generated — CardDemo Modernization Spec"),h&&(h.textContent=`${z} KB · 10 sections · ANTLR-derived artifacts`),n&&(n.textContent=x.slice(0,3e3)+`

… (click View Report for full content)`),o&&(o.style.display=""),m("Modernization report ready!")}else u.event==="error"&&m(`Error: ${u.message}`,"error")}catch(l){y(l)||m(`Failed: ${l.message}`,"error")}finally{a&&(a.style.display="none"),t&&(t.disabled=!1)}}function Se(){const t=s("mod-preview");if(!x){m("Generate the report first","error");return}t&&(t.textContent=x,t.style.maxHeight=t.style.maxHeight==="none"?"400px":"none")}function Re(){if(!x){m("Nothing to copy yet","error");return}navigator.clipboard.writeText(x).then(()=>m("Full report copied to clipboard!"))}async function He(){var a;const t=((a=s("emit-program"))==null?void 0:a.value)??"";if(!t){m("Select a program","error");return}await ee(t)}async function Pe(t){const a=s("emit-program");a&&(a.value=t),await ee(t)}async function ee(t){const a=s("emit-loading"),e=s("emit-filename"),o=s("emit-meta");a&&(a.style.display=""),e&&(e.textContent="Java Output"),o&&(o.textContent="");try{const n=await c(`/emit-java/${encodeURIComponent(t)}`);O=n.java_source??"",e&&(e.textContent=t+".java"),o&&(o.textContent=`${n.lines} lines`);const r=s("emit-code");r&&(r.textContent=O,r.className="language-java",hljs.highlightElement(r))}catch(n){if(!y(n)){const r=s("emit-code");r&&(r.textContent=`// Error: ${n.message}`,r.className="language-text")}}finally{a&&(a.style.display="none")}}function Ie(){navigator.clipboard.writeText(O).then(()=>m("Copied!"))}const je={preprocessing:"preprocessing","copy/replace":"preprocessing","phase 1 cobol":"parsing",proleap:"parsing",jar:"parsing","layer 1 ast":"layer1","layer 1":"layer1","layer 2 symbol":"layer2","layer 2":"layer2","layer 3 cfg":"layer3","layer 3":"layer3",cfg:"layer3","def-use":"layer3","layer 4 call":"layer4","layer 4":"layer4","call graph":"layer4","layer 5 business":"layer5","layer 5":"layer5","phase 7 coverage":"layer5",coverage:"layer5"},Ae=["preprocessing","parsing","layer1","layer2","layer3","layer4","layer5"],M=new Set;function k(t){J=t;const a=s("run-btn"),e=s("cancel-btn"),o=s("pipeline-progress-card");a&&(a.disabled=t),e&&(e.style.display=t?"":"none"),o&&(o.style.display=t?"":"none"),t||(M.clear(),F(0))}function F(t){const a=s("pipeline-progress-fill"),e=s("pipeline-progress-pct");a&&(a.style.width=t+"%"),e&&(e.textContent=Math.round(t)+"%")}async function te(){var e;if(J)return;k(!0);const t=s("pipeline-log");t.innerHTML="",M.clear(),F(0);const a=((e=s("corpus-path"))==null?void 0:e.value)||"external/carddemo/app/cbl";try{const o=await fetch("/pipeline/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({corpus:a}),signal:N()});if(!o.body)throw new Error("No response body");const n=o.body.getReader(),r=new TextDecoder;let d="";for(;;){const{done:i,value:l}=await n.read();if(i)break;d+=r.decode(l,{stream:!0});const p=d.split(`

`);d=p.pop()??"";for(const g of p)if(g.startsWith("data:"))try{const f=JSON.parse(g.slice(5));q(t,f),f.kind==="done"&&k(!1)}catch{}}}catch(o){y(o)||q(t,{kind:"error",msg:o.message})}k(!1)}function Oe(){const t=s("corpus-path");t&&(t.value="external/carddemo/app/cbl"),te()}function q(t,a){const e=document.createElement("div");e.className=`log-line log-${a.kind}`;const o=a.ts?new Date(a.ts*1e3).toLocaleTimeString():"";e.textContent=`[${o}] ${a.msg}`,t.appendChild(e),t.scrollTop=t.scrollHeight;const n=(a.msg??"").toLowerCase();for(const[r,d]of Object.entries(je))if(n.includes(r)){const i=document.querySelector(`.stage-item[data-stage="${d}"] .stage-icon`);i&&(i.textContent=a.kind==="error"?"❌":"✅",M.add(d),F(M.size/Ae.length*100))}}async function De(){try{await fetch("/pipeline/cancel",{method:"POST"}),K(),T=new AbortController,k(!1),m("Pipeline cancelled")}catch(t){m("Cancel failed: "+t.message,"error")}}async function Ne(){var t,a;try{const e=await c("/reports/coverage");C=e.files??[];const o=s("cov-ok"),n=s("cov-fail"),r=s("cov-pct");o&&(o.textContent=String(e.ok_files)),n&&(n.textContent=String(e.total_files-e.ok_files)),r&&(r.textContent=e.coverage_pct+"%");const d=(t=s("cov-donut"))==null?void 0:t.getContext("2d");d&&(H&&H.destroy(),H=new Chart(d,{type:"doughnut",data:{labels:["OK","Failed"],datasets:[{data:[e.ok_files,e.total_files-e.ok_files],backgroundColor:["#4ade80","#f87171"],borderWidth:0}]},options:{plugins:{legend:{labels:{color:"#7BA8D4"}}},cutout:"70%"}}));const i=C.filter(g=>g.status!=="OK"),l={};i.forEach(g=>{l[g.status??"unknown"]=(l[g.status??"unknown"]??0)+1});const p=(a=s("cov-bar"))==null?void 0:a.getContext("2d");p&&(P&&P.destroy(),P=new Chart(p,{type:"bar",data:{labels:Object.keys(l),datasets:[{data:Object.values(l),backgroundColor:"#006e74",borderRadius:4,borderWidth:0}]},options:{plugins:{legend:{display:!1}},indexAxis:"y",scales:{x:{ticks:{color:"#7BA8D4"},grid:{color:"#0A3A80"}},y:{ticks:{color:"#7BA8D4",font:{size:11}},grid:{display:!1}}}}})),ae(C)}catch(e){y(e)||["cov-ok","cov-fail","cov-pct"].forEach(o=>{const n=s(o);n&&(n.textContent="—")})}}function ae(t){const a=s("cov-tbody");a&&(a.innerHTML=t.map(e=>`
    <tr>
      <td style="font-size:12px;color:var(--muted);">${e.source_file.split("/").pop()}</td>
      <td><span class="badge ${e.status==="OK"?"badge-green":"badge-red"}">${e.status}</span></td>
      <td style="font-size:12px;color:var(--muted);">${e.error_class??e.status??""}</td>
      <td>${e.parse_time_ms??""}</td>
    </tr>`).join("")||'<tr><td colspan="4" style="color:var(--muted);text-align:center;padding:30px;">No coverage data yet.</td></tr>')}function Be(){var a;const t=((a=s("cov-filter"))==null?void 0:a.value.toLowerCase())??"";ae(C.filter(e=>e.source_file.toLowerCase().includes(t)||(e.status??"").toLowerCase().includes(t)))}async function Ue(){var t,a;try{v=await c("/reports/risk-register")??[];const o=v.filter(u=>u.severity==="HIGH").length,n=v.filter(u=>u.severity==="MEDIUM").length,r=v.filter(u=>u.severity==="LOW").length,d=s("risk-high"),i=s("risk-med"),l=s("risk-low");d&&(d.textContent=String(o)),i&&(i.textContent=String(n)),l&&(l.textContent=String(r));const p=(t=s("risk-donut"))==null?void 0:t.getContext("2d");p&&(I&&I.destroy(),I=new Chart(p,{type:"doughnut",data:{labels:["HIGH","MEDIUM","LOW"],datasets:[{data:[o,n,r],backgroundColor:["#f87171","#fbbf24","#4ade80"],borderWidth:0}]},options:{plugins:{legend:{labels:{color:"#7BA8D4"}}},cutout:"70%"}}));const g={};v.forEach(u=>{g[u.kind]=(g[u.kind]??0)+1});const f=(a=s("risk-bar"))==null?void 0:a.getContext("2d");f&&(j&&j.destroy(),j=new Chart(f,{type:"bar",data:{labels:Object.keys(g),datasets:[{data:Object.values(g),backgroundColor:"#006e74",borderRadius:4,borderWidth:0}]},options:{plugins:{legend:{display:!1}},indexAxis:"y",scales:{x:{ticks:{color:"#7BA8D4"},grid:{color:"#0A3A80"}},y:{ticks:{color:"#7BA8D4",font:{size:11}},grid:{display:!1}}}}})),oe(v)}catch(e){y(e)||["risk-high","risk-med","risk-low"].forEach(o=>{const n=s(o);n&&(n.textContent="—")})}}function oe(t){const a=s("risk-tbody-full");a&&(a.innerHTML=t.map(e=>`
    <tr>
      <td style="font-weight:600;color:var(--ust-sky);">${e.program_name??"—"}</td>
      <td><span class="badge badge-orange">${e.kind}</span></td>
      <td><span class="sev-${e.severity}" style="font-weight:700;">${e.severity}</span></td>
      <td style="font-size:12px;color:var(--muted);max-width:300px;">${e.note??""}</td>
      <td>${e.line??""}</td>
    </tr>`).join("")||'<tr><td colspan="5" style="color:var(--muted);text-align:center;padding:30px;">No risks detected. Run the pipeline first.</td></tr>')}function Fe(){var a;const t=((a=s("risk-filter"))==null?void 0:a.value.toLowerCase())??"";oe(v.filter(e=>(e.program_name??"").toLowerCase().includes(t)||(e.kind??"").toLowerCase().includes(t)||(e.severity??"").toLowerCase().includes(t)))}async function ne(){try{const t=await c("/settings"),a=s("settings-provider");a&&(a.value=t.llm_provider||t.provider);const e=s("settings-current-info");e&&(e.innerHTML=`
      <div style="display:flex;flex-direction:column;gap:10px;">
        <div><span style="color:var(--muted);font-size:12px;">Provider</span><br><span style="font-weight:600;">${t.provider}</span></div>
        <div><span style="color:var(--muted);font-size:12px;">OpenAI Model</span><br><span style="font-weight:600;">${t.openai_model}</span></div>
        <div><span style="color:var(--muted);font-size:12px;">Gemini Model</span><br><span style="font-weight:600;">${t.gemini_model}</span></div>
        <div><span style="color:var(--muted);font-size:12px;">OpenAI Key</span><br><span class="badge ${t.openai_key_set?"badge-green":"badge-red"}">${t.openai_key_set?"✓ Set":"Not set"}</span></div>
        <div><span style="color:var(--muted);font-size:12px;">Gemini Key</span><br><span class="badge ${t.gemini_key_set?"badge-green":"badge-red"}">${t.gemini_key_set?"✓ Set":"Not set"}</span></div>
      </div>`);const o=t.llm_provider||t.provider;await se(o,o==="gemini"?t.gemini_model:t.openai_model)}catch(t){y(t)||console.warn("Failed to load settings:",t)}}async function se(t,a){const e=s("settings-model");if(e){e.innerHTML="<option>Loading…</option>",e.disabled=!0;try{const o=await c("/models");e.innerHTML=o.models.map(n=>`<option value="${n}" ${n===a?"selected":""}>${n}</option>`).join(""),!e.value&&o.models.length&&(e.value=o.models[0]),e.disabled=!1}catch{e.innerHTML=`<option value="${a}">${a}</option>`,e.disabled=!1}}}async function Ge(){const t=s("settings-provider"),a=(t==null?void 0:t.value)??"openai",e=s("settings-openai-key-wrap"),o=s("settings-gemini-key-wrap");e&&(e.style.display=a==="openai"?"":"none"),o&&(o.style.display=a==="gemini"?"":"none"),await se(a,"")}async function qe(){var r,d,i,l,p,g;const t=((r=s("settings-provider"))==null?void 0:r.value)??"openai",a=((d=s("settings-model"))==null?void 0:d.value)??"",e=((l=(i=s("settings-openai-key"))==null?void 0:i.value)==null?void 0:l.trim())??"",o=((g=(p=s("settings-gemini-key"))==null?void 0:p.value)==null?void 0:g.trim())??"",n={llm_provider:t};t==="openai"?n.openai_model=a:n.gemini_model=a,e&&(n.openai_api_key=e),o&&(n.gemini_api_key=o);try{await c("/settings",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(n)});const f=s("settings-saved");f&&(f.style.display="",setTimeout(()=>{f.style.display="none"},3e3)),m("Settings saved"),await ne()}catch(f){y(f)||m("Save failed: "+f.message,"error")}}async function ie(){try{const t=await c("/programs?limit=500"),a=s("viz-program");if(!a)return;a.innerHTML='<option value="">— select program —</option>'+(t.items??[]).map(e=>`<option value="${e.name}">${e.name}</option>`).join("")}catch(t){y(t)||console.warn("Failed to load programs:",t)}}async function We(){var a;const t=((a=s("viz-program"))==null?void 0:a.value)??"";t&&await re(W,t)}function Je(t){var n;W=t,document.querySelectorAll("#page-visualizations .tab-panel").forEach(r=>{r.style.display="none"}),document.querySelectorAll("#page-visualizations .tab").forEach(r=>r.classList.remove("active"));const a=s(`viz-tab-${t}`);a&&(a.style.display="");const e=document.querySelector(`#page-visualizations .tab[onclick="switchVizTab('${t}')"]`);e&&e.classList.add("active");const o=((n=s("viz-program"))==null?void 0:n.value)??"";o&&re(t,o)}async function re(t,a){t==="ast"?await Ke(a):t==="cfg"?await Ye(a):t==="symbols"?await Qe(a):t==="complexity"?await Xe(a):t==="source"&&await ce(a)}async function Ke(t){const a=s("ast-container");if(a){a.innerHTML='<div style="color:var(--muted);text-align:center;padding:40px;">Loading AST…</div>';try{const e=await c(`/programs/${encodeURIComponent(t)}/ast`);if(!e.root){a.innerHTML='<div style="color:var(--muted);text-align:center;padding:40px;">No AST data. Run the pipeline first.</div>';return}a.innerHTML=`<div style="font-size:12px;margin-bottom:12px;color:var(--muted);">
      Click nodes to expand/collapse · ${t}</div>
      <div id="ast-tree">${le(e.root,0)}</div>`}catch(e){y(e)||(a.innerHTML=`<div style="color:#f87171;padding:20px;">Error: ${e.message}</div>`)}}}function le(t,a){var l;if(!t)return"";const e=a*18,o=(((l=t.children)==null?void 0:l.length)??0)>0,r={program:"#5ecdd1",paragraph:"#4ade80",section:"#fbbf24",statement:"#60c8fa",data_division:"#0097ab",procedure_division:"#009ddc"}[t.kind]??"#7e8c9a",d=o?"▶":"·",i=o?`<div id="ast-c-${t.uuid}" style="display:none;">${t.children.map(p=>le(p,a+1)).join("")}</div>`:"";return`
    <div style="margin-left:${e}px;">
      <div style="display:flex;align-items:center;gap:6px;padding:3px 0;cursor:${o?"pointer":"default"};"
           onclick="toggleASTNode('${t.uuid}')">
        <span style="color:var(--muted);font-size:11px;width:12px;text-align:center;" id="ast-toggle-${t.uuid}">${d}</span>
        <span style="color:${r};font-weight:600;font-size:12px;">${t.kind}</span>
        ${t.name?`<span style="color:var(--text);font-size:12px;">${t.name}</span>`:""}
        ${t.start_line?`<span style="color:var(--muted);font-size:11px;">L${t.start_line}${t.end_line&&t.end_line!==t.start_line?"–"+t.end_line:""}</span>`:""}
        ${o?`<span style="color:var(--muted);font-size:11px;">(${t.children.length})</span>`:""}
      </div>
      ${i}
    </div>`}function Ve(t){const a=document.getElementById(`ast-c-${t}`),e=document.getElementById(`ast-toggle-${t}`);if(!a)return;const o=a.style.display!=="none";a.style.display=o?"none":"",e&&(e.textContent=o?"▶":"▼")}async function Ye(t){var e,o;const a=s("cfg-container");if(a){a.innerHTML='<div style="color:var(--muted);text-align:center;padding:40px;">Loading CFG…</div>';try{const n=await c(`/programs/${encodeURIComponent(t)}/cfg`);if(!n.mermaid){a.innerHTML='<div style="color:var(--muted);text-align:center;padding:40px;">No CFG data. Run the pipeline first.</div>';return}a.innerHTML="";const r="cfg-mmd-"+Date.now(),{svg:d}=await mermaid.render(r,n.mermaid);a.innerHTML=d;const i=a.querySelector("svg");i&&(i.style.maxWidth="100%");const l=document.createElement("div");l.style.cssText="font-size:11px;color:var(--muted);margin-top:8px;",l.textContent=`${((e=n.nodes)==null?void 0:e.length)??0} nodes · ${((o=n.edges)==null?void 0:o.length)??0} edges`,a.appendChild(l)}catch(n){y(n)||(a.innerHTML=`<div style="color:#f87171;padding:20px;">Error: ${n.message}</div>`)}}}async function Qe(t){const a=s("symbols-container");if(a){a.innerHTML='<div style="color:var(--muted);text-align:center;padding:40px;">Loading symbol table…</div>';try{const o=(await c(`/programs/${encodeURIComponent(t)}/symbol-table`)).items??[];if(!o.length){a.innerHTML='<div style="color:var(--muted);text-align:center;padding:40px;">No symbol table data. Run the pipeline first.</div>';return}a.innerHTML=`
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">
        <div style="font-weight:600;font-size:14px;">Data Dictionary — ${t}</div>
        <input type="text" placeholder="Filter…" style="flex:1;max-width:280px;"
          oninput="filterSymbolTable(this.value)" id="sym-filter" />
        <span style="font-size:12px;color:var(--muted);">${o.length} items</span>
      </div>
      <div style="overflow:auto;max-height:520px;">
        <table id="sym-table">
          <thead><tr>
            <th>Name</th><th>Level</th><th>PIC</th><th>Usage</th>
            <th>Canonical Type</th><th>Precision</th><th>Scale</th><th>Scope</th>
          </tr></thead>
          <tbody id="sym-tbody">
            ${o.map(n=>de(n)).join("")}
          </tbody>
        </table>
      </div>`,window._symItems=o}catch(e){y(e)||(a.innerHTML=`<div style="color:#f87171;padding:20px;">Error: ${e.message}</div>`)}}}function de(t){const a=Math.max(0,(parseInt(t.level||"1")-1)*14),e=(t.conditions_88??[]).map(o=>`<tr style="background:rgba(0,110,116,.04);">
      <td style="padding-left:${a+28}px;font-size:11px;color:#4ade80;">88 ${o.name}</td>
      <td><span class="badge badge-gray">88</span></td>
      <td colspan="4" style="font-size:11px;color:var(--muted);">VALUE ${o.value_raw||""}</td>
      <td></td><td></td>
    </tr>`).join("");return`<tr>
    <td style="font-weight:500;padding-left:${a}px;">${t.name??""}</td>
    <td><span class="badge badge-gray">${t.level??""}</span></td>
    <td><code style="font-size:11px;color:var(--ust-sky);">${t.pic??""}</code></td>
    <td>${t.usage??"DISPLAY"}</td>
    <td><span class="badge ${t.canonical_kind==="decimal"?"badge-orange":t.canonical_kind==="alpha"?"badge-sky":"badge-gray"}">${t.canonical_kind??""}</span></td>
    <td>${t.precision??""}</td>
    <td>${t.scale??""}</td>
    <td style="font-size:11px;color:var(--muted);">${t.scope??""}</td>
  </tr>${e}`}function Ze(t){const a=window._symItems??[],e=s("sym-tbody");if(!e)return;const o=t.toLowerCase();e.innerHTML=a.filter(n=>(n.name??"").toLowerCase().includes(o)||(n.pic??"").toLowerCase().includes(o)||(n.scope??"").toLowerCase().includes(o)).map(n=>de(n)).join("")}async function Xe(t){var e;const a=s("complexity-container");if(a){a.innerHTML='<div style="color:var(--muted);text-align:center;padding:40px;">Loading complexity…</div>';try{const n=(await c(`/programs/${encodeURIComponent(t)}/complexity`)).paragraphs??[];if(!n.length){a.innerHTML='<div style="color:var(--muted);text-align:center;padding:40px;">No complexity data. Run the pipeline first.</div>';return}const r=[...n].sort((i,l)=>l.cyclomatic-i.cyclomatic).slice(0,20);a.innerHTML=`
      <div style="font-weight:600;margin-bottom:16px;font-size:14px;">Cyclomatic Complexity per Paragraph — ${t}</div>
      <canvas id="complexity-chart-canvas" height="350"></canvas>
      <div style="margin-top:20px;overflow:auto;max-height:300px;">
        <table><thead><tr><th>Paragraph</th><th>Cyclomatic</th><th>LoC</th><th>Risk</th></tr></thead>
        <tbody>${r.map(i=>`
          <tr>
            <td style="font-weight:500;color:var(--ust-sky);">${i.name}</td>
            <td><span class="badge ${i.cyclomatic>10?"badge-red":i.cyclomatic>5?"badge-amber":"badge-green"}">${i.cyclomatic}</span></td>
            <td>${i.loc}</td>
            <td><span class="sev-${i.cyclomatic>10?"HIGH":i.cyclomatic>5?"MEDIUM":"LOW"}" style="font-weight:700;">${i.cyclomatic>10?"HIGH":i.cyclomatic>5?"MEDIUM":"LOW"}</span></td>
          </tr>`).join("")}
        </tbody></table>
      </div>`;const d=(e=s("complexity-chart-canvas"))==null?void 0:e.getContext("2d");d&&(A&&A.destroy(),A=new Chart(d,{type:"bar",data:{labels:r.map(i=>i.name),datasets:[{label:"Cyclomatic Complexity",data:r.map(i=>i.cyclomatic),backgroundColor:r.map(i=>i.cyclomatic>10?"#f87171":i.cyclomatic>5?"#fbbf24":"#4ade80"),borderRadius:4,borderWidth:0}]},options:{indexAxis:"y",plugins:{legend:{display:!1}},scales:{x:{ticks:{color:"#7e8c9a"},grid:{color:"#2b333f"}},y:{ticks:{color:"#7e8c9a",font:{size:11}},grid:{display:!1}}}}}))}catch(o){y(o)||(a.innerHTML=`<div style="color:#f87171;padding:20px;">Error: ${o.message}</div>`)}}}async function ce(t){const a=s("source-container");if(a){a.innerHTML='<div style="color:var(--muted);text-align:center;padding:40px;">Loading source…</div>';try{const e=await c(`/programs/${encodeURIComponent(t)}/source`);if(!e.content){a.innerHTML='<div style="color:var(--muted);text-align:center;padding:40px;">Source file not found.</div>';return}a.innerHTML=`
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px;">
        <div>
          <span style="font-weight:600;font-size:14px;">${t}.cbl</span>
          <span style="font-size:12px;color:var(--muted);margin-left:12px;">${e.line_count} lines · ${e.source_file.split("/").slice(-3).join("/")}</span>
        </div>
        <button class="btn btn-secondary" style="font-size:12px;padding:5px 10px;"
          onclick="navigator.clipboard.writeText(document.getElementById('source-code-pre')?.textContent||'').then(()=>showToast('Copied!'))">
          Copy source
        </button>
      </div>
      <pre id="source-code-pre" style="max-height:580px;overflow:auto;border-radius:8px;margin:0;">
        <code class="language-cobol" style="font-size:11.5px;line-height:1.6;">${pe(e.content)}</code>
      </pre>`;const o=a.querySelector("code");o&&hljs.highlightElement(o)}catch(e){y(e)||(a.innerHTML=`<div style="color:#f87171;padding:20px;">Error: ${e.message}</div>`)}}}function pe(t){return t.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}async function ge(){const t=document.getElementById("lx-loading"),a=document.getElementById("lx-content");if(!(!t||!a)){t.style.display="",a.style.display="none";try{const e=await c("/layers/summary"),o=(d,i)=>{const l=document.getElementById(d);l&&(l.textContent=String(i))};o("lx-l1-programs",e.layer1.programs),o("lx-l1-paragraphs",e.layer1.paragraphs),o("lx-l1-statements",e.layer1.statements),o("lx-l2-items",e.layer2.data_items),o("lx-l2-cond88",e.layer2.conditions_88),o("lx-l2-copybooks",e.layer2.copybook_refs),o("lx-l3-cfg",e.layer3.cfg_edges),o("lx-l3-branch",e.layer3.branch_edges),o("lx-l3-du",e.layer3.def_use_entries);const n=document.getElementById("lx-l3-breakdown");if(n){const d=[{label:"PERFORM",val:e.layer3.perform_edges,color:"#5ecdd1"},{label:"BRANCH",val:e.layer3.branch_edges,color:"#34d399"},{label:"FALLTHROUGH",val:e.layer3.fallthru_edges,color:"#fbbf24"}];n.innerHTML=d.map(i=>`<span class="badge" style="background:#1c2a2c;color:${i.color};">${i.label} <strong>${i.val.toLocaleString()}</strong></span>`).join("")}o("lx-l4-calls",e.layer4.call_edges),o("lx-l4-fileio",e.layer4.file_io),o("lx-l4-tx",e.layer4.tx_flow),o("lx-l4-jcl",e.layer4.jcl_bindings);const r=document.getElementById("lx-l4-resolved");r&&(r.textContent=`${e.layer4.resolved_pct}% resolved`),o("lx-l5-total",e.layer5.business_rules),o("lx-l5-if",e.layer5.if_rules),o("lx-l5-eval",e.layer5.evaluate_rules),o("lx-l5-arith",e.layer5.arith_specs),o("lx-l6-bms",e.layer6.bms_maps),o("lx-l6-csd",e.layer6.csd_entries),o("lx-l7-cov",`${e.layer7.coverage_pct}%`),o("lx-l7-high",e.layer7.risk_high),o("lx-l7-med",e.layer7.risk_medium),o("lx-l7-low",e.layer7.risk_low),t.style.display="none",a.style.display=""}catch(e){y(e)||t&&(t.textContent="Run the pipeline first to populate layer artifacts.")}}}function et(t){const a=document.getElementById(`lx-${t}`);a&&a.scrollIntoView({behavior:"smooth",block:"start"})}let b="";async function tt(t,a=""){const e=s("lx-drilldown-panel"),o=s("lx-drilldown-title"),n=s("lx-drilldown-body");if(!e||!o||!n)return;e.style.display="",n.innerHTML='<div style="color:var(--muted);text-align:center;padding:40px;">Loading…</div>';const r={1:"Layer 1 — Programs & Paragraphs",2:"Layer 2 — Data Items",3:"Layer 3 — CFG Edges",4:"Layer 4 — Call Graph",5:"Layer 5 — Business Rules",6:"Layer 6 — BMS Maps",7:"Layer 7 — Risk Register"};o&&(o.textContent=r[t]??`Layer ${t}`);try{switch(t){case 1:{const d=await c("/layers/1/programs?limit=200");n.innerHTML=`
          <div style="overflow:auto;max-height:500px;">
            <table><thead><tr><th>Program</th><th>Source File</th><th>Paragraphs</th><th>Statements</th><th></th></tr></thead>
            <tbody>${(d??[]).map(i=>`
              <tr>
                <td style="font-weight:600;color:#5ecdd1;">${i.name}</td>
                <td style="font-size:11px;color:var(--muted);">${(i.source_file||"").split("/").pop()}</td>
                <td><span class="badge badge-sky">${i.para_count??0}</span></td>
                <td>${i.stmt_count??0}</td>
                <td><button class="btn btn-secondary" style="font-size:11px;padding:3px 8px;"
                    onclick="navigate('visualizations');setTimeout(()=>{const s=document.getElementById('viz-program');if(s)s.value='${i.name}';switchVizTab('source');loadSourceCode('${i.name}');},200)">
                  View Source</button></td>
              </tr>`).join("")}
            </tbody></table>
          </div>`;break}case 2:{const d=b?`?program=${encodeURIComponent(b)}&limit=200`:"?limit=200",i=await c(`/layers/2/data-items${d}`);n.innerHTML=`
          <div style="overflow:auto;max-height:500px;">
            <table><thead><tr><th>Name</th><th>Level</th><th>PIC</th><th>Type</th><th>Precision</th><th>Program</th></tr></thead>
            <tbody>${(i??[]).map(l=>`
              <tr>
                <td style="font-weight:500;padding-left:${Math.max(0,(parseInt(l.level||0)-1)*8)}px">${l.name}</td>
                <td><span class="badge badge-gray">${l.level}</span></td>
                <td><code style="font-size:11px;color:var(--ust-sky);">${l.pic||""}</code></td>
                <td><span class="badge ${l.canonical_kind==="decimal"?"badge-orange":l.canonical_kind==="alpha"?"badge-sky":"badge-gray"}">${l.canonical_kind||""}</span></td>
                <td>${l.precision||""}</td>
                <td style="font-size:11px;color:var(--muted);">${l.program_name||""}</td>
              </tr>`).join("")}
            </tbody></table>
          </div>`;break}case 3:{const d=b?`?program=${encodeURIComponent(b)}&limit=300`:"?limit=300",i=await c(`/layers/3/cfg-edges${d}`),l={};(i??[]).forEach(p=>{l[p.edge_type]=(l[p.edge_type]||0)+1}),n.innerHTML=`
          <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;">
            ${Object.entries(l).map(([p,g])=>`<span class="badge badge-sky">${p} <strong>${g}</strong></span>`).join("")}
          </div>
          <div style="overflow:auto;max-height:460px;">
            <table><thead><tr><th>From Paragraph</th><th>Edge Type</th><th>To Paragraph</th><th>Program</th></tr></thead>
            <tbody>${(i??[]).map(p=>{var g;return`
              <tr>
                <td style="font-weight:500;color:var(--ust-sky);">${p.from_para}</td>
                <td><span class="badge ${(g=p.edge_type)!=null&&g.includes("BRANCH")?"badge-green":p.edge_type==="PERFORM"?"badge-sky":p.edge_type==="FALLTHROUGH"?"badge-gray":"badge-amber"}">${p.edge_type}</span></td>
                <td style="font-weight:500;color:var(--ust-sky);">${p.to_para}</td>
                <td style="font-size:11px;color:var(--muted);">${p.program_name}</td>
              </tr>`}).join("")}
            </tbody></table>
          </div>`;break}case 4:{const d=await c("/layers/4/call-graph?limit=200");n.innerHTML=`
          <div style="overflow:auto;max-height:500px;">
            <table><thead><tr><th>Caller</th><th>Callee</th><th>Type</th><th>Resolved</th></tr></thead>
            <tbody>${(d??[]).map(i=>`
              <tr>
                <td style="font-weight:500;color:var(--ust-sky);">${i.caller_name}</td>
                <td style="font-weight:500;">${i.callee_name}</td>
                <td><span class="badge badge-sky">${i.call_type}</span></td>
                <td>${i.is_resolved?'<span class="badge badge-green">✓ resolved</span>':'<span class="badge badge-amber">unresolved</span>'}</td>
              </tr>`).join("")}
            </tbody></table>
          </div>`;break}case 5:{const d=b?`?program=${encodeURIComponent(b)}&limit=200`:"?limit=200",i=await c(`/layers/5/business-rules${d}`);n.innerHTML=`
          <div style="overflow:auto;max-height:500px;">
            <table><thead><tr><th>Program</th><th>Line</th><th>Kind</th><th>Predicate</th><th>Then</th><th>Else</th></tr></thead>
            <tbody>${(i??[]).map(l=>`
              <tr>
                <td style="font-size:11px;color:var(--muted);">${l.program_name}</td>
                <td>${l.line||""}</td>
                <td><span class="badge badge-orange">${l.kind}</span></td>
                <td style="font-size:11px;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${(l.predicate_raw||"").replace(/"/g,"&quot;")}">${(l.predicate_raw||"").slice(0,60)}</td>
                <td style="font-size:11px;color:var(--muted);max-width:150px;overflow:hidden;text-overflow:ellipsis;">${(l.then_summary||"").slice(0,40)}</td>
                <td style="font-size:11px;color:var(--muted);max-width:150px;overflow:hidden;text-overflow:ellipsis;">${(l.else_summary||"").slice(0,40)}</td>
              </tr>`).join("")}
            </tbody></table>
          </div>`;break}case 6:{const d=await c("/layers/6/bms-maps?limit=200"),i=await c("/layers/6/csd?limit=100");n.innerHTML=`
          <div style="font-weight:600;margin-bottom:8px;font-size:13px;">BMS Screen Maps (${d.length} fields)</div>
          <div style="overflow:auto;max-height:280px;margin-bottom:16px;">
            <table><thead><tr><th>Map</th><th>Mapset</th><th>Field</th><th>Row</th><th>Col</th><th>Length</th><th>Attrs</th></tr></thead>
            <tbody>${(d??[]).map(l=>`
              <tr>
                <td style="font-weight:500;color:var(--ust-sky);">${l.map_name}</td>
                <td style="font-size:11px;color:var(--muted);">${l.mapset_name}</td>
                <td>${l.field_name}</td>
                <td>${l.position_row}</td><td>${l.position_col}</td><td>${l.length}</td>
                <td style="font-size:11px;color:var(--muted);">${l.attributes||""}</td>
              </tr>`).join("")}
            </tbody></table>
          </div>
          <div style="font-weight:600;margin-bottom:8px;font-size:13px;">CSD Catalog (${i.length} entries)</div>
          <div style="overflow:auto;max-height:200px;">
            <table><thead><tr><th>Name</th><th>Type</th><th>Program</th><th>Transaction</th></tr></thead>
            <tbody>${(i??[]).map(l=>`
              <tr>
                <td style="font-weight:500;">${l.name||""}</td>
                <td><span class="badge badge-sky">${l.resource_type||""}</span></td>
                <td style="font-size:11px;">${l.program_name||""}</td>
                <td style="font-size:11px;">${l.transaction_id||""}</td>
              </tr>`).join("")}
            </tbody></table>
          </div>`;break}case 7:{const d=await c("/layers/7/risks?limit=500");n.innerHTML=`
          <div style="overflow:auto;max-height:500px;">
            <table><thead><tr><th>Program</th><th>Kind</th><th>Severity</th><th>Note</th><th>Line</th></tr></thead>
            <tbody>${(d??[]).map(i=>`
              <tr>
                <td style="font-weight:500;color:var(--ust-sky);">${i.program_name||"—"}</td>
                <td><span class="badge badge-orange">${i.kind}</span></td>
                <td><span class="sev-${i.severity}" style="font-weight:700;">${i.severity}</span></td>
                <td style="font-size:11px;color:var(--muted);">${i.note||""}</td>
                <td>${i.line||""}</td>
              </tr>`).join("")}
            </tbody></table>
          </div>`;break}}}catch(d){y(d)||(n.innerHTML=`<div style="color:#f87171;padding:20px;">Error: ${d.message}</div>`)}}function at(){const t=s("lx-drilldown-panel");t&&(t.style.display="none")}Object.assign(window,{navigate:ye,checkHealth:B,loadDashboard:U,loadPrograms:V,openProgram:me,closeDetail:ue,switchTab:Y,loadDiagram:Q,copyDiagramSource:Ce,loadProgramDropdowns:E,loadEmitDropdown:Z,onSpecProgramChange:X,onSpecScopeChange:Le,generateSpec:Ee,copySpec:Me,generateModernizationReport:ze,viewModernizationReport:Se,copyModernizationReport:Re,emitJava:He,quickEmit:Pe,copyJava:Ie,runPipeline:te,runSmoke:Oe,cancelPipeline:De,filterCovTable:Be,filterRiskTable:Fe,loadVizProgramDropdown:ie,loadViz:We,switchVizTab:Je,toggleASTNode:Ve,filterSymbolTable:Ze,onProviderChange:Ge,saveSettings:qe,loadLayersPage:ge,scrollToLayer:et,loadSourceCode:ce,lxBrowse:tt,lxClose:at});B();setInterval(()=>{B()},3e4);U();
