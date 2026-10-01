/* VeriMetrix national field / GATC / enforcement client layer */
(function(){
  "use strict";

  let secureQueueCache=[];
  let secureQueueReady=null;
  function openSecureQueueDb(){
    return new Promise((resolve,reject)=>{
      if(!("indexedDB" in window)){reject(Error("Encrypted offline storage is unavailable"));return}
      const req=indexedDB.open("verimetrix-secure-field",1);
      req.onupgradeneeded=()=>{const d=req.result;if(!d.objectStoreNames.contains("meta"))d.createObjectStore("meta");if(!d.objectStoreNames.contains("queue"))d.createObjectStore("queue")}
      req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error||Error("IndexedDB unavailable"));
    })
  }
  async function cryptoKey(){
    const db=await openSecureQueueDb();
    const existing=await new Promise((resolve,reject)=>{const tx=db.transaction("meta","readonly"),g=tx.objectStore("meta").get("key");g.onsuccess=()=>resolve(g.result||null);g.onerror=()=>reject(g.error||Error("Secure key read failed"))});
    if(existing)return existing;
    const key=await crypto.subtle.generateKey({name:"AES-GCM",length:256},false,["encrypt","decrypt"]);
    await new Promise((resolve,reject)=>{const tx=db.transaction("meta","readwrite"),p=tx.objectStore("meta").put(key,"key");p.onsuccess=()=>resolve();p.onerror=()=>reject(p.error||Error("Secure key write failed"))});
    return key;
  }
  async function initSecureQueue(){
    if(secureQueueReady)return secureQueueReady;
    secureQueueReady=(async()=>{
      try{
        const db=await openSecureQueueDb(),key=await cryptoKey();
        const raw=await new Promise((resolve,reject)=>{const tx=db.transaction("queue","readonly"),g=tx.objectStore("queue").get("pending");g.onsuccess=()=>resolve(g.result||null);g.onerror=()=>reject(g.error)});
        if(raw){
          const iv=new Uint8Array(raw.iv),data=new Uint8Array(raw.data),plain=await crypto.subtle.decrypt({name:"AES-GCM",iv},key,data);
          secureQueueCache=JSON.parse(new TextDecoder().decode(plain))||[];
        }else{
          try{secureQueueCache=JSON.parse(localStorage.getItem("vmx_field_queue")||"[]")||[];await persistSecureQueue();localStorage.removeItem("vmx_field_queue")}catch(_){secureQueueCache=[]}
        }
      }catch(e){secureQueueCache=[];window.__vmxQueueError=String(e.message||e)}
      return secureQueueCache;
    })();
    return secureQueueReady;
  }
  async function persistSecureQueue(){
    const db=await openSecureQueueDb(),key=await cryptoKey(),iv=crypto.getRandomValues(new Uint8Array(12)),plain=new TextEncoder().encode(JSON.stringify(secureQueueCache)),data=new Uint8Array(await crypto.subtle.encrypt({name:"AES-GCM",iv},key,plain));
    await new Promise((resolve,reject)=>{const tx=db.transaction("queue","readwrite"),s=tx.objectStore("queue");const p=s.put({iv:Array.from(iv),data:Array.from(data),updated_at:new Date().toISOString()},"pending");p.onsuccess=()=>resolve();p.onerror=()=>reject(p.error)});
  }
  function offlineQueue(){return secureQueueCache.slice()}
  function setOfflineQueue(q){secureQueueCache=Array.isArray(q)?q.slice():[];persistSecureQueue().catch(e=>window.__vmxQueueError=String(e.message||e))}
  async function secureQueuePush(item){await initSecureQueue();const q=offlineQueue();q.push(item);setOfflineQueue(q)}
  async function syncOfflineQueue(){
    await initSecureQueue();
    if(!navigator.onLine)return;
    const q=offlineQueue(),remaining=[];
    for(const item of q){
      try{
        const r=await fetch(API+"/field",{method:"POST",headers:{"content-type":"application/json","X-Vmx-CSRF":state?.officerCsrf||""},body:JSON.stringify({...item,offline:true})});
        if(!r.ok)throw Error("sync failed");
      }catch(_){remaining.push(item)}
    }
    setOfflineQueue(remaining);
    await new Promise(r=>setTimeout(r,0));
  }
  window.syncOfflineQueue=syncOfflineQueue;
  window.addEventListener("online",async()=>{
    await syncOfflineQueue();
    if(state.page==="officer"&&state.officer&&typeof officerFieldTab==="function")officerFieldTab();
  });

  async function officerFieldTab(){
    if(!state.officer)return;
    document.getElementById("officerTitle").textContent="Field Operations";
    document.getElementById("officerSub").textContent="Mobile-first inspection workspace with offline capture, GPS and photo evidence.";
    const out=document.getElementById("officerOut");
    out.innerHTML='<div class="empty">Loading field worklist…</div>';
    try{
      await syncOfflineQueue();
      const r=await fetch(API+"/field"),j=await r.json();
      if(!r.ok)throw Error(j.error||"Unable to load field worklist");
      state.fieldData=j;renderFieldOps(j);
    }catch(e){out.innerHTML='<div class="alert danger">'+esc(e.message||"Unable to load field operations")+'</div>'}
  }
  window.officerFieldTab=officerFieldTab;

  function renderFieldOps(j){
    const out=document.getElementById("officerOut"),list=j.inspections||[],q=offlineQueue();
    out.innerHTML='<div class="grid g4">'+[
      ["Assigned / scheduled",list.filter(x=>x.assignment_status==="ASSIGNED"||x.status==="SCHEDULED").length],
      ["Completed",list.filter(x=>x.status==="COMPLETED").length],
      ["Evidence items",(j.evidence||[]).length],
      ["Offline queued",q.length]
    ].map(x=>'<div class="card kpi"><div class="small muted">'+x[0]+'</div><div class="n">'+esc(x[1])+'</div></div>').join("")+
    '</div><div class="alert '+(navigator.onLine?"notice":"warn")+'" style="margin-top:18px"><b>'+
    (navigator.onLine?"Online sync available":"Offline mode active")+
    '</b><div class="small" style="margin-top:4px">'+
    (navigator.onLine?"Completed field actions can sync immediately.":"Changes are stored on this device and will sync automatically when connectivity returns.")+
    '</div></div><div class="card detail-section" style="margin-top:18px"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center"><div><h3 style="margin:0">Field worklist</h3><div class="small muted">Open a job to capture inspection observations and evidence.</div></div><button class="btn secondary" onclick="syncOfflineQueue().then(()=>officerFieldTab())">Sync offline queue</button></div><div class="table-wrap" style="margin-top:12px"><table class="table"><thead><tr><th>Application</th><th>Instrument</th><th>Date / Time</th><th>Assignee</th><th>Status</th><th></th></tr></thead><tbody>'+
    ((list.map(x=>'<tr><td class="mono">'+esc(x.application_number)+'</td><td>'+esc(x.serial_number||"—")+'</td><td>'+dateFmt(x.scheduled_date)+' '+esc(x.scheduled_time||"")+'</td><td>'+esc(x.assignee_name||x.officer_name||"Unassigned")+'<div class="small muted">'+esc(x.assignee_role||"")+'</div></td><td><span class="status '+statusClass(x.completion_result||x.status)+'">'+esc(x.completion_result||x.status)+'</span></td><td><button class="btn secondary" onclick="openFieldForm(\''+esc(x.id)+'\')">Open job</button></td></tr>').join(""))||
    '<tr><td colspan="6" class="empty">No scheduled inspections.</td></tr>')+
    '</tbody></table></div></div><div id="fieldForm" style="margin-top:18px"><div class="card empty">Select a field job to begin.</div></div>';
  }

  function openFieldForm(id){
    const x=(state.fieldData?.inspections||[]).find(v=>v.id===id);if(!x)return;
    document.getElementById("fieldForm").innerHTML='<div class="card detail-section"><div class="detail-head"><div><div class="small muted">FIELD INSPECTION</div><h3 style="margin:6px 0">'+esc(x.application_number)+'</h3><div class="small muted">'+esc(x.instrument_type)+' · serial '+esc(x.serial_number)+'</div></div><span class="status '+statusClass(x.completion_result||x.status)+'">'+esc(x.completion_result||x.status)+'</span></div><div class="grid g3" style="margin-top:18px"><div><div class="small muted">INSTRUMENT ID</div><b class="mono">'+esc(x.instrument_id||"—")+'</b></div><div><div class="small muted">LOCATION</div><b>'+esc(x.location||x.installation_location||"—")+'</b></div><div><div class="small muted">ASSIGNED TO</div><b>'+esc(x.assignee_name||x.officer_name||"—")+'</b></div></div><div class="grid g2" style="margin-top:18px"><div><label class="label">Inspection result<select id="fieldResult" class="select"><option>PASS</option><option>FAIL</option><option>PENDING</option></select></label><label class="label" style="margin-top:12px">Officer remarks<textarea id="fieldRemarks" class="textarea" placeholder="Observations, measurements, seal condition, discrepancies…"></textarea></label></div><div><label class="label">Field photograph<input id="fieldPhoto" class="input" type="file" accept="image/jpeg,image/png" capture="environment"></label><div class="grid g2" style="margin-top:12px"><label class="label">Latitude<input id="fieldLat" class="input" inputmode="decimal" placeholder="e.g. 18.5204"></label><label class="label">Longitude<input id="fieldLng" class="input" inputmode="decimal" placeholder="e.g. 73.8567"></label></div><button class="btn outline" style="margin-top:8px" onclick="useFieldLocation()">Use device location</button></div></div><div style="margin-top:18px"><div class="small muted" style="margin-bottom:8px">Inspection checklist</div><div class="check-grid"><label><input type="checkbox" class="fieldCheck" value="identity"> Instrument identity & serial match</label><label><input type="checkbox" class="fieldCheck" value="condition"> Physical condition acceptable</label><label><input type="checkbox" class="fieldCheck" value="accuracy"> Accuracy / performance checked</label><label><input type="checkbox" class="fieldCheck" value="seal"> Seals / verification mark checked</label></div></div><div class="form-actions"><span class="small muted">Actions are signed to the officer audit trail and can be queued offline.</span><div class="form-actions-right"><button class="btn outline" onclick="saveFieldInspection(\''+esc(x.id)+'\',true)">Save offline</button><button class="btn primary" onclick="saveFieldInspection(\''+esc(x.id)+'\',false)">Sync inspection</button></div></div></div>';
    window.__fieldJob=x;
    renderMeasurementPanel(x);
  }
  window.openFieldForm=openFieldForm;

  async function renderMeasurementPanel(x){
    const host=document.getElementById("fieldForm");if(!host)return;
    const panel=document.createElement("div");panel.id="measurementPanel";panel.className="card detail-section";panel.style.marginTop="18px";
    panel.innerHTML='<div style="display:flex;justify-content:space-between;align-items:center;gap:10px"><div><h3 style="margin:0">Measurement evidence</h3><div class="small muted">Record nominal value, indication, error and permissible error for an auditable verification point.</div></div><span class="pill">MEASUREMENT ENGINE</span></div><div id="measurementRows" class="table-wrap" style="margin-top:12px"><div class="empty">Loading measurements…</div></div><div class="grid g4" style="margin-top:14px"><input id="mName" class="input" placeholder="Point / test name"><input id="mNominal" class="input" type="number" step="any" placeholder="Nominal value"><input id="mIndication" class="input" type="number" step="any" placeholder="Indication"><input id="mPermissible" class="input" type="number" step="any" min="0" placeholder="Permissible error"></div><div class="grid g4" style="margin-top:9px"><input id="mLoad" class="input" type="number" step="any" placeholder="Test load (optional)"><input id="mDivision" class="input" type="number" step="any" placeholder="Division / resolution"><input id="mUnit" class="input" value="unit" placeholder="Unit"><input id="mStandard" class="input" placeholder="Reference standard"></div><div class="grid g3" style="margin-top:9px"><select id="mRulePack" class="select"><option value="">Rule pack (select)</option></select><select id="mStandardId" class="select"><option value="">Traceable standard (select)</option></select><input id="mDeviceId" class="input" placeholder="Capture device ID"></div><div class="grid g2" style="margin-top:9px"><input id="mCondition" class="input" placeholder="Observed condition e.g. temperature / setup"><button class="btn primary" onclick="addFieldMeasurement()">Record measurement</button></div><div id="measurementMsg" style="margin-top:10px"></div>';
    host.appendChild(panel);
    await loadFieldMeasurements(x.id);
    try{
      const [rr,sr]=await Promise.all([fetch(API+"/rules"),fetch(API+"/standards")]);
      const rj=await rr.json(),sj=await sr.json();
      const rp=document.getElementById("mRulePack"),st=document.getElementById("mStandardId");
      if(rp) (rj.rules||[]).forEach(v=>{const o=document.createElement("option");o.value=v.id;o.textContent=v.code_reference+" · "+v.version+" · ±"+v.permissible_error+" "+v.unit;rp.appendChild(o)});
      if(st) (sj.standards||[]).forEach(v=>{const o=document.createElement("option");o.value=v.id;o.textContent=v.standard_id+" · "+v.name+" · due "+(v.calibration_due||"—");st.appendChild(o)});
    }catch(_){/* selectors remain explicit and fail-safe */}
  }
  async function loadFieldMeasurements(inspectionId){
    const out=document.getElementById("measurementRows");if(!out)return;
    try{const r=await fetch(API+"/measurements?inspection_id="+encodeURIComponent(inspectionId));const j=await r.json();if(!r.ok)throw Error(j.error||"Unable to load measurements");const rows=j.measurements||[];
      out.innerHTML=rows.length?'<table class="table"><thead><tr><th>Point</th><th>Nominal</th><th>Indication</th><th>Error</th><th>Permissible</th><th>Result</th></tr></thead><tbody>'+rows.map(m=>'<tr><td>'+esc(m.measurement_name)+'</td><td>'+esc(m.nominal_value)+' '+esc(m.unit)+'</td><td>'+esc(m.indication)+'</td><td>'+esc(m.error_value)+'</td><td>±'+esc(m.permissible_error)+'</td><td><span class="status '+statusClass(m.result)+'">'+esc(m.result)+'</span></td></tr>').join("")+'</tbody></table>':'<div class="empty">No measurements recorded yet.</div>';
    }catch(e){out.innerHTML='<div class="alert danger">'+esc(e.message||"Unable to load measurements")+'</div>'}
  }
  window.addFieldMeasurement=async function(){
    const x=window.__fieldJob;if(!x){toast("Select a field job first","danger");return}
    const payload={action:"sync_measurement",inspection_id:x.id,application_id:x.application_id,measurement_name:document.getElementById("mName")?.value.trim()||"Verification point",nominal_value:document.getElementById("mNominal")?.value,indication:document.getElementById("mIndication")?.value,permissible_error:document.getElementById("mPermissible")?.value,test_load:document.getElementById("mLoad")?.value,division:document.getElementById("mDivision")?.value,unit:document.getElementById("mUnit")?.value.trim()||"unit",reference_standard:document.getElementById("mStandard")?.value.trim(),rule_pack_id:document.getElementById("mRulePack")?.value||"",standard_id:document.getElementById("mStandardId")?.value||"",method_version:"field-template-2026.1",decision_rule:"absolute error <= permissible error",device_id:document.getElementById("mDeviceId")?.value.trim()||"browser-field",observed_conditions:{condition:document.getElementById("mCondition")?.value.trim()||""},client_sync_id:crypto.randomUUID?crypto.randomUUID():String(Date.now())};
    if(payload.nominal_value===""||payload.indication===""||payload.permissible_error===""){toast("Nominal, indication and permissible error are required","danger");return}
    const msg=document.getElementById("measurementMsg");if(msg)msg.innerHTML='<div class="empty">Recording measurement…</div>';
    try{const r=await fetch(API+"/field",{method:"POST",headers:{"content-type":"application/json","X-Vmx-CSRF":state?.officerCsrf||""},body:JSON.stringify(payload)});const j=await r.json();if(!r.ok)throw Error(j.error||"Measurement save failed");if(msg)msg.innerHTML='<div class="alert '+(j.measurement.result==="PASS"?"success":"danger")+'"><b>'+esc(j.measurement.result)+'</b> · error '+esc(j.measurement.error_value)+' against ±'+esc(j.measurement.permissible_error)+'</div>';["mName","mNominal","mIndication","mPermissible","mLoad","mDivision","mStandard","mCondition"].forEach(id=>{const e=document.getElementById(id);if(e)e.value=""});await loadFieldMeasurements(x.id);
    }catch(e){
      await secureQueuePush(payload);if(msg)msg.innerHTML='<div class="alert warn">Offline/network save queued in encrypted device storage for automatic synchronization.</div>';toast("Measurement encrypted and queued offline","warn");
    }
  };

  function useFieldLocation(){
    if(!navigator.geolocation){toast("Geolocation is not supported by this device","danger");return}
    navigator.geolocation.getCurrentPosition(p=>{
      document.getElementById("fieldLat").value=p.coords.latitude.toFixed(7);
      document.getElementById("fieldLng").value=p.coords.longitude.toFixed(7);
      toast("Device location captured","success");
    },e=>toast(e.message||"Location permission failed","danger"),{enableHighAccuracy:true,timeout:10000,maximumAge:0});
  }
  window.useFieldLocation=useFieldLocation;

  function imageDataUrl(file){
    return new Promise((resolve,reject)=>{
      if(!file)return resolve(null);
      const rd=new FileReader();
      rd.onerror=()=>reject(Error("Could not read photo"));
      rd.onload=()=>{
        const img=new Image();
        img.onerror=()=>reject(Error("Could not process photo"));
        img.onload=()=>{
          const max=1280,scale=Math.min(1,max/Math.max(img.width,img.height)),c=document.createElement("canvas");
          c.width=Math.max(1,Math.round(img.width*scale));c.height=Math.max(1,Math.round(img.height*scale));
          c.getContext("2d").drawImage(img,0,0,c.width,c.height);
          resolve(c.toDataURL("image/jpeg",.74));
        };
        img.src=rd.result;
      };
      rd.readAsDataURL(file);
    });
  }

  async function saveFieldInspection(id,offline){
    const x=(state.fieldData?.inspections||[]).find(v=>v.id===id);if(!x)return;
    const result=document.getElementById("fieldResult").value;
    const remarks=document.getElementById("fieldRemarks").value.trim();
    const latitude=document.getElementById("fieldLat").value.trim();
    const longitude=document.getElementById("fieldLng").value.trim();
    const checklist=Array.from(document.querySelectorAll(".fieldCheck")).map(c=>({item:c.value,result:c.checked?"PASS":"NOT_CHECKED"}));
    const file=document.getElementById("fieldPhoto")?.files?.[0];
    let photo_data_url=null;
    try{photo_data_url=await imageDataUrl(file)}catch(e){toast(e.message,"danger");return}
    const item={action:"sync_inspection",inspection_id:id,application_id:x.application_id,completion_result:result,remarks,checklist,latitude:latitude||null,longitude:longitude||null,captured_at:new Date().toISOString(),photo_data_url,client_sync_id:(crypto.randomUUID?crypto.randomUUID():String(Date.now())+Math.random()),inspector_name:"Mobile Field Officer"};
    if(offline||!navigator.onLine){
      await secureQueuePush(item);toast("Field inspection encrypted on this device","success");return;
    }
    try{
      const r=await fetch(API+"/field",{method:"POST",headers:{"content-type":"application/json","X-Vmx-CSRF":state?.officerCsrf||""},body:JSON.stringify(item)});
      const j=await r.json();if(!r.ok)throw Error(j.error||"Field sync failed");
      toast("Field inspection synced","success");await officerFieldTab();
    }catch(e){
      const q=offlineQueue();q.push(item);setOfflineQueue(q);toast("Network failed — saved offline for automatic retry","warn");
    }
  }
  window.saveFieldInspection=saveFieldInspection;

  async function loadGatcRecommendations(applicationId){
    const box=document.getElementById("gatcRecommendations");
    if(!box||!applicationId)return;
    box.innerHTML='<div class="empty">Computing eligible GATCs…</div>';
    try{
      const r=await fetch(API+"/gatc-recommend?application_id="+encodeURIComponent(applicationId)),j=await r.json();
      if(!r.ok)throw Error(j.error||"Unable to compute GATC recommendations");
      state.gatcRecommendations=j;
      const inspection=(state.fieldData?.inspections||[]).find(x=>x.application_id===applicationId);
      const rows=(j.candidates||[]).map(function(x,i){
        return '<div class="card detail-section" style="border-left:4px solid '+(i===0?"#294497":"#dfe6f0")+'"><div style="display:flex;justify-content:space-between;gap:10px;align-items:flex-start"><div><b>'+esc(x.centre_code)+' · '+esc(x.name)+'</b><div class="small muted" style="margin-top:4px">'+esc(x.district)+', '+esc(x.state)+'</div></div><span class="status '+(i===0?"success":"notice")+'">'+(i===0?"RECOMMENDED ":"CANDIDATE ")+esc(x.score)+'</span></div><div class="small muted" style="margin-top:9px">'+esc((x.reasons||[]).join(" · "))+'</div><div class="small" style="margin-top:7px"><b>Load:</b> '+esc(x.current_load)+' / '+esc(x.daily_capacity||20)+' · '+esc(x.utilization)+'% of nominal daily capacity</div>'+(inspection?'<button class="btn primary" style="margin-top:10px" onclick="allocateRecommendedGatc(\''+esc(inspection.id)+'\',\''+esc(x.id)+'\')">Allocate this centre</button>': '<div class="small muted" style="margin-top:9px">Schedule an inspection first to allocate this application.</div>')+'</div>';
      }).join("");
      box.innerHTML=rows||'<div class="alert warn">No eligible GATC found for this instrument category. Review centre approvals before allocation.</div>';
    }catch(e){box.innerHTML='<div class="alert danger">'+esc(e.message||"GATC recommendation failed")+'</div>'}
  }
  window.loadGatcRecommendations=loadGatcRecommendations;

  async function allocateRecommendedGatc(inspectionId,gatcId){
    try{
      const r=await fetch(API+"/gatc",{method:"PUT",headers:{"content-type":"application/json","X-Vmx-CSRF":state?.officerCsrf||""},body:JSON.stringify({inspection_id:inspectionId,gatc_id:gatcId,assignee_name:"GATC Technician",assignee_role:"GATC",assigned_by:"Smart Allocation Engine"})});
      const j=await r.json();if(!r.ok)throw Error(j.error||"Could not allocate recommended GATC");
      toast("Recommended GATC allocated","success");await officerGatcTab();
    }catch(e){toast(e.message||"GATC allocation failed","danger")}
  }
  window.allocateRecommendedGatc=allocateRecommendedGatc;

  async function officerGatcTab(){
    if(!state.officer)return;
    document.getElementById("officerTitle").textContent="GATC Network";
    document.getElementById("officerSub").textContent="Approved centre registry and verification-job allocation.";
    const out=document.getElementById("officerOut");out.innerHTML='<div class="empty">Loading GATC network…</div>';
    try{
      const [a,b]=await Promise.all([fetch(API+"/gatc"),fetch(API+"/field")]);
      const j=await a.json(),f=await b.json();
      if(!a.ok)throw Error(j.error||"Unable to load GATC network");
      state.gatcData=j;state.fieldData=f;
      out.innerHTML='<div class="grid g3">'+[["Active centres",(j.centres||[]).filter(x=>x.status==="ACTIVE").length],["Assigned jobs",(j.assignments||[]).filter(x=>x.status==="ASSIGNED"||x.status==="IN_PROGRESS").length],["Available field jobs",(f.inspections||[]).filter(x=>!x.assignment_status).length]].map(x=>'<div class="card kpi"><div class="small muted">'+x[0]+'</div><div class="n">'+esc(x[1])+'</div></div>').join("")+'</div><div class="grid g2" style="margin-top:18px"><div class="card detail-section"><h3>Register / update GATC</h3><div class="grid g2" style="margin-top:12px"><input id="gatcCode" class="input" placeholder="GATC-MH-001"><input id="gatcName" class="input" placeholder="Centre name"><input id="gatcState" class="input" placeholder="State"><input id="gatcDistrict" class="input" placeholder="District"></div><input id="gatcEmail" class="input" style="margin-top:9px" placeholder="Contact email"><input id="gatcCats" class="input" style="margin-top:9px" placeholder="Approved categories, comma separated"><button class="btn primary" style="margin-top:10px" onclick="saveGatc()">Save centre</button></div><div class="card detail-section"><h3>Allocate verification job</h3><select id="gatcInspection" class="select" style="margin-top:12px">'+(f.inspections||[]).filter(x=>x.status==="SCHEDULED").map(x=>'<option value="'+esc(x.id)+'">'+esc(x.application_number)+' · '+esc(x.serial_number)+' · '+esc(x.scheduled_date||"")+'</option>').join("")+'</select><select id="gatcCentre" class="select" style="margin-top:9px">'+(j.centres||[]).filter(x=>x.status==="ACTIVE").map(x=>'<option value="'+esc(x.id)+'">'+esc(x.centre_code)+' · '+esc(x.name)+'</option>').join("")+'</select><input id="gatcAssignee" class="input" style="margin-top:9px" placeholder="Assigned officer / technician"><button class="btn secondary" style="margin-top:10px" onclick="assignGatc()">Allocate job</button></div></div><div class="card detail-section" style="margin-top:18px"><h3>GATC centres</h3><div class="table-wrap" style="margin-top:10px"><table class="table"><thead><tr><th>Centre</th><th>Location</th><th>Categories</th><th>Status</th></tr></thead><tbody>'+((j.centres||[]).map(x=>'<tr><td><b>'+esc(x.name)+'</b><div class="mono small">'+esc(x.centre_code)+'</div></td><td>'+esc(x.district)+', '+esc(x.state)+'</td><td class="small">'+esc((x.approved_categories||[]).join(", ")||"—")+'</td><td>'+esc(x.status)+'</td></tr>').join("")||'<tr><td colspan="4" class="empty">No GATC centres registered.</td></tr>')+'</tbody></table></div></div>';
    const smartApps=(f.inspections||[]).filter(x=>x.status==="SCHEDULED");
      out.insertAdjacentHTML("afterbegin",'<div class="card detail-section" style="margin-bottom:18px"><div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start"><div><h3 style="margin:0">Smart GATC Allocation</h3><p class="small muted" style="margin:6px 0 0">Eligibility first, then jurisdiction alignment and current workload. The engine shows its decision basis instead of hiding the recommendation.</p></div><span class="pill">DECISION SUPPORT</span></div><div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px"><select id="smartGatcApp" class="select" style="flex:1;min-width:260px"><option value="">Select a scheduled inspection</option>'+smartApps.map(x=>'<option value="'+esc(x.application_id)+'">'+esc(x.application_number)+' · '+esc(x.serial_number)+'</option>').join("")+'</select><button class="btn primary" onclick="loadGatcRecommendations(document.getElementById(\'smartGatcApp\').value)">Find eligible GATCs</button></div><div id="gatcRecommendations" style="margin-top:14px"><div class="empty">Select a scheduled inspection to compute recommendations.</div></div></div>');
    }catch(e){out.innerHTML='<div class="alert danger">'+esc(e.message||"Unable to load GATC network")+'</div>'}
  }
  window.officerGatcTab=officerGatcTab;

  async function saveGatc(){
    const payload={centre_code:document.getElementById("gatcCode").value.trim(),name:document.getElementById("gatcName").value.trim(),state:document.getElementById("gatcState").value.trim(),district:document.getElementById("gatcDistrict").value.trim(),contact_email:document.getElementById("gatcEmail").value.trim(),approved_categories:document.getElementById("gatcCats").value.split(",").map(x=>x.trim()).filter(Boolean)};
    try{
      const r=await fetch(API+"/gatc",{method:"POST",headers:{"content-type":"application/json","X-Vmx-CSRF":state?.officerCsrf||""},body:JSON.stringify(payload)}),j=await r.json();
      if(!r.ok)throw Error(j.error||"Could not save GATC");toast("GATC centre saved","success");officerGatcTab();
    }catch(e){toast(e.message,"danger")}
  }
  window.saveGatc=saveGatc;

  async function assignGatc(){
    const payload={inspection_id:document.getElementById("gatcInspection").value,gatc_id:document.getElementById("gatcCentre").value,assignee_name:document.getElementById("gatcAssignee").value.trim()||"GATC Technician",assignee_role:"GATC",assigned_by:"Verification Officer"};
    try{
      const r=await fetch(API+"/gatc",{method:"PUT",headers:{"content-type":"application/json","X-Vmx-CSRF":state?.officerCsrf||""},body:JSON.stringify(payload)}),j=await r.json();
      if(!r.ok)throw Error(j.error||"Could not allocate job");toast("Verification job allocated to GATC","success");officerGatcTab();
    }catch(e){toast(e.message,"danger")}
  }
  window.assignGatc=assignGatc;

  function useReportLocation(){
    if(!navigator.geolocation){toast("Geolocation is not supported by this device","danger");return}
    navigator.geolocation.getCurrentPosition(p=>{
      document.getElementById("reportLat").value=p.coords.latitude.toFixed(7);
      document.getElementById("reportLng").value=p.coords.longitude.toFixed(7);
      toast("Device location captured","success");
    },e=>toast(e.message||"Location permission failed","danger"),{enableHighAccuracy:true,timeout:10000,maximumAge:0});
  }
  window.useReportLocation=useReportLocation;

  function report(){
    const instrument=new URLSearchParams(location.search).get("instrument")||"";
    return '<section class="section"><div class="container">'+pageHead("CONSUMER PROTECTION","Report a compliance issue","Report a suspected expired certificate, damaged seal, tampered QR, wrong instrument identity or measurement concern. No login is required.")+
    '<div class="card formcard" style="max-width:820px;margin:30px auto 0"><div class="grid g2"><label class="label">QR / Instrument ID<input id="reportInstrument" class="input" value="'+esc(instrument)+'" placeholder="VMXQR-... or VMX-INS-00001"></label><label class="label">Issue type<select id="reportType" class="select"><option value="tampered_qr">Tampered QR / suspicious label</option><option value="damaged_seal">Damaged or missing seal</option><option value="wrong_instrument">QR belongs to another instrument</option><option value="fake_certificate">Suspicious certificate</option><option value="expired">Expired verification</option><option value="under_measurement">Suspected under-measurement</option><option value="other">Other compliance issue</option></select></label></div><label class="label" style="margin-top:14px">What did you observe?<textarea id="reportDescription" class="textarea" placeholder="Describe what happened, where and when…"></textarea></label><div class="grid g2" style="margin-top:14px"><label class="label">Evidence photo<input id="reportPhoto" class="input" type="file" accept="image/jpeg,image/png" capture="environment"></label><label class="label">Contact (optional)<input id="reportContact" class="input" placeholder="Email or phone for follow-up"></label></div><div class="grid g2" style="margin-top:12px"><input id="reportLat" class="input" placeholder="Latitude"><input id="reportLng" class="input" placeholder="Longitude"></div><button class="btn outline" style="margin-top:10px" onclick="useReportLocation()">Use device location</button><div class="form-actions"><span class="small muted">Your report is routed to an auditable compliance queue.</span><button class="btn primary" onclick="submitReport()">Submit report</button></div><div id="reportMsg" style="margin-top:12px"></div></div></div></section>';
  }
  window.report=report;

  async function submitReport(){
    const type=document.getElementById("reportType").value;
    const description=document.getElementById("reportDescription").value.trim();
    const token=document.getElementById("reportInstrument").value.trim();
    const contact=document.getElementById("reportContact").value.trim();
    const latitude=document.getElementById("reportLat").value.trim();
    const longitude=document.getElementById("reportLng").value.trim();
    if(!description){toast("Describe the compliance issue","danger");return}
    let photo_data_url=null;
    const f=document.getElementById("reportPhoto")?.files?.[0];
    try{photo_data_url=await imageDataUrl(f)}catch(e){toast(e.message,"danger");return}
    const out=document.getElementById("reportMsg");
    out.innerHTML='<div class="empty">Submitting report…</div>';
    try{
      const r=await fetch(API+"/reports",{method:"POST",headers:{"content-type":"application/json","X-Vmx-CSRF":state?.officerCsrf||""},body:JSON.stringify({issue_type:type,description,qr_identifier:token,reporter_contact:contact,latitude:latitude||null,longitude:longitude||null,photo_data_url})});
      const j=await r.json();if(!r.ok)throw Error(j.error||"Could not submit report");
      out.innerHTML='<div class="alert success"><b>Report received.</b><div class="small" style="margin-top:5px">Reference: <span class="mono">'+esc(j.report?.id||"—")+'</span></div><div class="small" style="margin-top:5px">'+esc(j.message||"The responsible office can now review it.")+'</div></div>';
      document.getElementById("reportDescription").value="";
      document.getElementById("reportPhoto").value="";
      toast("Compliance report submitted","success");
    }catch(e){out.innerHTML='<div class="alert danger">'+esc(e.message||"Report submission failed")+'</div>'}
  }
  window.submitReport=submitReport;

  async function officerReportsTab(){
    if(!state.officer)return;
    document.getElementById("officerTitle").textContent="Compliance Reports";
    document.getElementById("officerSub").textContent="Consumer-reported issues routed into an auditable enforcement queue.";
    const out=document.getElementById("officerOut");out.innerHTML='<div class="empty">Loading compliance reports…</div>';
    try{
      const r=await fetch(API+"/officer/reports"),j=await r.json();if(!r.ok)throw Error(j.error||"Unable to load reports");
      state.reports=j.reports||[];
      out.innerHTML='<div class="grid g4">'+[["Open",state.reports.filter(x=>x.status==="OPEN").length],["In review",state.reports.filter(x=>x.status==="IN_REVIEW").length],["Critical",state.reports.filter(x=>x.priority==="CRITICAL"&&x.status!=="RESOLVED").length],["Resolved",state.reports.filter(x=>x.status==="RESOLVED").length]].map(x=>'<div class="card kpi"><div class="small muted">'+x[0]+'</div><div class="n">'+esc(x[1])+'</div></div>').join("")+'</div><div class="card detail-section" style="margin-top:18px"><div class="table-wrap"><table class="table"><thead><tr><th>Issue</th><th>Instrument</th><th>Priority</th><th>Location</th><th>Status</th><th></th></tr></thead><tbody>'+((state.reports.map(x=>'<tr><td><b>'+esc(x.issue_type)+'</b><div class="small muted">'+esc(x.description)+'</div></td><td class="mono">'+esc(x.instrument_id||"Unmatched")+'<div class="small">'+esc(x.serial_number||"")+'</div></td><td><span class="status '+(x.priority==="CRITICAL"?"danger":x.priority==="HIGH"?"warn":"notice")+'">'+esc(x.priority)+'</span></td><td class="small mono">'+esc(x.latitude??"—")+', '+esc(x.longitude??"—")+'</td><td>'+esc(x.status)+'</td><td><button class="btn secondary" onclick="updateReport(\''+esc(x.id)+'\',\''+esc(x.status)+'\')">Review</button></td></tr>').join(""))||'<tr><td colspan="6" class="empty">No compliance reports.</td></tr>')+'</tbody></table></div></div>';
    }catch(e){out.innerHTML='<div class="alert danger">'+esc(e.message||"Unable to load reports")+'</div>'}
  }
  window.officerReportsTab=officerReportsTab;

  async function updateReport(id,current){
    const status=prompt("Set report status: OPEN, IN_REVIEW, ASSIGNED, RESOLVED, DISMISSED",current||"IN_REVIEW");if(!status)return;
    const assigned_to=prompt("Assigned officer / team (optional):","Enforcement Officer")||"";
    const resolution_notes=prompt("Officer note:","")||"";
    try{
      const r=await fetch(API+"/officer/reports",{method:"PUT",headers:{"content-type":"application/json","X-Vmx-CSRF":state?.officerCsrf||""},body:JSON.stringify({id,status:status.toUpperCase(),assigned_to,resolution_notes})});
      const j=await r.json();if(!r.ok)throw Error(j.error||"Could not update report");
      toast("Compliance report updated","success");officerReportsTab();
    }catch(e){toast(e.message,"danger")}
  }
  window.updateReport=updateReport;

  async function officerEnforcementTab(){
    if(!state.officer)return;
    document.getElementById("officerTitle").textContent="Enforcement Cases";
    document.getElementById("officerSub").textContent="Turn public compliance reports into traceable cases and record actions to closure.";
    const out=document.getElementById("officerOut");out.innerHTML='<div class="empty">Loading enforcement cases…</div>';
    try{
      const r=await fetch(API+"/enforcement"),j=await r.json();if(!r.ok)throw Error(j.error||"Unable to load cases");
      const rr=await fetch(API+"/officer/reports"),rep=await rr.json();if(!rr.ok)rep={reports:[]};
      const qr=await fetch(API+"/quality-actions"),qj=await qr.json();
      state.enforcementCases=j.cases||[];state.reports=rep.reports||[];state.qualityActions=qr.ok?(qj.actions||[]):[];
      const openReports=state.reports.filter(function(x){return x.status!=="RESOLVED"&&x.status!=="DISMISSED"});
      const stats=[["Open",state.enforcementCases.filter(function(x){return x.status==="OPEN"}).length],["Assigned",state.enforcementCases.filter(function(x){return x.status==="ASSIGNED"}).length],["Actioned",state.enforcementCases.filter(function(x){return x.status==="ACTIONED"}).length],["Closed",state.enforcementCases.filter(function(x){return x.status==="CLOSED"}).length]];
      const k=stats.map(function(x){return '<div class="card kpi"><div class="small muted">'+x[0]+'</div><div class="n">'+esc(x[1])+'</div></div>'}).join("");
      const options=openReports.map(function(x){return '<option value="'+esc(x.id)+'">'+esc(x.issue_type)+' · '+esc(x.instrument_id||"Unmatched")+' · '+esc(x.priority)+'</option>'}).join("");
      const rows=state.enforcementCases.map(function(x){return '<tr><td class="mono"><b>'+esc(x.case_number)+'</b></td><td><b>'+esc(x.issue_type||"Case")+'</b><div class="small mono">'+esc(x.public_instrument_id||"Unmatched")+' · '+esc(x.serial_number||"")+'</div></td><td>'+esc(x.severity)+'</td><td><span class="status '+statusClass(x.status)+'">'+esc(x.status)+'</span></td><td>'+esc(x.assigned_to||"Unassigned")+'</td><td><button class="btn secondary" onclick="updateEnforcementCase(\''+esc(x.id)+'\',\''+esc(x.status)+'\')">Update</button></td></tr>'}).join("");
      const qa=(state.qualityActions||[]).map(function(x){return '<tr><td>'+esc(x.title)+'</td><td>'+esc(x.action_type)+'</td><td>'+esc(x.owner||"Unassigned")+'</td><td><span class="status '+statusClass(x.status)+'">'+esc(x.status)+'</span></td><td>'+esc(x.due_date||"—")+'</td></tr>'}).join("");
      out.innerHTML='<div class="grid g4">'+k+'</div><div class="card detail-section" style="margin-top:18px"><h3>Create enforcement case</h3><div class="grid g3" style="margin-top:12px"><select id="caseReport" class="select"><option value="">Select an open compliance report</option>'+options+'</select><input id="caseAssignee" class="input" placeholder="Initial assignee"><button class="btn primary" onclick="createEnforcementCase()">Create case</button></div></div><div class="card detail-section" style="margin-top:18px"><h3>Case register</h3><div class="table-wrap" style="margin-top:12px"><table class="table"><thead><tr><th>Case</th><th>Issue / Instrument</th><th>Severity</th><th>Status</th><th>Assignee</th><th></th></tr></thead><tbody>'+ (rows||'<tr><td colspan="6" class="empty">No enforcement cases yet.</td></tr>') +'</tbody></table></div></div><div class="card detail-section" style="margin-top:18px"><div class="detail-head"><div><h3 style="margin:0">Corrective actions (CAPA)</h3><div class="small muted">Track containment, corrective work and effectiveness instead of stopping at case creation.</div></div><span class="pill">QUALITY LOOP</span></div><div class="table-wrap" style="margin-top:12px"><table class="table"><thead><tr><th>Action</th><th>Type</th><th>Owner</th><th>Status</th><th>Due</th></tr></thead><tbody>'+ (qa||'<tr><td colspan="5" class="empty">No corrective actions recorded yet.</td></tr>') +'</tbody></table></div></div>';
    }catch(e){out.innerHTML='<div class="alert danger">'+esc(e.message||"Unable to load enforcement cases")+'</div>'}
  }
  window.officerEnforcementTab=officerEnforcementTab;
  async function createEnforcementCase(){
    const report_id=document.getElementById("caseReport")&&document.getElementById("caseReport").value;
    if(!report_id){toast("Select a compliance report","danger");return}
    const assigned_to=(document.getElementById("caseAssignee")&&document.getElementById("caseAssignee").value.trim())||"";
    try{const r=await fetch(API+"/enforcement",{method:"POST",headers:{"content-type":"application/json","X-Vmx-CSRF":state?.officerCsrf||""},body:JSON.stringify({report_id,assigned_to})});const j=await r.json();if(!r.ok)throw Error(j.error||"Could not create case");toast("Enforcement case "+j.case.case_number+" created","success");officerEnforcementTab()}catch(e){toast(e.message,"danger")}
  }
  window.createEnforcementCase=createEnforcementCase;
  async function updateEnforcementCase(id,current){
    const status=prompt("Set case status: OPEN, IN_REVIEW, ASSIGNED, INSPECTED, ACTIONED, RESOLVED, CLOSED",current||"IN_REVIEW");if(!status)return;
    const assigned_to=prompt("Assigned officer / team:","Enforcement Officer")||"";
    const action_taken=prompt("Action taken (optional):","")||"";
    const resolution_notes=prompt("Resolution notes (optional):","")||"";
    try{const r=await fetch(API+"/enforcement",{method:"PUT",headers:{"content-type":"application/json","X-Vmx-CSRF":state?.officerCsrf||""},body:JSON.stringify({id,status:status.toUpperCase(),assigned_to,action_taken,resolution_notes})});const j=await r.json();if(!r.ok)throw Error(j.error||"Could not update case");toast("Enforcement case updated","success");officerEnforcementTab()}catch(e){toast(e.message,"danger")}
  }
  window.updateEnforcementCase=updateEnforcementCase;

  async function officerNationalCommandTab(){
    if(!state.officer)return;
    document.getElementById("officerTitle").textContent="National Command Center";
    document.getElementById("officerSub").textContent="Live operational picture across applications, certificates, risk, expiry, compliance reports and GATC capacity.";
    const out=document.getElementById("officerOut");out.innerHTML='<div class="empty">Loading national command center…</div>';
    try{
      const r=await fetch(API+"/national-command"),j=await r.json();if(!r.ok)throw Error(j.error||"Unable to load national command center");
      state.nationalCommand=j;
      const o=j.overview||{},e=j.expiry||{};
      const cards=[["Applications",o.total||0],["Certified",o.certified||0],["Open",o.open||0],["High / Critical Risk",o.high_risk||0],["Expiry 90d",e.d90||0],["Expiry 30d",e.d30||0],["Expiry 7d",e.d7||0],["Expired",e.expired||0]];
      const table=function(rows,keys,heads){return '<div class="table-wrap"><table class="table"><thead><tr>'+heads.map(function(h){return '<th>'+h+'</th>'}).join("")+'</tr></thead><tbody>'+((rows||[]).map(function(x){return '<tr>'+keys.map(function(k){return '<td>'+esc(x[k]??"—")+'</td>'}).join("")+'</tr>'}).join("")||'<tr><td colspan="'+heads.length+'" class="empty">No data.</td></tr>')+'</tbody></table></div>'};
      const gatcRows=(j.gatc||[]).map(function(x){return '<tr><td><b>'+esc(x.centre_code)+'</b><div class="small">'+esc(x.name)+'</div></td><td>'+esc(x.district)+', '+esc(x.state)+'</td><td>'+esc(x.current_load)+' / '+esc(x.daily_capacity)+'</td><td>'+esc(x.status)+'</td></tr>'}).join("");
      const trust=j.trust||{};
      const trustCards=[["Certificate signing",trust.certificate_signing_ready?"READY":"NOT CONFIGURED"],["Email provider",trust.email_ready?"READY":"NOT CONFIGURED"],["SMS provider",trust.sms_ready?"READY":"NOT CONFIGURED"],["Data source","DEMO / LIVE REGISTRY"]];
      out.innerHTML='<div class="alert notice"><b>Prototype national operations view.</b><div class="small" style="margin-top:4px">Aggregates come from the VeriMetrix registry. They are demo/system data, not official government statistics.</div></div><div class="grid g4">'+trustCards.map(function(x){return '<div class="card kpi"><div class="small muted">'+x[0]+'</div><div class="n" style="font-size:18px">'+esc(x[1])+'</div></div>'}).join("")+'</div><div class="grid g4" style="margin-top:18px">'+cards.map(function(x){return '<div class="card kpi"><div class="small muted">'+x[0]+'</div><div class="n">'+esc(x[1])+'</div></div>'}).join("")+'</div><div class="grid g2" style="margin-top:18px"><div class="card detail-section"><h3>State coverage</h3><div style="margin-top:12px">'+table(j.states,["state","applications","certified"],["State","Applications","Certified"])+'</div></div><div class="card detail-section"><h3>Instrument categories</h3><div style="margin-top:12px">'+table(j.categories,["instrument_category","applications","certified"],["Category","Applications","Certified"])+'</div></div><div class="card detail-section"><h3>Compliance reports</h3><div style="margin-top:12px">'+table(j.reports,["priority","count"],["Priority","Open count"])+'</div></div><div class="card detail-section"><h3>GATC capacity</h3><div style="margin-top:12px"><div class="table-wrap"><table class="table"><thead><tr><th>Centre</th><th>Location</th><th>Load</th><th>Status</th></tr></thead><tbody>'+(gatcRows||'<tr><td colspan="4" class="empty">No GATC centres configured.</td></tr>')+'</tbody></table></div></div></div></div>';
    }catch(e){out.innerHTML='<div class="alert danger">'+esc(e.message||"Unable to load national command center")+'</div>'}
  }
  window.officerNationalCommandTab=officerNationalCommandTab;

  async function officerNationalAnalyticsTab(){
    if(!state.officer)return;
    document.getElementById("officerTitle").textContent="National Analytics";
    document.getElementById("officerSub").textContent="Jurisdiction, category, inspection turnaround, GATC workload, expiry and enforcement visibility.";
    const out=document.getElementById("officerOut");out.innerHTML='<div class="empty">Loading national analytics…</div>';
    try{
      const r=await fetch(API+"/officer/analytics"),j=await r.json();if(!r.ok)throw Error(j.error||"Unable to load analytics");
      state.nationalAnalytics=j;
      const makeTable=function(rows,label){return '<div class="table-wrap"><table class="table"><thead><tr><th>'+label+'</th><th>Count</th></tr></thead><tbody>'+((rows||[]).map(function(x){return '<tr><td>'+esc(x.state||x.category||x.status)+'</td><td>'+esc(x.count)+'</td></tr>'}).join("")||'<tr><td colspan="2" class="empty">No data.</td></tr>')+'</tbody></table></div>'};
      out.innerHTML='<div class="grid g4">'+[["Avg inspection hours",j.inspection_sla&&j.inspection_sla.avg_hours||0],["Completed inspections",j.inspection_sla&&j.inspection_sla.completed||0],["Expiring in 90 days",j.expiry&&j.expiry.d90||0],["Expired",j.expiry&&j.expiry.expired||0]].map(function(x){return '<div class="card kpi"><div class="small muted">'+x[0]+'</div><div class="n">'+esc(x[1])+'</div></div>'}).join("")+'</div><div class="grid g2" style="margin-top:18px"><div class="card detail-section"><h3>Applications by state</h3><div style="margin-top:12px">'+makeTable(j.by_state,"State")+'</div></div><div class="card detail-section"><h3>Applications by category</h3><div style="margin-top:12px">'+makeTable(j.by_category,"Category")+'</div></div><div class="card detail-section"><h3>Application status</h3><div style="margin-top:12px">'+makeTable(j.by_status,"Status")+'</div></div><div class="card detail-section"><h3>Enforcement pipeline</h3><div style="margin-top:12px">'+makeTable(j.cases,"Status")+'</div></div></div><div class="card detail-section" style="margin-top:18px"><h3>GATC workload</h3><div class="table-wrap" style="margin-top:12px"><table class="table"><thead><tr><th>Centre</th><th>Location</th><th>Assignments</th><th>Status</th></tr></thead><tbody>'+((j.gatc||[]).map(function(x){return '<tr><td><b>'+esc(x.centre_code)+'</b><div class="small">'+esc(x.name)+'</div></td><td>'+esc(x.district)+', '+esc(x.state)+'</td><td>'+esc(x.assignments)+'</td><td>'+esc(x.status)+'</td></tr>'}).join("")||'<tr><td colspan="4" class="empty">No GATC centres.</td></tr>')+'</tbody></table></div></div><div class="form-actions"><span class="small muted">Export contains the same current aggregates shown above.</span><button class="btn secondary" onclick="downloadNationalAnalyticsCsv()">Export CSV</button></div>';
    }catch(e){out.innerHTML='<div class="alert danger">'+esc(e.message||"Unable to load analytics")+'</div>'}
  }
  window.officerNationalAnalyticsTab=officerNationalAnalyticsTab;
  function downloadNationalAnalyticsCsv(){
    const j=state.nationalAnalytics;if(!j)return;
    const groups=[["State",j.by_state||[],"state"],["Category",j.by_category||[],"category"],["Status",j.by_status||[],"status"],["Enforcement",j.cases||[],"status"]];
    let lines=["Section,Label,Count"];
    groups.forEach(function(g){(g[1]||[]).forEach(function(x){lines.push([g[0],x[g[2]],x.count].map(function(v){return '"'+String(v==null?"":v).replace(/"/g,'""')+'"'}).join(","))})});
    const blob=new Blob([lines.join("\n")],{type:"text/csv"}),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="verimetrix-national-analytics.csv";document.body.appendChild(a);a.click();a.remove();setTimeout(function(){URL.revokeObjectURL(url)},1000);
  }
  window.downloadNationalAnalyticsCsv=downloadNationalAnalyticsCsv;
})();