let state = {
  jobs: [], selected: null, config: null, scanTimer: null, scanWasRunning: false,
  handoffJobId: null, view: 'active', activeCount: 0, archivedCount: 0, scoreSort: 'score',
};
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const statusNames = {new:'Ny',discovered:'Fundet',shortlisted:'Kortlistet',dismissed:'Fravalgt',drafted:'Udkast',awaiting_review:'Til gennemgang',submitted:'Sendt',confirmation_received:'Bekræftet',interview:'Samtale',rejected:'Afslag',withdrawn:'Trukket',duplicate:'Dublet'};
const providerNames = {template:'Skabelon',openai:'OpenAI',ollama:'Ollama',clipboard:'AI-agent',manual:'Manuelt redigeret','manual-edit':'Manuelt redigeret'};

async function api(url, options={}) {
  const res = await fetch(url, options);
  const data = res.headers.get('content-type')?.includes('json') ? await res.json() : await res.text();
  if (!res.ok) throw new Error(data.detail || data || `HTTP ${res.status}`);
  return data;
}
function toast(msg, error=false, duration=5000){
  const el=$('#toast'); el.textContent=msg; el.classList.remove('hidden','error');
  if(error)el.classList.add('error');
  window.clearTimeout(el._timer); el._timer=setTimeout(()=>el.classList.add('hidden'),duration);
}
function detailsOf(value){
  if(!value) return {};
  if(typeof value==='object') return value;
  try{return JSON.parse(value)}catch{return {}}
}
function recommendationClass(label=''){
  const lower=label.toLowerCase();
  if(lower.includes('stærk')||lower.includes('høj prioritet')) return 'strong';
  if(lower.includes('muligt')||lower.includes('interessant')) return 'possible';
  return 'weak';
}
async function copyText(text){
  try{
    await navigator.clipboard.writeText(text);
  }catch{
    const area=document.createElement('textarea'); area.value=text; area.style.position='fixed'; area.style.opacity='0';
    document.body.appendChild(area); area.select(); document.execCommand('copy'); area.remove();
  }
}

