(function(){
"use strict";
window.officerAdminPage=function(){document.body.classList.remove("public-mode");
return '<section style="min-height:100vh;background:#f6f8fb;padding:42px 20px"><div style="width:min(980px,100%);margin:0 auto"><div style="display:flex;justify-content:space-between;gap:12px;align-items:center"><div><span class="pill">ADMINISTRATION</span><h1 style="margin:10px 0 5px">Officer Account Management</h1><p class="muted" style="margin:0">Only the project administrator can provision, rotate or disable Legal Metrology Officer accounts.</p></div><button class="btn outline" onclick="go(\'home\')">Public portal</button></div><div id="officerAdminAuth" class="card" style="padding:26px;margin-top:24px"><div class="empty">Checking administrator access…</div></div></div></section>';
};
window.loadOfficerAdmin=async function(){
const box=document.getElementById("officerAdminAuth");if(!box)return;
try{
 const r=await fetch(API+"/officer/provision");
 const j=await r.json();
 if(r.status===401){box.innerHTML='<div class="alert notice"><b>Administrator sign-in required.</b><div class="small" style="margin-top:6px">This control plane is separate from the officer login. Only the VeriMetrix project administrator can manage employee credentials.</div><a class="btn primary" style="display:inline-block;margin-top:14px;text-decoration:none" href="/login?next=%2Fofficer-admin">Open administrator sign-in →</a></div>';return;}
 if(r.status===403){box.innerHTML='<div class="alert danger"><b>Administrator access denied.</b><div class="small" style="margin-top:6px">Your signed-in account is not the project administrator.</div></div>';return;}
 if(!r.ok)throw Error(j.error||"Unable to load officer accounts");
 const officers=j.officers||[];
 const rows=officers.map(function(o){
   return '<tr><td><b>'+esc(o.full_name)+'</b><div class="small muted">'+esc(o.email)+'</div></td><td>'+esc(o.office)+'</td><td>'+esc(o.state)+' · '+esc(o.district)+'</td><td><span class="status '+(o.active?"success":"danger")+'">'+(o.active?"ACTIVE":"DISABLED")+'</span></td><td class="small">'+(o.last_login_at?dateFmt(o.last_login_at):"Never")+'</td><td><button class="btn outline" onclick="toggleOfficerAccount(\''+esc(o.email)+'\','+(o.active?"false":"true")+')">'+(o.active?"Disable":"Enable")+'</button></td></tr>';
 }).join("")||'<tr><td colspan="6" class="empty">No officer accounts have been provisioned.</td></tr>';
 box.innerHTML='<div class="alert notice"><b>Credential security</b><div class="small" style="margin-top:5px">Passwords are stored only as salted PBKDF2 hashes. The administrator cannot retrieve an existing password. Rotation creates a new password hash and invalidates the old officer session.</div></div><form id="officerProvisionForm" style="margin-top:18px"><h3 style="margin:0">Provision / rotate officer</h3><div class="grid g2" style="margin-top:16px"><label><span class="label">Officer email</span><input id="paEmail" class="input" type="email" required placeholder="employee@legalmetrology.example"></label><label><span class="label">Full name</span><input id="paName" class="input" required placeholder="Officer full name"></label><label><span class="label">Office</span><input id="paOffice" class="input" required placeholder="Legal Metrology Office — Pune"></label><label><span class="label">State</span><input id="paState" class="input" required placeholder="Maharashtra"></label><label><span class="label">District</span><input id="paDistrict" class="input" required placeholder="Pune"></label><label><span class="label">Issued password</span><input id="paPassword" class="input" type="password" minlength="12" autocomplete="new-password" required placeholder="12+ chars, upper/lower/number/symbol"></label></div><div class="alert warn" style="margin-top:14px"><b>Credential handling:</b> Share the password with the officer through an approved secure channel. Never place it in email, screenshots, source code or the dashboard.</div><div id="paMsg" class="small" style="min-height:20px;margin-top:14px"></div><div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px"><button id="paBtn" class="btn primary">Provision / rotate account</button><button type="button" class="btn outline" onclick="loadOfficerAdmin()">Refresh accounts</button></div></form><div class="card detail-section" style="margin-top:22px"><div style="display:flex;justify-content:space-between;align-items:center;gap:12px"><div><h3 style="margin:0">Provisioned officers</h3><p class="small muted" style="margin:5px 0 0">Active officers are limited to their assigned state and district.</p></div><span class="pill">'+officers.length+' accounts</span></div><div class="table-wrap" style="margin-top:14px"><table class="table"><thead><tr><th>Officer</th><th>Office</th><th>Jurisdiction</th><th>Status</th><th>Last login</th><th>Access</th></tr></thead><tbody>'+rows+'</tbody></table></div></div>';
 document.getElementById("officerProvisionForm").addEventListener("submit",async function(e){
   e.preventDefault();
   const btn=document.getElementById("paBtn"),msg=document.getElementById("paMsg");
   btn.disabled=true;msg.textContent="Provisioning securely…";msg.style.color="";
   try{
     const body={email:document.getElementById("paEmail").value.trim(),full_name:document.getElementById("paName").value.trim(),office:document.getElementById("paOffice").value.trim(),state:document.getElementById("paState").value.trim(),district:document.getElementById("paDistrict").value.trim(),password:document.getElementById("paPassword").value};
     const rr=await fetch(API+"/officer/provision",{method:"POST",headers:{"content-type":"application/json","X-Vmx-CSRF":state?.officerCsrf||""},body:JSON.stringify(body)});
     const jj=await rr.json();if(!rr.ok)throw Error(jj.error||"Officer provisioning failed");
     msg.textContent="Officer account "+jj.officer.email+" is ready. The password is stored only as a salted hash.";
     msg.style.color="var(--green)";
     document.getElementById("paPassword").value="";
     setTimeout(loadOfficerAdmin,250);
   }catch(x){msg.textContent=x.message||"Provisioning failed";msg.style.color="var(--red)"}finally{btn.disabled=false}
 });
}catch(e){box.innerHTML='<div class="alert danger">'+esc(e.message||"Unable to load administrator controls")+'</div>'}
};
window.toggleOfficerAccount=async function(email,enable){
 if(!confirm((enable?"Enable ":"Disable ")+email+"?"))return;
 try{
  const r=await fetch(API+"/officer/provision",{method:"POST",headers:{"content-type":"application/json","X-Vmx-CSRF":state?.officerCsrf||""},body:JSON.stringify({action:enable?"ENABLE":"DISABLE",email})});
  const j=await r.json();if(!r.ok)throw Error(j.error||"Account update failed");
  toast("Officer account "+(enable?"enabled":"disabled"),"success");loadOfficerAdmin();
 }catch(e){toast(e.message||"Account update failed","danger")}
};
})();