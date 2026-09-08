/* Standalone Arena: anonymous browsing, authenticated spending, one-click voting with visible vendors. */
(() => {
  'use strict';
  if (!window.Vue) return;
  const DRAFT = 'treg.arena.draft.v1';
  const ACTIVE = 'treg.arena.active.v1';
  const NAMES = {'fiber-ai':'Fiber','pdl':'People Data Labs','branddev':'Brand.dev',
    'thecompaniesapi':'TheCompaniesAPI','companyenrich':'CompanyEnrich','leadmagic':'LeadMagic',
    'findymail':'Findymail','leadsforge':'Leadsforge','icypeas':'Icypeas','predictleads':'PredictLeads',
    'apollo':'Apollo','aviato':'Aviato','hunter':'Hunter','tomba':'Tomba','lusha':'Lusha'};
  const LABELS = {full_name:'Full name',domain:'Company domain',linkedin_url:'LinkedIn URL',name:'Company name',
    email:'Email',first_name:'First name',last_name:'Last name',company_domain:'Company domain',
    line_type:'Line type',country_code:'Country code',valid:'Valid mailbox',verified:'Verified',
    employees:'Employees',founded:'Founded',website:'Website',description:'Description',industry:'Industry',
    company:'Company',title:'Job title',location:'Location',phone:'Phone',confidence:'Confidence',status:'Verdict',score:'Score'};
  const read = key => {try{return JSON.parse(sessionStorage.getItem(key)||'null');}catch{return null;}};
  const write = (key,value) => {try{sessionStorage.setItem(key,JSON.stringify(value));}catch{}};
  const remove = key => {try{sessionStorage.removeItem(key);}catch{}};

  const ArenaFighters = {
    props:{entries:{type:Array,default:()=>[]},mode:String,active:Boolean,selectable:Boolean,disabled:Boolean,selected:{type:Array,default:()=>[]},choosable:Boolean,enabledProviders:{type:Array,default:()=>[]},awards:{type:Object,default:()=>({})}},
    template:'#arena-fighters-template',
    emits:['change-mode','toggle-provider'],
    methods:{
      chooseMode(mode){if(this.selectable&&!this.disabled&&!this.active&&mode!==this.mode)this.$emit('change-mode',mode);},
      enabled(entry){return !this.choosable||this.enabledProviders.includes(entry.provider);},
      chooseProvider(entry){if(this.choosable&&!this.disabled&&!this.active)this.$emit('toggle-provider',entry.provider);},
      name(provider){return NAMES[provider]||provider;},
      rejected(entry){return (entry.rating?.value || (entry.report?'down':''))==='down';},
      state(entry){
        if(this.choosable&&!this.active&&!this.enabled(entry))return 'excluded';
        if(this.rejected(entry))return 'defeated';
        if(this.selected.includes(entry.id))return 'champion';
        if(entry.state==='running')return this.active?'fighting':'paused';
        return ({hit:'won',miss:'defeated',error:'error',timeout:'error',interrupted:'paused',cancelled:'paused',skipped:'benched',not_attempted:'benched',queued:'waiting'})[entry.state]||'ready';
      },
      label(entry){return ({excluded:'Excluded',champion:'Winner',won:'Hit!',defeated:this.rejected(entry)?'Thumbs down':'No match',error:entry.state==='timeout'?'Timed out':'Error',paused:'Stopped',benched:'Not called',waiting:'Waiting',fighting:'Fighting…',ready:'Ready'})[this.state(entry)];}
    }
  };

  Vue.createApp({
    components:{ArenaFighters},
    data:()=>({tasks:[],taskId:'people.email.find',variant:0,inputs:{},mode:'waterfall',
      user:null,teams:[],team:'',balance:null,meta:{},busy:false,error:'',run:null,quote:null,history:[],customServices:false,services:[],
      manualQuotes:{},reporting:'',reportDrafts:{},pricing:false,quoteTimer:null,quoteSequence:0,pricedKey:'',expandedResults:[],email:'',code:'',emailStep:'email',devCode:'',authBusy:false,authError:'',
      newTeamName:'',pendingSubmit:false,pollTimer:null,pollFailures:0,runTeam:'',booted:false,historySequence:0}),
    watch:{
      quoteKey(){this.scheduleQuote();},
      running(value){if(!value)this.scheduleQuote();},
      user(){this.scheduleQuote();}
    },
    computed:{
      quoteKey(){return JSON.stringify({team:this.team,capability:this.taskId,identity:this.identity(),mode:this.mode,providers:this.customServices?this.services:null});},
      readyQuote(){return this.quote&&this.pricedKey===this.quoteKey?this.quote:null;},
      runButtonLabel(){if(this.running)return 'Running…';if(this.busy)return 'Starting…';if(this.pricing)return 'Pricing…';const q=this.readyQuote;if(q)return this.mode==='waterfall'?(q.affordable?'Run from ':'Top up · from ')+this.usd(q.required_micro):(q.affordable?'Run':'Top up')+' · ~'+this.usd(q.required_micro);return this.mode==='waterfall'?'Run waterfall':'Battle';},
      currentTask(){return this.tasks.find(t=>t.id===this.taskId)||{description:'Choose a task to get started.',variants:[],providers:[],fields:[]};},
      inputKeys(){return this.currentTask.variants[this.variant]||[];},
      availableProviders(){
        const catalog=this.currentTask.provider_previews?.[this.variant]||[],quoted=this.readyQuote?.providers||[];
        return [...quoted,...catalog.filter(p=>!quoted.some(q=>q.provider===p.provider))];
      },
      enabledProviders(){return this.customServices?this.services:this.availableProviders.map(p=>p.provider);},
      resultFighters(){return this.showProviderPreview||this.running?this.run.results:[...this.run.results,...this.availableProviders.filter(p=>!this.run.results.some(r=>r.provider===p.provider))];},
      previewProviders(){const rows=this.readyQuote?.providers||this.currentTask.provider_previews?.[this.variant]||[];return this.customServices?rows.filter(p=>this.services.includes(p.provider)):rows;},
      showProviderPreview(){if(!this.run)return true;if(this.running)return false;return this.run.capability!==this.taskId||this.run.mode!==this.mode||JSON.stringify(Object.entries(this.identity()).sort())!==JSON.stringify(Object.entries(this.run.identity).sort())||this.customServices&&this.services.slice().sort().join()!==this.run.results.map(r=>r.provider).sort().join();},
      running(){return this.run?.state==='running';},
      upvotedResults(){return (this.run?.results||[]).filter(r=>this.ratingValue(r)==='up');},
      winnerIds(){if(this.run?.state!=='completed')return [];const upvoted=this.upvotedResults;return upvoted.length===1?[upvoted[0].id]:upvoted.length>1?Object.keys(this.battleAwards):[];},
      battleAwards(){
        const awards={};
        if(!this.run||this.run.state!=='completed')return awards;
        const upvoted=this.upvotedResults;
        if(!upvoted.length&&this.run.mode!=='compare')return awards;
        const results=upvoted.length>1?upvoted:this.run.results.filter(r=>r.state==='hit'&&this.ratingValue(r)!=='down');
        if(results.length<2)return awards;
        for(const [field,label] of [['duration_ms','Fastest'],['charged_micro','Cheapest']]){
          // Unknown charges/timings cannot establish a winner; genuine zeros can.
          if(!results.every(r=>Number.isFinite(r[field])&&r[field]>=0))continue;
          const best=Math.min(...results.map(r=>r[field]));
          for(const r of results)if(r[field]===best)(awards[r.id] ||= []).push(label);
        }
        return awards;
      },
      primaryFields(){const preferred={'people.email.find':['email','verified'],'people.enrich':['name','full_name','title','company'],'companies.enrich':['name','domain','industry'],'people.phone.find':['phone','line_type'],'people.email.verify':['valid','status'],'people.identity.resolve':['linkedin_url','full_name']};const fields=this.run?.fields||[];const core=fields.filter(k=>(preferred[this.run?.capability]||[]).includes(k));return core.length?core:fields.slice(0,3);},
      timelineEnd(){return Math.max(1,...(this.run?.results||[]).map(r=>(r.started_ms||0)+(r.duration_ms||0)));}
    },
    methods:{
      awardTitle(badge){if(badge==='Winner')return 'Your thumbs-up winner';const cohort=this.upvotedResults.length>1?'thumbs-up results':'successful, non-downvoted results';return (badge==='Fastest'?'Lowest response time':'Lowest actual charge')+' among '+cohort;},
      usd(n){return n==null?'—':new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:2,maximumFractionDigits:6}).format(n/1e6);},
      billingLabel(p){if(p.tier&&p.tier!=='platform'&&p.tier!=='catalog')return 'Your key · no treg charge';return ({per_success:'Per successful request',per_result:'Per result',per_call:'Per request'})[p.price_type]||'Per lookup';},
      seconds(ms){return ms<1000?ms+' ms':(ms/1000).toFixed(1)+' s';},
      providerName(p){return NAMES[p]||p;},fieldLabel(k){return LABELS[k]||k.replaceAll('_',' ');},
      variantLabel(v){return v.map(k=>({full_name:'Name',domain:'company domain',linkedin_url:'LinkedIn URL',email:'Email',name:'Company name'})[k]||k).join(' + ');},
      taskLabel(id){return this.tasks.find(t=>t.id===id)?.label||id;},
      placeholder(k){return ({full_name:'First and last name',domain:'company.com',linkedin_url:this.taskId==='companies.enrich'?'https://linkedin.com/company/…':'https://linkedin.com/in/…',email:'name@company.com',name:'Company name'})[k]||k;},
      domainMismatch(r){if(this.run?.capability!=='people.email.find'||!this.run.identity.domain||typeof r.output.email!=='string')return '';const actual=r.output.email.split('@')[1]?.toLowerCase();const expected=this.run.identity.domain.toLowerCase();return actual&&actual!==expected&&!actual.endsWith('.'+expected)?'Email domain is '+actual+'; you searched '+expected+'.':'';},
      summaryFields(r){if(r.state!=='hit')return [];return this.primaryFields.filter(k=>r.output[k]!==null&&r.output[k]!==undefined&&r.output[k]!=='');},
      toggleResultRow(event,id){if(event.target.closest('button,a,input,select,textarea,summary'))return;this.toggleDetails(id);},
      toggleDetails(id){this.expandedResults=this.expandedResults.includes(id)?this.expandedResults.filter(x=>x!==id):[...this.expandedResults,id];},
      displayValue(v){return v===null||v===undefined||v===''?'Not returned':v===true?'Yes':v===false?'No':String(v);},
      outcomeLabel(r){if(r.state==='hit')return this.run.capability==='people.email.verify'?'Verdict returned':'Found';return ({miss:'No match',error:'Error',timeout:'Timed out',not_attempted:'Not attempted',skipped:'Skipped',running:'Running',queued:'Queued',interrupted:'Interrupted',cancelled:'Cancelled'})[r.state]||r.state;},
      emptyLabel(r){return ({miss:'No matching data returned.',error:'This service could not complete the lookup.',timeout:'This service did not finish within the deadline.',not_attempted:'This service was not called.',skipped:'This step was skipped.',queued:'Waiting for its turn.',running:'Looking for an answer…',cancelled:'The attempt was stopped.',interrupted:'No complete result was recorded.'})[r.state]||'No fields returned.';},
      timeLeft(r){return (r.started_ms||0)/this.timelineEnd*100;},timeWidth(r){return Math.max(.5,(r.duration_ms||0)/this.timelineEnd*100);},
      async api(path,options={},teamOverride){
        const headers={'Content-Type':'application/json',...(options.headers||{})};
        const active=teamOverride===undefined?this.team:teamOverride;
        if(active)headers['X-Treg-Org']=active;
        const response=await fetch(path,{credentials:'same-origin',...options,headers});
        let body;try{body=await response.json();}catch{body={detail:'The server returned an unreadable response.'};}
        if(!response.ok){const d=body.detail;const e=new Error(typeof d==='string'?d:d?.message||body.message||'The request could not be completed.');e.status=response.status;throw e;}
        return body;
      },
      identity(){return Object.fromEntries(this.inputKeys.map(k=>[k,(this.inputs[k]||'').trim()]));},
      saveDraft(pending=false){write(DRAFT,{taskId:this.taskId,variant:this.variant,inputs:this.inputs,mode:this.mode,customServices:this.customServices,services:this.services,pending,at:Date.now()});},
      restoreDraft(){const d=read(DRAFT);if(!d||Date.now()-d.at>600000){remove(DRAFT);return false;}if(!this.tasks.some(t=>t.id===d.taskId))return false;this.taskId=d.taskId;this.variant=d.variant;this.inputs=d.inputs||{};this.mode=d.mode==='compare'?'compare':'waterfall';this.customServices=!!d.customServices;this.services=d.services||[];return !!d.pending;},
      chooseTask(id){this.taskId=id;this.variant=0;this.inputs={};this.quote=null;this.customServices=false;this.services=[];},
      taskKeydown(event,index){const count=this.tasks.length;let next;if(event.key==='ArrowRight')next=(index+1)%count;else if(event.key==='ArrowLeft')next=(index+count-1)%count;else if(event.key==='Home')next=0;else if(event.key==='End')next=count-1;else return;event.preventDefault();this.chooseTask(this.tasks[next].id);event.currentTarget.parentElement.querySelectorAll('[role="tab"]')[next].focus();},
      chooseVariant(i){this.variant=i;this.inputs={};this.quote=null;this.customServices=false;this.services=[];},setMode(m){if(this.busy||this.running||this.mode===m)return;this.mode=m;this.quote=null;},
      toggleProvider(provider){
        if(this.busy||this.running||!this.availableProviders.some(p=>p.provider===provider))return;
        const selected=this.enabledProviders;
        this.services=selected.includes(provider)?selected.filter(p=>p!==provider):[...selected,provider];
        this.customServices=true;this.quote=null;this.error='';this.saveDraft(false);
      },
      async loadIdentity(){
        try{this.user=await this.api('/auth/me',{},'');}catch(e){if(e.status!==401)throw e;this.user=null;this.teams=[];this.team='';this.balance=null;this.history=[];this.historySequence++;return;}
        const orgs=await this.api('/orgs');this.teams=orgs.filter(t=>!t.demo);
        let saved='';try{saved=localStorage.getItem('treg.arena.team')||'';}catch{}
        this.team=this.teams.find(t=>t.slug===this.team)?.slug||this.teams.find(t=>t.slug===saved)?.slug||this.teams[0]?.slug||'';
        if(this.team){await this.loadBalance();await this.refreshHistory();}
      },
      async loadBalance(){const t=this.teams.find(t=>t.slug===this.team);if(!t)return;const b=await this.api('/orgs/'+t.org_id+'/balance?limit=1');this.balance=b.balance_micro;},
      async changeTeam(){this.quote=null;this.run=null;this.history=[];this.historySequence++;clearTimeout(this.pollTimer);remove(ACTIVE);try{localStorage.setItem('treg.arena.team',this.team);await this.loadBalance();await this.refreshHistory();}catch(e){this.error=e.message;}},
      async openLogin(pending){this.pendingSubmit=pending;this.authError='';this.emailStep='email';this.code='';this.devCode='';this.saveDraft(pending);await this.$nextTick();this.$refs.loginDialog.showModal();},
      closeLogin(){this.pendingSubmit=false;this.saveDraft(false);this.$refs.loginDialog.close();},
      socialLogin(provider){this.saveDraft(this.pendingSubmit);location.assign('/auth/'+provider+'?return_to=%2Fenrich-arena');},
      async sendCode(){this.authBusy=true;this.authError='';try{const r=await this.api('/auth/email/start',{method:'POST',body:JSON.stringify({email:this.email})},'');this.emailStep='code';this.devCode=r.dev_code||'';}catch(e){this.authError=e.message;}finally{this.authBusy=false;}},
      async verifyEmail(){this.authBusy=true;this.authError='';try{await this.api('/auth/email/verify',{method:'POST',body:JSON.stringify({email:this.email,code:this.code})},'');this.$refs.loginDialog.close();await this.loadIdentity();const resume=this.pendingSubmit;this.pendingSubmit=false;this.saveDraft(false);if(resume){if(this.team)await this.prepare();else this.$refs.teamDialog.showModal();}}catch(e){this.authError=e.message;this.error=e.message;}finally{this.authBusy=false;}},
      async createTeam(){this.authBusy=true;this.authError='';try{await this.api('/orgs',{method:'POST',body:JSON.stringify({name:this.newTeamName})},'');this.$refs.teamDialog.close();await this.loadIdentity();await this.prepare();}catch(e){this.authError=e.message;}finally{this.authBusy=false;}},
      inputError(){
        if(!this.inputKeys.length||this.inputKeys.some(k=>!(this.inputs[k]||'').trim()))return 'Complete the query fields.';
        if(this.inputKeys.includes('full_name')){const parts=this.inputs.full_name.trim().split(/\s+/);if(parts.length<2||![parts[0],parts.at(-1)].every(p=>/\p{L}/u.test(p)))return 'Enter both a first and last name for a name-based comparison, or use a LinkedIn URL.';}
        if(this.customServices&&!this.services.length)return 'Enable at least one vendor by clicking its avatar.';
        return '';
      },
      scheduleQuote(){
        clearTimeout(this.quoteTimer);this.quoteSequence++;this.quote=null;this.pricedKey='';this.pricing=false;
        if(!this.booted||!this.user||!this.team||this.running||this.inputError())return;
        this.quoteTimer=setTimeout(()=>this.prepare(true),800);
      },
      async submit(){
        if(this.busy||this.running||this.pricing)return;
        this.error=this.inputError();if(this.error)return;
        this.saveDraft(false);
        if(!this.user){await this.openLogin(true);return;}
        if(!this.team){this.$refs.teamDialog.showModal();return;}
        if(!this.readyQuote){await this.prepare();return;}
        if(!this.readyQuote.affordable){this.topUp();return;}
        if(Date.parse(this.readyQuote.expires_at)<=Date.now()){await this.prepare();return;}
        await this.startRun();
      },
      async prepare(quiet=false){
        clearTimeout(this.quoteTimer);
        if(!this.user||!this.team||this.running)return;
        const problem=this.inputError();if(problem){if(!quiet)this.error=problem;return;}
        const key=this.quoteKey,sequence=++this.quoteSequence,team=this.team;
        this.pricing=true;this.quote=null;this.error='';this.saveDraft(false);
        try{
          const q=await this.api('/arena/plans',{method:'POST',body:JSON.stringify({capability:this.taskId,identity:this.identity(),mode:this.mode,providers:this.customServices?this.services:null,max_cost_micro:10_000_000})},team);
          if(sequence!==this.quoteSequence||key!==this.quoteKey)return;
          this.quote=q;this.pricedKey=key;this.balance=q.balance_micro;
        }catch(e){if(sequence!==this.quoteSequence||key!==this.quoteKey)return;if(e.status===401){this.user=null;this.quote=null;if(!quiet)await this.openLogin(true);}else this.error=e.message;}
        finally{if(sequence===this.quoteSequence)this.pricing=false;}
      },
      topUp(){this.saveDraft(false);try{localStorage.setItem('treg-active',this.team);}catch{}location.assign('/app#billing');},
      async startRun(){
        if(this.busy||this.running||!this.readyQuote)return;
        const id=this.readyQuote.id;this.busy=true;this.error='';this.expandedResults=[];this.runTeam=this.team;clearTimeout(this.quoteTimer);
        try{await this.api('/arena/runs/'+id+'/start',{method:'POST'},this.runTeam);this.quote=null;write(ACTIVE,{id,team:this.runTeam,at:Date.now()});await this.pollRun(id);if(this.running)await this.refreshHistory();}
        catch(e){if(e.status===401){this.user=null;this.quote=null;await this.openLogin(true);}else if(e.status===402){this.topUp();}else{this.error=e.message;if(e.status===409){this.quote=null;await this.prepare(true);}}}
        finally{this.busy=false;}
      },
      async pollRun(id){
        clearTimeout(this.pollTimer);
        try{this.run=await this.api('/arena/runs/'+id,{},this.runTeam);this.pollFailures=0;
          if(this.run.state==='running')this.pollTimer=setTimeout(()=>this.pollRun(id),1500);
          else{await this.loadBalance();await this.refreshHistory();}
        }catch(e){this.error=e.message;this.pollFailures++;if(this.pollFailures<5&&e.status!==401&&e.status!==403&&e.status!==404)this.pollTimer=setTimeout(()=>this.pollRun(id),3000);}
      },
      manualLabel(r){const q=this.manualQuotes[r.id];return (q&&!q.affordable?'Top up':'Try')+' · '+this.usd(q?.estimate_micro??r.estimate_micro);},
      ratingValue(r){return r.rating?.value || (r.report?'down':'');},
      openReport(r){this.reporting=r.id;this.reportDrafts[r.id] ||= {reason:r.report?.reason||'',comment:r.report?.comment||''};},
      async rateResult(r,value){
        if(this.busy||this.running)return;
        if(value==='down')this.openReport(r);else this.reporting='';
        if(r.rating?.value===value)return;
        const previous=r.rating;this.busy=true;this.error='';r.rating={value};
        try{const saved=await this.api('/arena/runs/'+this.run.id+'/attempts/'+r.id+'/rating',{method:'POST',body:JSON.stringify({value})},this.runTeam);r.rating=saved.rating;}
        catch(e){r.rating=previous;this.reporting='';this.error='Could not save your rating. '+e.message;}
        finally{this.busy=false;}
      },
      async sendReport(r){
        if(this.busy||this.running||r.report||this.ratingValue(r)!=='down')return;
        this.busy=true;this.error='';
        try{const saved=await this.api('/arena/runs/'+this.run.id+'/attempts/'+r.id+'/report',{method:'POST',body:JSON.stringify(this.reportDrafts[r.id])},this.runTeam);r.report=saved.report;this.reporting='';}
        catch(e){this.error=e.message;}finally{this.busy=false;}
      },
      async tryVendor(r){
        if(this.busy||this.running||!r.can_try)return;
        const runId=this.run.id;this.busy=true;this.error='';
        try{
          let q=this.manualQuotes[r.id];const displayed=q?.estimate_micro??r.estimate_micro;
          if(!q||Date.parse(q.expires_at)<=Date.now()){
            q=await this.api('/arena/runs/'+runId+'/attempts/'+r.id+'/plan',{method:'POST'},this.runTeam);
            this.manualQuotes[r.id]=q;this.balance=q.balance_micro;
            if(q.estimate_micro>displayed){this.error='This service’s price changed. Review the updated price and click Try again.';return;}
          }
          if(!q.affordable){this.topUp();return;}
          await this.api('/arena/runs/'+runId+'/attempts/'+r.id+'/start',{method:'POST',body:JSON.stringify({quote_id:q.id})},this.runTeam);
          delete this.manualQuotes[r.id];this.quote=null;this.reporting='';
          await this.pollRun(runId);await this.refreshHistory();
        }catch(e){delete this.manualQuotes[r.id];if(e.status===402)this.topUp();else this.error=e.message;}
        finally{this.busy=false;}
      },
      async cancelRun(){this.busy=true;try{await this.api('/arena/runs/'+this.run.id+'/cancel',{method:'POST'},this.runTeam);}catch(e){this.error=e.message;}finally{this.busy=false;}},
      historyDate(value){return new Intl.DateTimeFormat(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}).format(new Date(value));},
      async refreshHistory(){
        const team=this.team,sequence=++this.historySequence;
        if(!this.user||!team){this.history=[];return;}
        try{const rows=await this.api('/arena/runs',{},team);if(sequence===this.historySequence&&team===this.team&&this.user)this.history=rows;}
        catch(e){if(sequence===this.historySequence&&team===this.team)this.error=e.message;}
      },
      async loadHistory(id){
        if(this.busy||this.running)return;
        this.busy=true;this.error='';clearTimeout(this.pollTimer);
        try{
          const result=await this.api('/arena/runs/'+id,{},this.team);
          this.runTeam=this.team;this.run=result;this.expandedResults=[];this.manualQuotes={};this.reporting='';this.pollFailures=0;
          this.taskId=result.capability;this.mode=result.mode;this.inputs={...result.identity};
          this.variant=Math.max(0,this.currentTask.variants.findIndex(v=>v.every(k=>k in this.inputs)));
          this.customServices=true;this.services=result.results.map(r=>r.provider);this.quote=null;this.saveDraft(false);
          write(ACTIVE,{id,team:this.team,at:Date.now()});
          if(this.running)this.pollTimer=setTimeout(()=>this.pollRun(id),1500);
        }catch(e){this.error=e.message;}finally{this.busy=false;}
      },
      async newSession(){
        if(this.busy||this.running)return;
        this.inputs={};this.variant=0;this.customServices=false;this.services=[];this.error='';
        await this.newQuery();this.saveDraft(false);document.querySelector('#composer input')?.focus();
      },
      async newQuery(){this.manualQuotes={};this.reporting='';this.expandedResults=[];this.run=null;this.quote=null;remove(ACTIVE);this.scheduleQuote();await this.$nextTick();document.querySelector('#composer')?.scrollIntoView({behavior:'smooth'});},
      tryWaterfall(){const previous=this.run;this.taskId=previous.capability;this.inputs={...previous.identity};this.variant=Math.max(0,this.currentTask.variants.findIndex(v=>v.every(k=>k in previous.identity)));this.mode='waterfall';this.customServices=false;this.services=[];this.newQuery();},
      exportResult(){const b=new Blob([JSON.stringify(this.run,null,2)],{type:'application/json'});const url=URL.createObjectURL(b);const a=document.createElement('a');a.href=url;a.download='enrich-arena-'+this.run.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);},
      async logout(){try{await this.api('/auth/logout',{method:'POST'});this.user=null;this.teams=[];this.team='';this.balance=null;this.run=null;this.quote=null;this.history=[];this.historySequence++;remove(ACTIVE);remove(DRAFT);}catch(e){this.error=e.message;}}
    },
    async mounted(){
      try{
        const [tasks,meta]=await Promise.all([this.api('/arena/tasks',{},''),this.api('/meta',{},'')]);this.tasks=tasks;this.meta=meta;
        const preset=new URLSearchParams(location.search).get('capability');if(tasks.some(t=>t.id===preset))this.taskId=preset;
        const presetMode=new URLSearchParams(location.search).get('mode');if(['compare','waterfall'].includes(presetMode))this.mode=presetMode;
        const pending=this.restoreDraft();await this.loadIdentity();this.booted=true;
        if(pending&&this.user){this.saveDraft(false);if(this.team)await this.prepare();else this.$refs.teamDialog.showModal();}
        else{const active=read(ACTIVE);if(this.user&&active&&active.team===this.team&&Date.now()-active.at<86400000){await this.loadHistory(active.id);}}
        if(!pending)this.scheduleQuote();
      }catch(e){this.error=e.message;}
    },
    beforeUnmount(){clearTimeout(this.pollTimer);clearTimeout(this.quoteTimer);this.quoteSequence++;this.historySequence++;}
  }).mount('#arena');
})();