async function loadJobs(){
  const data=await api('/api/jobs'); state.jobs=data.jobs; state.activeCount=Number(data.active_count||0); state.archivedCount=Number(data.archived_count||0); renderStats(data.counts); renderViewSwitch(); renderRows();
  if(state.selected){
    const exists=state.jobs.find(j=>j.id===state.selected);
    if(exists) await selectJob(state.selected);
    else state.selected=null;
  }
}
function renderStats(counts){
  const cards=[['Ny',counts.new||0],['Fundet',counts.discovered||0],['Kortlistet',counts.shortlisted||0],['Udkast',counts.drafted||0],['Sendt',(counts.submitted||0)+(counts.confirmation_received||0)],['Afslag',counts.rejected||0]];
  $('#stats').innerHTML=cards.map(([label,n])=>`<div class="stat card"><strong>${n}</strong><span>${label}</span></div>`).join('');
}
function renderViewSwitch(){
  $('#activeViewCount').textContent=state.activeCount; $('#archiveViewCount').textContent=state.archivedCount;
  $('#activeViewBtn').classList.toggle('active',state.view==='active');
  $('#archiveViewBtn').classList.toggle('active',state.view==='archive');
}
function clearSelection(){
  state.selected=null; $('#detail').innerHTML='<div class="empty">Vælg et job for at se detaljerne.</div>';
}
function setJobView(view){
  state.view=view==='archive'?'archive':'active'; $('#statusFilter').value=''; clearSelection(); renderViewSwitch(); renderRows();
}
function renderRows(){
  const q=$('#searchFilter').value.toLowerCase(); const st=$('#statusFilter').value;
  const minimums={qualification_score:Number($('#qualificationFilter').value||0),direction_score:Number($('#directionFilter').value||0),curiosity_score:Number($('#curiosityFilter').value||0)};
  const jobs=state.jobs.filter(j=>{
    const archived=Boolean(Number(j.archived||0));
    const inView=state.view==='archive'?archived:!archived;
    return inView&&(!st||j.status===st)&&(`${j.title} ${j.employer}`.toLowerCase().includes(q))
      &&Object.entries(minimums).every(([key,value])=>Number(j[key]||0)>=value);
  }).sort((a,b)=>Number(b[state.scoreSort]??-1)-Number(a[state.scoreSort]??-1)||Number(b.score??-1)-Number(a.score??-1));
  $('#empty').textContent=state.view==='archive'?'Arkivet er tomt eller matcher ikke filteret.':'Ingen aktive job matcher filteret.';
  $('#empty').classList.toggle('hidden',jobs.length>0);
  $('#jobRows').innerHTML=jobs.map(j=>{
    const cls=j.score>=70?'high':j.score>=45?'mid':'low';
    const d=detailsOf(j.score_details);
    const scoreTitle=`${d.recommendation||'Vurdering'}${d.summary?`: ${d.summary}`:''}`;
    return `<tr data-id="${j.id}" class="${state.selected===j.id?'selected':''}">
      <td><span class="score ${cls}" title="${esc(scoreTitle)}">${j.score??'–'}</span></td>
      <td><strong>${esc(j.title)}</strong><br><small>${esc(j.location||'')}</small></td>
      <td>${esc(j.employer||'Ikke fundet endnu')}</td>
      <td><span class="badge ${esc(j.status)}">${esc(statusNames[j.status]||j.status)}</span></td>
      <td>${esc(j.source)}</td>
    </tr>`;
  }).join('');
  document.querySelectorAll('#jobRows tr').forEach(tr=>tr.onclick=()=>selectJob(Number(tr.dataset.id)));
}
function reasonRows(items=[], positive=true){
  return items.map(item=>{
    const points=Number(item.points||0);
    const sign=points>0?`+${points}`:points<0?String(points):'—';
    return `<div class="reason-row ${positive?'positive':'negative'}"><span>${esc(item.label)}</span><strong>${esc(sign)}</strong></div>`;
  }).join('');
}
const dimensionNames={qualification:'Kvalifikation',direction:'Retning',curiosity:'Nysgerrighed'};
function dimensionReasonMarkup(details){
  const grouped=details.reasons_by_dimension||{};
  return Object.entries(dimensionNames).map(([key,label])=>{
    const items=grouped[key]||[];
    if(!items.length) return '';
    return `<section class="dimension-reasons"><h4>${label}</h4>${reasonRows(items.filter(x=>Number(x.points)>=0),true)}${reasonRows(items.filter(x=>Number(x.points)<0),false)}</section>`;
  }).join('');
}
function listMarkup(items=[], empty=''){
  if(!items.length) return empty?`<p class="hint">${esc(empty)}</p>`:'';
  return `<ul>${items.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`;
}
async function selectJob(id){
  state.selected=id; renderRows(); const data=await api(`/api/jobs/${id}`); const j=data.job; const draft=data.drafts[0];
  const details=detailsOf(j.score_details);
  const groupedReasons=dimensionReasonMarkup(details);
  const positive=reasonRows(details.positive_reasons||[],true);
  const negative=reasonRows(details.negative_reasons||[],false);
  const recommendation=details.recommendation||((j.score||0)>=70?'Stærkt match':(j.score||0)>=45?'Muligt match':'Svagt match');
  const draftMeta=draft?`${providerNames[draft.provider]||draft.provider}${draft.model?` · ${draft.model}`:''}`:'';
  const facts=draft?.facts_used||[]; const warnings=draft?.warnings||[];
  $('#detail').innerHTML=`
    <h2>${esc(j.title)}</h2><div class="meta">${esc(j.employer||'Virksomhed ikke fundet')} · ${esc(j.location||'Sted ikke angivet')}${Number(j.archived||0)?' · Arkiveret':''}</div>
    <div class="fit-box">
      <div class="fit-heading"><span class="recommendation ${recommendationClass(recommendation)}">${esc(recommendation)}</span><strong>${j.score??'–'} / 100</strong></div>
      <div class="dimension-scores">
        <div><span>Samlet</span><strong>${j.score??'–'}</strong></div>
        <div><span>Kan jeg?</span><strong>${j.qualification_score??details.qualification_score??'–'}</strong></div>
        <div><span>Retning</span><strong>${j.direction_score??details.direction_score??'–'}</strong></div>
        <div><span>Nysgerrighed</span><strong>${j.curiosity_score??details.curiosity_score??'–'}</strong></div>
      </div>
      <p>${esc(details.summary||'Scoren sammenligner annoncen med din profil og dine jobpræferencer.')}</p>
      ${(details.matched_career_tracks||[]).length?`<p class="track-list"><strong>Karrierespor:</strong> ${(details.matched_career_tracks||[]).map(track=>esc(track.label||track.id)).join(' · ')}</p>`:''}
      <p class="found-note"><strong>Hvorfor vises jobbet?</strong> Det matchede filtrene på Jobnet/Jobindex. Status “Ny” betyder, at jobbet er fundet for første gang af en scanning. “Ny” eller “Fundet” er ikke i sig selv en anbefaling.</p>
      ${(groupedReasons||positive||negative)?`<div class="reason-list">${groupedReasons||positive+negative}</div>`:''}
    </div>
    <label>Status<select id="detailStatus">${Object.entries(statusNames).map(([v,l])=>`<option value="${v}" ${j.status===v?'selected':''}>${l}</option>`).join('')}</select></label>
    <div class="detail-actions">
      <button class="primary" id="draftBtn">${draft?'Lav nyt udkast':'Lav ansøgning'}</button>
      <button class="secondary archive-action" id="archiveBtn">${Number(j.archived||0)?'Gendan til aktive':'Arkivér'}</button>
      <button class="secondary" id="assistBtn">Åbn ansøgningsassistent</button>
      ${draft?`<a href="/api/jobs/${j.id}/application.txt"><button class="secondary">Hent tekst</button></a>`:''}
      ${j.source_url?`<a href="${esc(j.source_url)}" target="_blank" rel="noreferrer"><button class="secondary">Se annonce</button></a>`:''}
    </div>
    ${draft?`<section class="draft-section">
      <div class="draft-heading"><h3>Seneste udkast</h3><span class="provider-badge">${esc(draftMeta)}</span></div>
      <textarea id="draftEditor" rows="18">${esc(draft.body)}</textarea>
      <div class="inline-actions"><button class="primary" id="saveDraftBtn">Gem ændringer</button><button class="secondary" id="copyDraftBtn">Kopiér ansøgning</button></div>
      ${(facts.length||warnings.length)?`<details class="generation-details"><summary>Fakta og advarsler fra generatoren</summary>${facts.length?`<h4>Fakta brugt</h4>${listMarkup(facts)}`:''}${warnings.length?`<h4>Kontrollér dette</h4>${listMarkup(warnings)}`:''}</details>`:''}
    </section>`:''}
    <h3>Annonce</h3><div class="description">${esc(j.description||'Ingen tekst')}</div>`;
  $('#detailStatus').onchange=async e=>{ await api(`/api/jobs/${id}/status`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:e.target.value})}); toast('Status opdateret'); await loadJobs(); };
  $('#archiveBtn').onclick=async()=>{
    const shouldArchive=!Boolean(Number(j.archived||0));
    try{
      await api(`/api/jobs/${id}/archive`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({archived:shouldArchive})});
      toast(shouldArchive?'Jobbet er flyttet til arkivet':'Jobbet er gendannet til den aktive liste');
      clearSelection(); await loadJobs();
    }catch(e){toast(e.message,true)}
  };
  $('#draftBtn').onclick=async()=>{
    const btn=$('#draftBtn'); const old=btn.textContent; btn.disabled=true; btn.innerHTML='<span class="spinner small"></span> Laver udkast…';
    try{
      const result=await api(`/api/jobs/${id}/draft`,{method:'POST'});
      if(result.mode==='handoff'){
        state.handoffJobId=id; $('#aiAgentPrompt').value=result.prompt; $('#aiAgentResponse').value=''; $('#aiAgentDialog').showModal();
        toast('Ansøgningspakken er klar til AI-agenten');
      }else{
        toast(`Ansøgning klar via ${providerNames[result.provider]||result.provider}`); await loadJobs();
      }
    }catch(e){toast(e.message,true,8000)}finally{btn.disabled=false;btn.textContent=old}
  };
  $('#assistBtn').onclick=async()=>{ try{await api(`/api/jobs/${id}/launch-assist`,{method:'POST'});toast('Browserassistenten åbner. Kontrollér alt før du sender.');}catch(e){toast(e.message,true)} };
  if(draft){
    $('#saveDraftBtn').onclick=async()=>{
      const body=$('#draftEditor').value.trim(); if(body.length<20){toast('Ansøgningen er for kort til at gemme.',true);return}
      try{
        await api(`/api/jobs/${id}/draft/save`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({body,provider:'manual-edit',facts_used:facts,warnings})});
        toast('Dine ændringer er gemt som et nyt udkast'); await loadJobs();
      }catch(e){toast(e.message,true)}
    };
    $('#copyDraftBtn').onclick=async()=>{await copyText($('#draftEditor').value);toast('Ansøgningen er kopieret')};
  }
}

