/* VeriMetrix judging / trust / stakeholder / physical-demo UI */
(function(){
  "use strict";

  window.officerTrustTab = async function(){
    if(!state || !state.officer) return;
    const title=document.getElementById("officerTitle");
    const sub=document.getElementById("officerSub");
    const out=document.getElementById("officerOut");
    title.textContent="Trust & Access";
    sub.textContent="Digital signing readiness, notification providers and stakeholder role provisioning.";
    out.innerHTML='<div class="empty">Loading trust controls…</div>';
    try{
      const results=await Promise.all([
        fetch(API+"/signature-health"),
        fetch(API+"/notification-status"),
        fetch(API+"/stakeholders")
      ]);
      const sig=await results[0].json();
      const not=await results[1].json();
      const users=await results[2].json();
      const badge=function(ok){return '<span class="status '+(ok?"success":"warn")+'">'+(ok?"READY":"ACTION REQUIRED")+'</span>';};
      const rows=(users.users||[]).map(function(x){
        return '<tr><td>'+esc(x.full_name)+'</td><td class="mono">'+esc(x.email)+'</td><td><span class="pill">'+esc(x.role)+'</span></td><td>'+esc(x.district||"")+(x.state?", "+esc(x.state):"")+'</td><td>'+esc(x.active?"YES":"NO")+'</td></tr>';
      }).join("") || '<tr><td colspan="5" class="empty">No stakeholders provisioned yet.</td></tr>';
      out.innerHTML=
        '<div class="grid g4">'+
          '<div class="card kpi"><div class="small muted">RSA certificate signing</div><div class="n" style="font-size:15px">'+badge(!!sig.configured)+'</div><div class="small muted" style="margin-top:7px">'+esc(sig.key_id||"No key configured")+'</div></div>'+
          '<div class="card kpi"><div class="small muted">Transactional email</div><div class="n" style="font-size:15px">'+badge(!!not.email_ready)+'</div><div class="small muted" style="margin-top:7px">'+esc(not.email_provider)+'</div></div>'+
          '<div class="card kpi"><div class="small muted">SMS gateway</div><div class="n" style="font-size:15px">'+badge(!!not.sms_ready)+'</div><div class="small muted" style="margin-top:7px">'+esc(not.sms_provider)+'</div></div>'+
          '<div class="card kpi"><div class="small muted">AI model</div><div class="n" style="font-size:15px" id="aiHealthBadge"><span class="status warn">CHECK</span></div><div class="small muted" style="margin-top:7px">Live model check</div><button class="btn secondary" style="margin-top:10px;width:100%" onclick="runAiHealthCheck()">Test AI</button><div id="aiHealthNote" class="small muted" style="margin-top:6px"></div></div>'+
        '</div>'+
        '<div class="card detail-section" style="margin-top:18px"><h3>Production setup</h3><div class="grid g3" style="margin-top:12px">'+
          '<div class="alert '+(sig.configured?"success":"warn")+'"><b>Digital signature</b><div class="small" style="margin-top:5px">'+esc(not.notes.signing)+'</div></div>'+
          '<div class="alert '+(not.email_ready?"success":"warn")+'"><b>Email</b><div class="small" style="margin-top:5px">'+esc(not.notes.email)+'</div></div>'+
          '<div class="alert '+(not.sms_ready?"success":"warn")+'"><b>SMS</b><div class="small" style="margin-top:5px">'+esc(not.notes.sms)+'</div></div>'+
        '</div></div>'+
        '<div class="card detail-section" style="margin-top:18px"><h3>Stakeholder RBAC registry</h3><div class="small muted" style="margin-top:4px">Roles are bound to authenticated platform identities. Provision an email once; the stakeholder dashboard exposes only its role-specific scope.</div>'+
          '<div class="grid g4" style="margin-top:14px"><input id="stEmail" class="input" placeholder="person@organisation.in"><input id="stName" class="input" placeholder="Full name"><select id="stRole" class="select"><option>OWNER</option><option>LMO</option><option>GATC</option><option>ADMIN</option><option>ENFORCEMENT</option></select><input id="stState" class="input" placeholder="State / jurisdiction"></div>'+
          '<div class="grid g3" style="margin-top:9px"><input id="stDistrict" class="input" placeholder="District"><input id="stGatc" class="input" placeholder="GATC UUID (optional)"><button class="btn primary" onclick="saveStakeholderRole()">Provision stakeholder</button></div>'+
          '<div class="table-wrap" style="margin-top:14px"><table class="table"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Jurisdiction</th><th>Active</th></tr></thead><tbody>'+rows+'</tbody></table></div>'+
        '</div>'+
        '<div class="alert notice" style="margin-top:18px"><b>Key-management boundary:</b> private signing material is never shown in the dashboard. Configure it through project secrets.</div>';
    }catch(e){
      out.innerHTML='<div class="alert danger">'+esc(e.message||"Unable to load trust controls")+'</div>';
    }
  };

  window.runAiHealthCheck = async function(){
    const badgeEl=document.getElementById("aiHealthBadge"),note=document.getElementById("aiHealthNote");
    if(badgeEl)badgeEl.innerHTML='<span class="status warn">CHECKING</span>';
    if(note)note.textContent="Running live AI check…";
    try{
      const r=await fetch(API+"/model-health",{method:"POST"});
      const j=await r.json();
      if(!r.ok||!j.ready)throw Error(j.detail||j.error||"AI model is not ready");
      if(badgeEl)badgeEl.innerHTML='<span class="status success">READY</span>';
      if(note)note.textContent="Model responded "+esc(j.response||"READY")+" in "+esc(j.latency_ms)+" ms.";
    }catch(e){
      if(badgeEl)badgeEl.innerHTML='<span class="status danger">ACTION REQUIRED</span>';
      if(note)note.textContent=e.message||"AI check failed";
    }
  };

  window.saveStakeholderRole = async function(){
    const payload={
      email:document.getElementById("stEmail")?.value.trim(),
      full_name:document.getElementById("stName")?.value.trim(),
      role:document.getElementById("stRole")?.value,
      state:document.getElementById("stState")?.value.trim(),
      district:document.getElementById("stDistrict")?.value.trim(),
      gatc_id:document.getElementById("stGatc")?.value.trim() || null
    };
    if(!payload.email||!payload.full_name){toast("Email and full name are required","danger");return;}
    try{
      const r=await fetch(API+"/stakeholders",{method:"POST",headers:{"content-type":"application/json","X-Vmx-CSRF":state?.officerCsrf||""},body:JSON.stringify(payload)});
      const j=await r.json();
      if(!r.ok) throw Error(j.error||"Could not provision stakeholder");
      toast("Stakeholder role provisioned","success");
      officerTrustTab();
    }catch(e){toast(e.message||"Stakeholder provisioning failed","danger");}
  };

  window.officerPhysicalDemoTab = function(){
    if(!state || !state.officer) return;
    document.getElementById("officerTitle").textContent="Physical-Machine Demo";
    document.getElementById("officerSub").textContent="Run the competition demonstration against a real instrument while preserving the digital evidence trail.";
    const apps=state.officer.dashboard.applications||[];
    document.getElementById("officerOut").innerHTML=
      '<div class="card detail-section">'+
        '<div class="alert notice"><b>Live demo script</b><div class="small" style="margin-top:5px">Place the real instrument on a stable surface → show serial / QR → apply a known test load → enter the observed indication → attach evidence in Field Operations → record this demonstration.</div></div>'+
        '<div class="grid g3" style="margin-top:16px"><input id="demoInstrument" class="input" list="demoInstrumentIds" placeholder="VMX-INS-00001"><datalist id="demoInstrumentIds">'+
          apps.map(function(a){return '<option value="'+esc(a.instrument_id||"")+'">'+esc(a.application_number)+'</option>';}).join("")+
        '</datalist><select id="demoIdentity" class="select"><option value="PASS">Instrument identity matched</option><option value="FAIL">Instrument identity mismatch</option></select><select id="demoSeal" class="select"><option value="PASS">Seal / verification mark intact</option><option value="FAIL">Seal issue observed</option></select></div>'+
        '<div class="grid g4" style="margin-top:10px"><input id="demoNominal" class="input" type="number" step="any" placeholder="Nominal / test load"><input id="demoIndication" class="input" type="number" step="any" placeholder="Observed indication"><input id="demoPermissible" class="input" type="number" min="0" step="any" placeholder="Permissible error"><input id="demoEvidence" class="input" type="number" min="0" step="1" placeholder="Evidence items"></div>'+
        '<div class="grid g2" style="margin-top:10px"><input id="demoOperator" class="input" placeholder="Officer / operator email"><input id="demoScenario" class="input" value="PHYSICAL_MACHINE_VERIFICATION" placeholder="Scenario"></div>'+
        '<div class="form-actions"><span class="small muted">The value is entered from the physical machine; this screen records the evidence trail.</span><button class="btn primary" onclick="submitPhysicalDemo()">Record demonstration</button></div>'+
        '<div class="card detail-section" style="margin-top:18px"><div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start"><div><h3 style="margin:0">Competition Demo Mode</h3><p class="small muted" style="margin:6px 0 0">Admin-only, reversible state controls for the primary demo certificate. Use these only during judging; restore the original record afterward.</p></div><span class="pill">REVERSIBLE</span></div><div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px"><button class="btn secondary" onclick="competitionDemoState(\'VALID\')">VALID</button><button class="btn secondary" onclick="competitionDemoState(\'EXPIRING\')">EXPIRING · 10 DAYS</button><button class="btn secondary" onclick="competitionDemoState(\'EXPIRED\')">EXPIRED</button><button class="btn danger" onclick="competitionDemoState(\'REVOKED\')">REVOKED</button><button class="btn outline" onclick="competitionDemoState(\'RESTORE\')">Restore baseline</button></div><div id="competitionDemoState" class="small muted" style="margin-top:10px"></div></div>'+
        '<div id="demoResult" style="margin-top:12px"></div>'+
      '</div>'+
      '<div id="demoHistory" style="margin-top:18px"></div>';
  };

  window.competitionDemoState = async function(action){
    const box=document.getElementById("competitionDemoState");
    if(box)box.innerHTML="Applying demo state…";
    try{
      const r=await fetch(API+"/competition-demo",{method:"POST",headers:{"content-type":"application/json","X-Vmx-CSRF":state?.officerCsrf||""},body:JSON.stringify({action})});
      const j=await r.json();if(!r.ok)throw Error(j.error||"Could not change demo state");
      const c=j.current||{};
      if(box)box.innerHTML="<b>"+esc(action)+"</b> → certificate "+esc(c.certificate_status)+" · instrument "+esc(c.instrument_status)+" · valid until "+esc(c.valid_until);
      toast("Competition demo state: "+action,"success");
    }catch(e){if(box)box.innerHTML='<span style="color:#b33333">'+esc(e.message||"Demo state change failed")+"</span>";toast(e.message||"Demo state change failed","danger")}
  };

  window.submitPhysicalDemo = async function(){
    const instrument_id=document.getElementById("demoInstrument")?.value.trim();
    const nominal=Number(document.getElementById("demoNominal")?.value);
    const indication=Number(document.getElementById("demoIndication")?.value);
    const permissible=Number(document.getElementById("demoPermissible")?.value);
    if(!instrument_id||!Number.isFinite(nominal)||!Number.isFinite(indication)||!Number.isFinite(permissible)){
      toast("Instrument, nominal, indication and permissible error are required","danger");return;
    }
    const measurementResult=Math.abs(indication-nominal)<=permissible ? "PASS" : "FAIL";
    const checklist=[
      {item:"Instrument identity and serial",result:document.getElementById("demoIdentity").value},
      {item:"Seal / verification mark",result:document.getElementById("demoSeal").value},
      {item:"Observed indication within permissible error",result:measurementResult}
    ];
    try{
      const r=await fetch(API+"/physical-demo",{method:"POST",headers:{"content-type":"application/json","X-Vmx-CSRF":state?.officerCsrf||""},body:JSON.stringify({
        instrument_id,
        operator_email:document.getElementById("demoOperator")?.value.trim(),
        test_scenario:document.getElementById("demoScenario")?.value.trim() || "PHYSICAL_MACHINE_VERIFICATION",
        checklist,
        measurements:[{nominal_value:nominal,indication,permissible_error:permissible,result:measurementResult}],
        evidence_count:Number(document.getElementById("demoEvidence")?.value||0)
      })});
      const j=await r.json();
      if(!r.ok) throw Error(j.error||"Could not record physical demonstration");
      document.getElementById("demoResult").innerHTML='<div class="alert '+(j.run.result==="PASS"?"success":"danger")+'"><b>Demo run '+esc(j.run.result)+'</b> · error '+esc(indication-nominal)+' against ±'+esc(permissible)+' · Run '+esc(j.run.id)+'</div>';
      toast("Physical demonstration recorded","success");
    }catch(e){toast(e.message||"Physical demo failed","danger");}
  };

  window.stakeholderPortal = async function(){
    const out=document.getElementById("app");
    out.innerHTML=nav()+'<section class="section"><div class="container"><div class="pagehead"><span class="pill">STAKEHOLDER ACCESS</span><h1>Role-based national workspace</h1><p>Sign in with the platform identity assigned to an OWNER, LMO, GATC, ADMIN or ENFORCEMENT role.</p></div><div id="stakeholderOut" class="card" style="padding:24px;max-width:900px;margin:30px auto 0"><div class="empty">Checking stakeholder access…</div></div></div></section>'+footer();
    try{
      const r=await fetch(API+"/stakeholder-dashboard");
      const j=await r.json();
      const box=document.getElementById("stakeholderOut");
      if(!r.ok){
        if(r.status===401 || j.error==="login_required"){
          box.innerHTML='<div class="alert notice"><b>Sign in to open the Stakeholder Portal.</b><div class="small" style="margin-top:6px">Use your VeriMetrix stakeholder account. After sign-in, the portal will return you here automatically.</div><button class="btn primary" style="margin-top:14px" data-next="/login?next=%2Fstakeholder" onclick="location.href=this.dataset.next">Sign in securely →</button></div>';
        }else{
          box.innerHTML='<div class="alert danger"><b>Stakeholder access is not provisioned.</b><div class="small" style="margin-top:6px">'+esc(j.error||"Ask an administrator to assign your stakeholder role.")+'</div></div>';
        }
        return;
      }
      const cards=Object.keys(j.metrics||{}).map(function(k){
        return '<div class="card kpi"><div class="small muted">'+esc(k.replace(/_/g," "))+'</div><div class="n">'+esc(j.metrics[k])+'</div></div>';
      }).join("");
      box.innerHTML='<div class="detail-head"><div><div class="small muted">SIGNED-IN STAKEHOLDER</div><h3 style="margin:6px 0">'+esc(j.identity.full_name)+'</h3><div class="small muted">'+esc(j.identity.email)+'</div></div><span class="pill">'+esc(j.identity.role)+'</span></div><div class="grid g3" style="margin-top:20px">'+cards+'</div><div class="alert notice" style="margin-top:18px"><b>Scoped access</b><div class="small" style="margin-top:5px">Data is filtered by the provisioned stakeholder role and jurisdiction.</div></div>';
    }catch(e){
      document.getElementById("stakeholderOut").innerHTML='<div class="alert danger">'+esc(e.message||"Stakeholder access check failed")+'</div>';
    }
  };
})();