
export default {
// ---- marketplace endpoint Try-it ----
    openEpTry(e){
      this.epTry=e; this.epTryTab='agent'; this.epTryResp=''; this.epTryStatus=null; this.epTryMs=null; this.epTryCost=null;
      this.epTryAccess=null; this.epTryAccessByMethod={}; this.epTryBusy=false;
      this.epTryAuthMethod=e.authorization_method||((e.authorization_methods||[])[0])||'';
      const secs=this.paramSections(e);
      const tr=e.test_request||{};  // the live-verified request — the best possible prefill
      const trq={...(tr.queryParams||{}), ...(tr.pathParams||{})};
      const qp=[];  // query + path params (path placeholders are consumed from query server-side)
      for(const s of secs) if(s.key!=='body') for(const p of s.rows)
        qp.push({name:p.name, required:!!p.required, authorization_methods:p.authorization_methods||[],
                 value: trq[p.name]!=null ? this.fmtExample(trq[p.name])
                        : (p.example!=null ? this.fmtExample(p.example) : '')});
      // test_request may carry params the input spec doesn't list — keep them, they made the call work
      for(const [k,v] of Object.entries(trq)) if(!qp.some(p=>p.name===k)) qp.push({name:k, required:false, value:this.fmtExample(v)});
      this.epTryParams=qp;
      let body='';
      if((e.method||'GET')!=='GET'){
        if(tr.body!=null){ body=JSON.stringify(tr.body,null,2); }  // verbatim — array-vs-object is ground truth here
        else{
          const bodySec=secs.find(s=>s.key==='body');
          if(bodySec){ const o={}; for(const p of bodySec.rows) if(p.example!=null||p.required) o[p.name]=p.example!=null?p.example:null;
            body=JSON.stringify(o,null,2); }
        }
      }
      this.epTryBody=body;
      // the authenticated dry-run: which ladder rung would serve this org, and at what price
      this.loadEpTryAccessPolicy();
    },
epTryParamAllowed(p){ return !p.authorization_methods || !p.authorization_methods.length
      || p.authorization_methods.includes(this.epTryAuthMethod); },
async loadEpTryAccessPolicy(){
      const e=this.epTry; if(!e) return;
      const methods=this.epTryAuthMethods;
      if(!methods.length){ this.loadEpTryAccess(); return; }
      const rows=await Promise.all(methods.map(async m=>{
        try{ return [m, await this.api('/catalog/endpoints/'+e.id+'/access?authorization_method='+encodeURIComponent(m))]; }
        catch(_){ return [m, null]; }
      }));
      if(this.epTry!==e) return;
      this.epTryAccessByMethod=Object.fromEntries(rows);
      const connected=this.epTryConnectedMethods;
      if(connected.length===1) this.epTryAuthMethod=connected[0];
      else this.epTryAuthMethod=e.authorization_method||methods[0]||'';
      this.epTryAccess=this.epTryAccessByMethod[this.epTryAuthMethod]||null;
    },
loadEpTryAccess(){
      if(!this.epTry) return;
      if(this.epTryAccessByMethod[this.epTryAuthMethod]){
        this.epTryAccess=this.epTryAccessByMethod[this.epTryAuthMethod]; return;
      }
      const q=this.epTryAuthMethod?'?authorization_method='+encodeURIComponent(this.epTryAuthMethod):'';
      this.epTryAccess=null;
      this.api('/catalog/endpoints/'+this.epTry.id+'/access'+q).then(a=>{
        this.epTryAccess=a;
        if(this.epTryAuthMethod) this.epTryAccessByMethod={...this.epTryAccessByMethod,[this.epTryAuthMethod]:a};
      }).catch(()=>{});
    },
async runEpTry(){
      const e=this.epTry; if(!e) return; this.epTryBusy=true; const t0=performance.now();
      try{
        const qs=this.epTryVisibleParams.filter(p=>String(p.value)!=='').map(p=>encodeURIComponent(p.name)+'='+encodeURIComponent(p.value)).join('&');
        const opts={method:e.method||'GET', credentials:'include', headers:{...this.headers()}};
        if(this.epTryAuthMethod) opts.headers['X-Treg-Authorization-Method']=this.epTryAuthMethod;
        if(opts.method!=='GET' && this.epTryBody.trim()){ opts.body=this.epTryBody; opts.headers['content-type']='application/json'; }
        const r=await fetch('/call/'+e.id+(qs?'?'+qs:''), opts);
        this.epTryStatus=r.status; this.epTryMs=Math.round(performance.now()-t0);
        const txt=await r.text();
        if(!txt){ this.epTryResp='(empty response body - status '+r.status+')'; }
        else{ try{ this.epTryResp=JSON.stringify(JSON.parse(txt),null,2).slice(0,20000); }catch(_){ this.epTryResp=txt.slice(0,8000); } }
        // The charge, read back from the same telemetry Activity renders. The audit writer is
        // fire-and-forget, so give it a beat; also refresh the balance pill.
        setTimeout(async()=>{ try{
          const rows=await this.api('/calls?limit=5');
          const row=(rows||[]).find(c=>c.endpoint_id===e.id);
          if(row && row.cost_charged_micro!=null) this.epTryCost=row.cost_charged_micro;
          this.loadBilling();
        }catch(_){}} , 900);
      }catch(err){ this.epTryResp='Request failed: '+err; this.epTryStatus=0; this.epTryMs=Math.round(performance.now()-t0); }
      finally{ this.epTryBusy=false; }
    },
async runTry(){ this.trying=true; const t0=performance.now();
      try{ const opts={method:this.tryMethod, credentials:'include', headers:{...this.headers()}};
        if(this.tryMethod!=='GET' && (this.tryBody||'').trim()){ opts.body=this.tryBody; opts.headers['content-type']='application/json'; }  // send the body for POST/PUT/DELETE
        const r=await fetch(`/call/${this.tryTool.name}/${this.tryPath}`,opts);
        this.tryStatus=r.status; this.tryMs=Math.round(performance.now()-t0); const txt=await r.text();
        if(!txt){ this.tryResp='(empty response body - status '+r.status+')'; }  // e.g. a 404 with no body left the box blank
        else { try{ this.tryResp=JSON.stringify(JSON.parse(txt),null,2); }catch(_){ this.tryResp=txt.slice(0,4000); } }
      }catch(e){ this.tryResp='Request failed: '+e; this.tryStatus=0; this.tryMs=Math.round(performance.now()-t0); } finally{ this.trying=false; } }
}