function renderScanStatus(scan){
  const panel=$('#scanStatus'); const btn=$('#scanBtn');
  const active=Boolean(scan.running);
  btn.disabled=active;
  btn.classList.toggle('scanning',active);
  btn.innerHTML=active?'<span class="spinner small"></span><span>Scanner…</span>':'Scan søgninger';
  if(scan.phase==='idle'&&!active){panel.classList.add('hidden');return}
  panel.classList.remove('hidden','scan-error','scan-complete');
  if(scan.phase==='failed'||scan.phase==='complete_with_errors')panel.classList.add('scan-error');
  if(scan.phase==='complete')panel.classList.add('scan-complete');
  $('#scanIcon').innerHTML=active?'<span class="spinner"></span>':scan.phase==='failed'?'!':'✓';
  $('#scanMessage').textContent=scan.message||'Scanner…';
  const rejected=(scan.prefiltered||0)+(scan.filtered||0);
  const batches=Number(scan.api_batches||0); const batchText=batches?` · ${batches} resultatindlæsninger`:'';
  const fallbacks=Number(scan.detail_fallbacks||0); const fallbackText=fallbacks?` · ${fallbacks} Jobindex-kort brugt som fallback`:'';
  const checkpointCards=Number(scan.checkpoint_cards||0); const checkpointText=checkpointCards&&checkpointCards!==Number(scan.listings_seen||0)?` · ${checkpointCards} siden kontrolpunkt`:'';
  $('#scanCounts').textContent=`${scan.listings_seen||0} jobkort set${checkpointText}${batchText}${fallbackText} · ${scan.processed||0}/${scan.links||0} kandidater · ${scan.new||0} nye · ${scan.duplicates||0} dubletter · ${rejected} frasorteret`;
  $('#scanCurrent').textContent=scan.current_title||scan.current_source||'';
  const progress=$('#scanProgress');
  if(active&&scan.phase==='discovering'){
    progress.removeAttribute('value'); progress.max=1;
  }else{
    progress.max=Math.max(1,Number(scan.links||0)); progress.value=Math.min(Number(scan.processed||0),progress.max);
  }
  const error=(scan.errors||[])[0]||'';
  $('#scanError').textContent=error;
  $('#scanError').classList.toggle('hidden',!error);
}
async function pollScanStatus(){
  window.clearTimeout(state.scanTimer);
  try{
    const scan=await api('/api/search/scan/status');
    renderScanStatus(scan);
    if(scan.running){
      state.scanWasRunning=true;
      state.scanTimer=setTimeout(pollScanStatus,700);
    }else if(state.scanWasRunning){
      state.scanWasRunning=false;
      toast(scan.message||'Scanningen er færdig',scan.phase==='failed'||scan.phase==='complete_with_errors',8000);
      await loadJobs();
    }
  }catch(e){
    renderScanStatus({phase:'failed',running:false,message:e.message,errors:[e.message]});
  }
}

$('#searchFilter').oninput=renderRows; $('#statusFilter').onchange=renderRows;
$('#scoreSort').onchange=e=>{state.scoreSort=e.target.value;renderRows()};
['qualificationFilter','directionFilter','curiosityFilter'].forEach(id=>$(`#${id}`).oninput=renderRows);
$('#activeViewBtn').onclick=()=>setJobView('active'); $('#archiveViewBtn').onclick=()=>setJobView('archive');
$('#addBtn').onclick=()=>$('#addDialog').showModal();
$('#addForm').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target);const body=Object.fromEntries(f.entries());try{toast('Henter og analyserer annoncen…');const r=await api('/api/jobs/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});$('#addDialog').close();e.target.reset();toast(r.duplicate?`Dublet fundet: ${r.reason}`:`Job importeret med score ${r.score}`);await loadJobs();}catch(err){toast(err.message,true)}};
$('#scanBtn').onclick=async()=>{try{const r=await api('/api/search/scan',{method:'POST'});state.scanWasRunning=true;renderScanStatus(r);pollScanStatus();}catch(e){toast(e.message,true)}};
$('#exportBtn').onclick=()=>location.href='/api/export/joblog.csv';
$('#historyBtn').onclick=()=>$('#historyDialog').showModal();
$('#historyForm').onsubmit=async e=>{e.preventDefault();try{const r=await api('/api/history/import',{method:'POST',body:new FormData(e.target)});$('#historyDialog').close();toast(`${r.imported} historiske ansøgninger importeret; ${r.duplicates} dubletter`);await loadJobs();}catch(err){toast(err.message,true)}};

function csvList(value){return value.split(',').map(x=>x.trim()).filter(Boolean)}
function aiConfigFromForm(){
  return {
    openai_model:$('#openaiModel').value.trim(), openai_url:$('#openaiUrl').value.trim(),
    ollama_model:$('#ollamaModel').value, ollama_url:$('#ollamaUrl').value.trim(),
  };
}
function setConnectionStatus(message,error=false){
  const el=$('#aiConnectionStatus'); el.textContent=message; el.classList.remove('hidden','error','good'); el.classList.add(error?'error':'good');
}
function setOllamaModels(models=[], current=''){
  const unique=[...new Set([current,...models].filter(Boolean))];
  $('#ollamaModel').innerHTML=unique.length?unique.map(m=>`<option value="${esc(m)}">${esc(m)}</option>`).join(''):'<option value="">Ingen lokale modeller fundet</option>';
  if(current) $('#ollamaModel').value=current;
}
function refreshProviderPanels(){
  const provider=$('#aiProvider').value;
  document.querySelectorAll('[data-provider-panel]').forEach(el=>el.classList.toggle('active',el.dataset.providerPanel===provider));
  const messages={
    template:'Gratis og helt lokal. Resultatet er stabilt, men generisk.',
    openai:'Sender annoncen og den relevante faktaprofil til OpenAI og gemmer det strukturerede resultat lokalt.',
    ollama:'Kører lokalt uden metered API-regning. Kvaliteten afhænger af den valgte model og din computer.',
    clipboard:'Laver en komplet pakke, som du kan kopiere til en valgfri AI-agent. Copy/paste-trinnet foregår manuelt.',
  };
  $('#providerExplainer').textContent=messages[provider]||'';
}
function formatCheckpoint(value){
  if(!value) return 'Ikke sat endnu';
  const date=new Date(value); if(Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('da-DK',{dateStyle:'medium',timeStyle:'short'});
}
function parseSettingsJson(selector){
  const value=JSON.parse($(selector).value);
  const isObject=x=>x!==null && typeof x==='object' && !Array.isArray(x);
  if(!isObject(value)) throw new Error('JSON skal være et objekt.');
  if(selector==='#profileJson'){
    for(const key of ['person','career','preferences','files','ai']){
      if(value[key]!==undefined && !isObject(value[key])) throw new Error(`${key} skal være et objekt.`);
    }
    for(const [group,key] of [['career','skills'],['preferences','preferred_terms'],['preferences','negative_terms']]){
      if(value[group]?.[key]!==undefined && (!Array.isArray(value[group][key]) || !value[group][key].every(x=>typeof x==='string'))) throw new Error(`${group}.${key} skal være en liste med tekst.`);
    }
  }else if(selector==='#searchesJson'){
    if(!Array.isArray(value.searches) || !value.searches.every(isObject)) throw new Error('searches skal være en liste med søgeobjekter.');
    for(const search of value.searches){
      if(search.incremental!==undefined && !isObject(search.incremental)) throw new Error('incremental skal være et objekt.');
      if(search.incremental?.checkpoint!==undefined && !isObject(search.incremental.checkpoint)) throw new Error('checkpoint skal være et objekt.');
    }
  }else{
    if(!Number.isInteger(value.version)||value.version<1) throw new Error('version skal være et positivt heltal.');
    if(!isObject(value.overall_score?.formula)) throw new Error('overall_score.formula skal være et objekt.');
    for(const key of ['qualification','direction','curiosity']){
      if(typeof value.overall_score.formula[key]!=='number') throw new Error(`overall_score.formula.${key} skal være et tal.`);
    }
    const weightSum=Object.values(value.overall_score.formula).reduce((sum,weight)=>sum+Number(weight||0),0);
    if(Math.abs(weightSum-1)>0.001) throw new Error('Vægtene i overall_score.formula skal tilsammen være 1.');
    for(const key of ['career_tracks','activity_signals','penalties','recommendations']){
      if(!Array.isArray(value[key])) throw new Error(`${key} skal være en liste.`);
    }
    if(!Array.isArray(value.retention_rules?.store_if_any)) throw new Error('retention_rules.store_if_any skal være en liste.');
  }
  return value;
}
function settingsError(message=''){
  $('#settingsError').textContent=message;
  $('#settingsError').classList.toggle('hidden',!message);
}
function resetSearchCheckpoint(index){
  let searches;
  try{searches=parseSettingsJson('#searchesJson')}catch(err){settingsError(err.message);return}
  const search=searches.searches[index]; if(!search) return;
  search.incremental=search.incremental||{};
  search.incremental.checkpoint={...(search.incremental.checkpoint||{}),value:'',committed_at:''};
  $('#searchesJson').value=JSON.stringify(searches,null,2);
  settingsError();
  const button=document.querySelector(`[data-reset-checkpoint="${index}"]`);
  if(button){button.disabled=true; const label=button.parentElement?.querySelector('strong'); if(label) label.textContent='Ikke sat endnu';}
  toast('Scanningskontrolpunktet er nulstillet. Gem indstillingerne for at bekræfte.');
}

function populateProfileSettings(p){
  const person=p.person||{}, career=p.career||{}, prefs=p.preferences||{};
  $('#profileName').value=person.full_name||''; $('#profileEmail').value=person.email||''; $('#profilePhone').value=person.phone||''; $('#profileCity').value=person.city||'';
  $('#profileHeadline').value=career.headline||''; $('#profileSummary').value=career.summary||''; $('#profileSkills').value=(career.skills||[]).join(', '); $('#profilePreferred').value=(prefs.preferred_terms||[]).join(', '); $('#profileNegative').value=(prefs.negative_terms||[]).join(', ');
  const cvFiles=[...new Set([...(state.config.cv_files||[]),p.files?.default_cv].filter(Boolean))];
  $('#defaultCv').innerHTML=`<option value="">Intet valgt</option>`+cvFiles.map(f=>`<option value="${esc(f)}">${esc(f)}</option>`).join(''); $('#defaultCv').value=p.files?.default_cv||'';
  const ai=p.ai||{}; $('#aiProvider').value=ai.provider||'template'; $('#openaiModel').value=ai.openai_model||'gpt-5-mini'; $('#openaiUrl').value=ai.openai_url||'https://api.openai.com/v1'; $('#ollamaUrl').value=ai.ollama_url||'http://localhost:11434'; setOllamaModels([],ai.ollama_model||'qwen3:8b');
  refreshProviderPanels();
}
function populateSearchSettings(searches){
  $('#searchEntries').innerHTML=(searches.searches||[]).map((x,i)=>{
    const incremental=x.incremental||{}, checkpoint=incremental.checkpoint||{};
    const supported=String(x.source||'').toLowerCase()==='jobnet';
    return `<div class="search-entry card"><label><input type="checkbox" data-search-enabled="${i}" ${x.enabled?'checked':''}> Aktivér ${esc(x.source)}</label><label>Navn<input data-search-name="${i}" value="${esc(x.name)}"></label><label>Søgeresultat-URL<input type="url" data-search-url="${i}" value="${esc(x.url||'')}" placeholder="https://…"></label><label class="checkpoint-toggle"><input type="checkbox" data-search-incremental="${i}" ${incremental.enabled?'checked':''} ${supported?'':'disabled'}> Kun nye opslag siden sidste fuldførte scanning</label><div class="checkpoint-row"><span>Scanningskontrolpunkt: <strong>${esc(formatCheckpoint(checkpoint.value))}</strong>${supported?'':' · endnu ikke understøttet for denne kilde'}</span><button type="button" class="secondary compact" data-reset-checkpoint="${i}" ${checkpoint.value?'':'disabled'}>Nulstil kontrolpunkt</button></div></div>`;
  }).join('');
  document.querySelectorAll('[data-reset-checkpoint]').forEach(btn=>btn.onclick=()=>resetSearchCheckpoint(Number(btn.dataset.resetCheckpoint)));
}
function populateSettings(){
  $('#profileJson').value=JSON.stringify(state.config.profile,null,2);
  $('#searchesJson').value=JSON.stringify(state.config.searches,null,2);
  $('#scoringJson').value=JSON.stringify(state.config.scoring,null,2);
  populateProfileSettings(state.config.profile);
  populateSearchSettings(state.config.searches);
  settingsError();
  $('#resetConfirmation').value=''; $('#resetDatabaseBtn').disabled=true; $('#aiConnectionStatus').classList.add('hidden');
}

$('#aiProvider').onchange=refreshProviderPanels;
$('#testOpenaiBtn').onclick=async()=>{
  const btn=$('#testOpenaiBtn');btn.disabled=true;btn.textContent='Tester…';
  try{const r=await api('/api/ai/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider:'openai',config:aiConfigFromForm()})});setConnectionStatus(r.message)}catch(e){setConnectionStatus(e.message,true)}finally{btn.disabled=false;btn.textContent='Test OpenAI-forbindelse'}
};
async function refreshOllama(testOnly=false){
  const btn=testOnly?$('#testOllamaBtn'):$('#refreshOllamaBtn'); const old=btn.textContent;btn.disabled=true;btn.textContent='Forbinder…';
  try{
    const r=await api('/api/ai/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider:'ollama',config:aiConfigFromForm()})});
    const current=$('#ollamaModel').value; setOllamaModels(r.models||[],current); setConnectionStatus(r.message);
  }catch(e){setConnectionStatus(e.message,true)}finally{btn.disabled=false;btn.textContent=old}
}
$('#refreshOllamaBtn').onclick=()=>refreshOllama(false); $('#testOllamaBtn').onclick=()=>refreshOllama(true);

$('#copyAiAgentPromptBtn').onclick=async()=>{await copyText($('#aiAgentPrompt').value);toast('Pakken er kopieret.')};
$('#saveAiAgentDraftBtn').onclick=async()=>{
  const body=$('#aiAgentResponse').value.trim(); if(body.length<20){toast('Indsæt den færdige ansøgning først.',true);return}
  if(!state.handoffJobId){toast('Jeg har mistet forbindelsen til jobbet. Luk dialogen og prøv igen.',true);return}
  const btn=$('#saveAiAgentDraftBtn');btn.disabled=true;btn.textContent='Gemmer…';
  try{
    await api(`/api/jobs/${state.handoffJobId}/draft/save`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({body,provider:'clipboard'})});
    $('#aiAgentDialog').close(); toast('AI-agent-udkastet er gemt'); await loadJobs();
  }catch(e){toast(e.message,true)}finally{btn.disabled=false;btn.textContent='Gem som udkast'}
};

$('#resetConfirmation').oninput=e=>{ $('#resetDatabaseBtn').disabled=e.target.value!=='NULSTIL'; };
$('#resetDatabaseBtn').onclick=async()=>{
  if($('#resetConfirmation').value!=='NULSTIL') return;
  const proceed=window.confirm('Sidste chance: Alle job, historiske ansøgninger, udkast, statusser og scanningskontrolpunkter bliver slettet. Indstillinger og CV-filer bevares. Fortsæt?');
  if(!proceed) return;
  const btn=$('#resetDatabaseBtn'); btn.disabled=true; btn.textContent='Nulstiller…';
  try{
    const result=await api('/api/admin/reset-database',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirmation:'NULSTIL'})});
    state.selected=null; state.jobs=[]; state.scanWasRunning=false; window.clearTimeout(state.scanTimer);
    renderScanStatus({phase:'idle',running:false});
    $('#detail').innerHTML='<div class="empty">Vælg et job for at se detaljerne.</div>';
    $('#settingsDialog').close();
    const deleted=Number(result.deleted?.jobs||0);
    toast(`Databasen er nulstillet. ${deleted} jobposter blev slettet.`,false,7000);
    await loadJobs();
  }catch(err){toast(err.message,true,8000)}finally{btn.textContent='Nulstil jobdatabasen';$('#resetConfirmation').value='';btn.disabled=true}
};
$('#settingsBtn').onclick=async()=>{state.config=await api('/api/config');populateSettings();$('#settingsDialog').showModal()};
$('#settingsForm').oninput=e=>{try{
  const input=e.target;
  if(input.id==='profileJson'){
    populateProfileSettings(parseSettingsJson('#profileJson'));
  }else if(input.id==='searchesJson'){
    populateSearchSettings(parseSettingsJson('#searchesJson'));
  }else if(input.id==='scoringJson'){
    parseSettingsJson('#scoringJson');
  }else if(input.closest('[data-panel="profile"], [data-panel="ai"]')){
    const profile=parseSettingsJson('#profileJson');
    profile.person={...(profile.person||{}),full_name:$('#profileName').value,email:$('#profileEmail').value,phone:$('#profilePhone').value,city:$('#profileCity').value};
    profile.career={...(profile.career||{}),headline:$('#profileHeadline').value,summary:$('#profileSummary').value,skills:csvList($('#profileSkills').value)};
    profile.preferences={...(profile.preferences||{}),preferred_terms:csvList($('#profilePreferred').value),negative_terms:csvList($('#profileNegative').value)};
    profile.files={...(profile.files||{}),default_cv:$('#defaultCv').value};
    profile.ai={...(profile.ai||{}),provider:$('#aiProvider').value,...aiConfigFromForm()};
    $('#profileJson').value=JSON.stringify(profile,null,2);
  }else if(input.closest('#searchEntries')){
    const searches=parseSettingsJson('#searchesJson');
    searches.searches=searches.searches.map((x,i)=>({...x,enabled:document.querySelector(`[data-search-enabled="${i}"]`).checked,name:document.querySelector(`[data-search-name="${i}"]`).value,url:document.querySelector(`[data-search-url="${i}"]`).value,incremental:{...(x.incremental||{}),enabled:document.querySelector(`[data-search-incremental="${i}"]`).checked,checkpoint:{...(x.incremental?.checkpoint||{}),kind:String(x.source||'').toLowerCase()==='jobnet'?'published_at':(x.incremental?.checkpoint?.kind||'')}}}));
    $('#searchesJson').value=JSON.stringify(searches,null,2);
  }
  settingsError();
}catch(err){settingsError(`Ugyldige indstillinger: ${err.message}`)}};
$('#settingsForm').onsubmit=async e=>{e.preventDefault();
  if(e.submitter?.value==='cancel'){$('#settingsDialog').close();return}
  try{
  const profile=parseSettingsJson('#profileJson'), searches=parseSettingsJson('#searchesJson'), scoring=parseSettingsJson('#scoringJson');
  await api('/api/config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile,searches,scoring})}); $('#settingsDialog').close(); toast('Indstillinger gemt og job genberegnet'); await loadJobs();
}catch(err){settingsError(`Ugyldige indstillinger: ${err.message}`)}};
document.querySelectorAll('.tab').forEach(btn=>btn.onclick=()=>{document.querySelectorAll('.tab,.tab-panel').forEach(x=>x.classList.remove('active'));btn.classList.add('active');document.querySelector(`[data-panel="${btn.dataset.tab}"]`).classList.add('active')});
loadJobs().catch(e=>toast(e.message,true));
pollScanStatus();
