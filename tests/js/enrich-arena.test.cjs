const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../../src/treg/web/enrich-arena/arena.js'),'utf8');
function setup(){
  let options;const stored=new Map(),destinations=[];
  const storage={getItem:k=>stored.get(k)||null,setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)};
  const Vue={createApp:o=>{options=o;return {mount(){}};}};
  vm.runInNewContext(source,{Vue,window:{Vue},sessionStorage:storage,localStorage:storage,location:{assign:x=>destinations.push(x)},setTimeout:()=>1,clearTimeout(){},document:{querySelector:()=>null},URLSearchParams,Intl,Date});
  const app=options.data();for(const [k,f]of Object.entries(options.methods))app[k]=f.bind(app);
  for(const [k,f]of Object.entries(options.computed))Object.defineProperty(app,k,{get:f.bind(app)});
  app.tasks=[{id:'people.email.find',variants:[['full_name','domain']],fields:[]}];
  app.inputs={full_name:'Test Person',domain:'example.com'};app.user={id:1};app.team='test-team';app.booted=true;app.$nextTick=async()=>{};
  const quote=(extra={})=>({id:'q1',required_micro:25000,balance_micro:1000000,affordable:true,expires_at:new Date(Date.now()+60000).toISOString(),...extra});
  const price=(extra={})=>{app.quote=quote(extra);app.pricedKey=app.quoteKey;};
  return {app,quote,price,stored,destinations,components:options.components};
}
test('Waterfall is the default and the priced button includes its estimate',()=>{
 const {app,price}=setup();assert.equal(app.mode,'waterfall');price();assert.equal(app.runButtonLabel,'Run from $0.025');
});
test('Submitting a priced query starts directly, once, and consumes the quote',async()=>{
 const {app,price}=setup();price();let calls=0,release;app.api=()=>{calls++;return new Promise(r=>release=r);};app.pollRun=async()=>{app.run={state:'completed'};};
 const run=app.submit();await app.submit();assert.equal(calls,1);release({});await run;assert.equal(app.quote,null);
});
test('Pricing alone does not start or open a confirmation modal',async()=>{
 const {app,quote}=setup();const calls=[];app.api=async p=>{calls.push(p);return quote();};await app.submit();assert.deepEqual(calls,['/arena/plans']);assert.equal(app.readyQuote.id,'q1');
});
test('An edit invalidates a previous price and discards an in-flight response',async()=>{
 const {app,quote,price}=setup();price();let release;app.api=()=>new Promise(r=>release=r);const pricing=app.prepare();app.inputs.full_name='Different Person';app.scheduleQuote();release(quote());await pricing;assert.equal(app.readyQuote,null);assert.equal(app.pricing,false);
});
test('Changing input makes a quote unusable even before the watcher runs',()=>{
 const {app,price}=setup();price();app.mode='compare';assert.equal(app.readyQuote,null);
});
test('Insufficient credits go to the selected team billing page without dispatch',async()=>{
 const {app,price,destinations,stored}=setup();price({affordable:false});app.api=()=>assert.fail('Must not dispatch');await app.submit();assert.deepEqual(destinations,['/app#billing']);assert.equal(stored.get('treg-active'),'test-team');assert.equal(JSON.parse(stored.get('treg.arena.draft.v1')).pending,false);
});
test('Anonymous submission triggers login without sending a plan or paid request',async()=>{
 const {app}=setup();app.user=null;let login=false;app.openLogin=async pending=>{login=pending;};app.api=()=>assert.fail('Must not call before login');await app.submit();assert.equal(login,true);
});
test('Expired quotes refresh on the button without buying at an unseen price',async()=>{
 const {app,price,quote}=setup();price({expires_at:new Date(Date.now()-1000).toISOString()});const calls=[];app.api=async p=>{calls.push(p);return quote({required_micro:50000});};await app.submit();assert.deepEqual(calls,['/arena/plans']);assert.match(app.runButtonLabel,/0.05/);
});
test('Incomplete names never request pricing',async()=>{
 const {app}=setup();app.inputs.full_name='Test';app.api=()=>assert.fail('Must validate first');await app.submit();assert.match(app.error,/first and last name/);
});
test('A balance consumed elsewhere redirects a rejected start to top-up',async()=>{
 const {app,price,destinations}=setup();price();app.api=async()=>{throw Object.assign(new Error('Not enough credits'),{status:402});};await app.submit();assert.deepEqual(destinations,['/app#billing']);assert.equal(app.busy,false);
});

test('Vendor estimates appear before login or a complete input and follow input type',()=>{
 const {app}=setup();app.user=null;app.inputs={};app.tasks[0].variants.push(['linkedin_url']);
 app.tasks[0].provider_previews=[[{provider:'hunter',estimate_micro:24500}],[{provider:'fiber-ai',estimate_micro:40000}]];
 assert.equal(app.showProviderPreview,true);assert.equal(app.previewProviders[0].provider,'hunter');
 app.chooseVariant(1);assert.equal(app.previewProviders[0].provider,'fiber-ai');
 app.customServices=true;app.services=[];assert.equal(app.previewProviders.length,0);
});
test('Current team quotes replace catalog pricing, including own keys',()=>{
 const {app,price}=setup();app.tasks[0].provider_previews=[[{provider:'hunter',estimate_micro:24500}]];
 price({providers:[{provider:'hunter',estimate_micro:0,tier:'credential'}]});
 assert.equal(app.previewProviders[0].estimate_micro,0);assert.match(app.billingLabel(app.previewProviders[0]),/Your key/);
 app.inputs.domain='changed.com';assert.equal(app.previewProviders[0].estimate_micro,24500);
});
test('Results replace the matching preview; changing a query restores pricing without losing results',()=>{
 const {app}=setup();app.run={state:'completed',capability:app.taskId,mode:app.mode,identity:app.identity(),results:[]};
 assert.equal(app.showProviderPreview,false);app.inputs.domain='changed.com';assert.equal(app.showProviderPreview,true);assert.equal(app.run.state,'completed');
 app.run.state='running';assert.equal(app.showProviderPreview,false);
});

test('Fighters follow real attempt states and never label uncalled or failed services defeated',()=>{
 const {components}=setup();const component=components.ArenaFighters;const fighter={active:true,selected:[]};
 for(const [name,fn] of Object.entries(component.methods))fighter[name]=fn.bind(fighter);
 assert.equal(fighter.state({state:'running'}),'fighting');assert.equal(fighter.state({state:'queued'}),'waiting');
 assert.equal(fighter.state({state:'hit'}),'won');assert.equal(fighter.state({state:'miss'}),'defeated');
 for(const state of ['error','timeout'])assert.equal(fighter.state({state}),'error');
 for(const state of ['skipped','not_attempted'])assert.equal(fighter.state({state}),'benched');
 for(const state of ['cancelled','interrupted'])assert.equal(fighter.state({state}),'paused');
 fighter.active=false;assert.equal(fighter.state({state:'running'}),'paused');
 fighter.selected=['winner'];assert.equal(fighter.state({id:'winner',state:'hit'}),'champion');
 assert.equal(fighter.label({id:'winner',state:'hit'}),'Winner');
});

test('Battle mode selection invalidates pricing without dispatch and is locked during a run',()=>{
 const {app,price,components}=setup();price();const emitted=[];
 const fighter={selectable:true,disabled:false,active:false,mode:app.mode,$emit:(event,mode)=>{emitted.push(event);app.setMode(mode);}};
 const choose=components.ArenaFighters.methods.chooseMode.bind(fighter);
 app.api=()=>assert.fail('Selecting a mode must not dispatch');choose('compare');
 assert.equal(app.mode,'compare');assert.equal(app.readyQuote,null);assert.deepEqual(emitted,['change-mode']);
 fighter.active=true;choose('waterfall');assert.equal(emitted.length,1);
 fighter.active=false;fighter.disabled=true;choose('waterfall');assert.equal(emitted.length,1);
 app.run={state:'running'};app.setMode('waterfall');assert.equal(app.mode,'compare');
});

test('Session history is loaded without spending and stale team responses are discarded',async()=>{
 const {app}=setup();let release;app.api=()=>new Promise(r=>release=r);const pending=app.refreshHistory();
 app.team='different-team';release([{id:'old-team-session'}]);await pending;assert.equal(app.history.length,0);
 app.api=async path=>{assert.equal(path,'/arena/runs');return [];};await app.refreshHistory();assert.equal(app.history.length,0);
 app.user=null;app.api=()=>assert.fail('Anonymous visitors must not fetch private history');await app.refreshHistory();
});
test('Selecting a session restores its inputs and mode using only a read',async()=>{
 const {app,stored}=setup();const calls=[];app.quote={id:'old-quote'};
 app.api=async(path,options)=>{calls.push(path);assert.equal(options.method,undefined);return {id:'saved',state:'completed',capability:app.taskId,mode:'compare',identity:{full_name:'Saved Person',domain:'saved.com'},results:[]};};
 await app.loadHistory('saved');assert.deepEqual(calls,['/arena/runs/saved']);assert.equal(app.inputs.full_name,'Saved Person');assert.equal(app.mode,'compare');assert.equal(app.quote,null);assert.equal(app.run.id,'saved');assert.equal(JSON.parse(stored.get('treg.arena.active.v1')).id,'saved');
});
test('Failed session selection keeps the existing session and does not persist a failed id',async()=>{
 const {app,stored}=setup();app.run={id:'existing',state:'completed'};app.api=async()=>{throw new Error('Unavailable');};
 await app.loadHistory('missing');assert.equal(app.run.id,'existing');assert.equal(stored.has('treg.arena.active.v1'),false);assert.equal(app.busy,false);
});
test('New query clears current results and inputs while retaining the session list',async()=>{
 const {app,price,stored}=setup();price();app.run={id:'saved',state:'completed'};app.history=[{id:'saved'}];app.customServices=true;app.services=['hunter'];
 app.api=()=>assert.fail('Starting a blank query must not call anything');await app.newSession();
 assert.equal(app.run,null);assert.equal(Object.keys(app.inputs).length,0);assert.equal(app.quote,null);assert.equal(app.customServices,false);assert.equal(app.history.length,1);assert.equal(stored.has('treg.arena.active.v1'),false);assert.equal(JSON.parse(stored.get('treg.arena.draft.v1')).pending,false);
});
test('Trying an uncalled vendor prices then starts it once in the same session',async()=>{
 const {app}=setup();app.run={id:'session',state:'completed'};app.runTeam=app.team;const calls=[];
 app.api=async(path,options)=>{calls.push(path);return path.endsWith('/plan')?{id:'price',estimate_micro:100,affordable:true,expires_at:new Date(Date.now()+60000).toISOString()}:{};};
 app.pollRun=async id=>assert.equal(id,'session');app.refreshHistory=async()=>{};
 const row={id:'next',can_try:true,estimate_micro:100};const first=app.tryVendor(row);await app.tryVendor(row);await first;
 assert.deepEqual(calls,['/arena/runs/session/attempts/next/plan','/arena/runs/session/attempts/next/start']);
 row.can_try=false;await app.tryVendor(row);assert.equal(calls.length,2);
});
test('A higher manual price is displayed for another click before spending',async()=>{
 const {app}=setup();app.run={id:'session',state:'completed'};app.runTeam=app.team;const calls=[];
 app.api=async path=>{calls.push(path);return {id:'price',estimate_micro:200,affordable:true,expires_at:new Date(Date.now()+60000).toISOString()};};
 await app.tryVendor({id:'next',can_try:true,estimate_micro:100});assert.equal(calls.length,1);assert.match(app.manualLabel({id:'next'}),/0.0002/);assert.match(app.error,/price changed/);
});
test('Manual attempts with insufficient credits go to billing without starting',async()=>{
 const {app,destinations}=setup();app.run={id:'session',state:'completed'};app.runTeam=app.team;const calls=[];
 app.api=async path=>{calls.push(path);return {id:'price',estimate_micro:100,affordable:false,expires_at:new Date(Date.now()+60000).toISOString()};};
 await app.tryVendor({id:'next',can_try:true,estimate_micro:100});assert.equal(calls.length,1);assert.deepEqual(destinations,['/app#billing']);
});
test('Reporting incorrect data sends feedback without dispatching vendor calls',async()=>{
 const {app}=setup();app.run={id:'session',state:'completed'};app.runTeam=app.team;const calls=[];const row={id:'answer',rating:{value:'down'}};
 app.openReport(row);app.reportDrafts.answer.comment='Wrong company';
 app.api=async(path,options)=>{calls.push(path);assert.equal(JSON.parse(options.body).comment,'Wrong company');return {};};app.pollRun=async()=>{};
 await app.sendReport(row);assert.deepEqual(calls,['/arena/runs/session/attempts/answer/report']);assert.equal(app.reporting,'');
});
test('Clicking a result row toggles details while its action buttons remain independent',()=>{
 const {app}=setup();app.toggleResultRow({target:{closest:()=>null}},'answer');assert.equal(app.expandedResults.includes('answer'),true);
 app.toggleResultRow({target:{closest:()=>({tagName:'BUTTON'})}},'answer');assert.equal(app.expandedResults.includes('answer'),true);
 app.toggleResultRow({target:{closest:()=>null}},'answer');assert.equal(app.expandedResults.length,0);
});
test('Thumbs down saves immediately even when optional details are dismissed',async()=>{
 const {app}=setup();app.run={id:'session',state:'completed'};app.runTeam=app.team;
 const row={id:'answer'},calls=[];let release;
 app.api=(path,options)=>{calls.push(path);assert.equal(JSON.parse(options.body).value,'down');return new Promise(resolve=>release=resolve);};
 const save=app.rateResult(row,'down');assert.equal(app.ratingValue(row),'down');assert.equal(app.reporting,'answer');
 assert.equal(app.reportDrafts.answer.reason,'');app.reporting='';
 await app.rateResult(row,'up');assert.equal(calls.length,1);
 release({rating:{value:'down',created_at:'saved'}});await save;
 assert.equal(row.rating.created_at,'saved');assert.equal(app.reporting,'');assert.equal(row.report,undefined);
 assert.deepEqual(calls,['/arena/runs/session/attempts/answer/rating']);
 await app.rateResult(row,'down');assert.equal(calls.length,1);assert.equal(app.reporting,'answer');
});
test('Thumbs up saves without a form and can replace a downvote',async()=>{
 const {app}=setup();app.run={id:'session',state:'completed'};const row={id:'answer',rating:{value:'down'}};app.reporting='answer';
 app.api=async(path,options)=>{assert.ok(path.endsWith('/rating'));assert.equal(JSON.parse(options.body).value,'up');return {rating:{value:'up'}};};
 await app.rateResult(row,'up');assert.equal(row.rating.value,'up');assert.equal(app.reporting,'');assert.equal(app.reportDrafts.answer,undefined);
});
test('Failed rating saves restore the previous selection and show an error',async()=>{
 const {app}=setup();app.run={id:'session',state:'completed'};const previous={value:'up'},row={id:'answer',rating:previous};
 app.api=async()=>{throw new Error('Offline');};await app.rateResult(row,'down');
 assert.equal(row.rating,previous);assert.equal(app.reporting,'');assert.equal(app.busy,false);assert.match(app.error,/Could not save.*Offline/);
});
test('Avatar selection retains excluded vendors and prices only enabled services',async()=>{
 const {app,quote,stored}=setup();app.tasks[0].provider_previews=[[{provider:'hunter',estimate_micro:24500},{provider:'tomba',estimate_micro:8900}]];
 app.api=async(path,options)=>{assert.equal(path,'/arena/plans');const body=JSON.parse(options.body);assert.deepEqual(body.providers,['tomba']);assert.equal(body.max_cost_micro,10000000);return quote({providers:[{provider:'tomba',estimate_micro:0,tier:'credential'}]});};
 app.toggleProvider('hunter');assert.equal(app.customServices,true);assert.deepEqual(Array.from(app.enabledProviders),['tomba']);await app.prepare();
 assert.equal(app.availableProviders.length,2);assert.equal(app.previewProviders.length,1);assert.equal(app.previewProviders[0].estimate_micro,0);
 app.toggleProvider('hunter');assert.equal(app.readyQuote,null);assert.equal(app.availableProviders.length,2);assert.equal(app.enabledProviders.length,2);
 assert.equal(JSON.parse(stored.get('treg.arena.draft.v1')).budget,undefined);
});
test('Disabling every avatar blocks a run and changing input type resets the selection',async()=>{
 const {app}=setup();app.tasks[0].provider_previews=[[{provider:'hunter'}],[{provider:'tomba'}]];app.tasks[0].variants.push(['linkedin_url']);
 app.toggleProvider('hunter');assert.match(app.inputError(),/Enable at least one vendor/);app.api=()=>assert.fail('Empty selection cannot dispatch');await app.submit();
 app.chooseVariant(1);assert.equal(app.customServices,false);assert.deepEqual(Array.from(app.enabledProviders),['tomba']);
});
test('Avatar buttons are locked during calls and excluded is distinct from a failed result',()=>{
 const {app,components}=setup();app.tasks[0].provider_previews=[[{provider:'hunter'}]];app.run={state:'running'};
 app.toggleProvider('hunter');assert.equal(app.customServices,false);
 const events=[],fighter={choosable:true,active:false,disabled:false,enabledProviders:[],selected:[],$emit:(...args)=>events.push(args)};
 for(const [name,fn] of Object.entries(components.ArenaFighters.methods))fighter[name]=fn.bind(fighter);
 assert.equal(fighter.label({provider:'hunter'}),'Excluded');fighter.chooseProvider({provider:'hunter'});assert.deepEqual(events,[['toggle-provider','hunter']]);
 fighter.active=true;fighter.chooseProvider({provider:'hunter'});assert.equal(events.length,1);assert.equal(fighter.state({provider:'hunter',state:'running'}),'fighting');
});
test('Thumbs down defeats a successful fighter immediately, even a previous winner',async()=>{
 const {app,components}=setup();app.run={id:'session',state:'completed'};const row={id:'answer',state:'hit'};
 const fighter={active:false,selected:['answer']};for(const [name,fn] of Object.entries(components.ArenaFighters.methods))fighter[name]=fn.bind(fighter);
 let release;app.api=()=>new Promise(resolve=>release=resolve);
 const save=app.rateResult(row,'down');assert.equal(fighter.state(row),'defeated');assert.equal(fighter.label(row),'Thumbs down');
 release({rating:{value:'down'}});await save;assert.equal(fighter.state(row),'defeated');
 row.rating={value:'up'};assert.equal(fighter.state(row),'champion');
 row.report={reason:'wrong_person'};row.rating=null;assert.equal(fighter.state(row),'defeated');
});
test('Battle awards use actual successful results, share ties, and omit rejected or unknown results',()=>{
 const {app}=setup();app.run={mode:'compare',state:'completed',results:[
 {id:'a',state:'hit',duration_ms:100,charged_micro:20,estimate_micro:1},
 {id:'b',state:'hit',duration_ms:200,charged_micro:10,estimate_micro:999},
 {id:'c',state:'hit',duration_ms:100,charged_micro:10},
 {id:'d',state:'miss',duration_ms:0,charged_micro:0},
 {id:'e',state:'hit',duration_ms:1,charged_micro:0,rating:{value:'down'}}]};
 assert.deepEqual(JSON.parse(JSON.stringify(app.battleAwards)),{a:['Fastest'],b:['Cheapest'],c:['Fastest','Cheapest']});
 app.run.results[2].charged_micro=null;assert.equal(Object.values(app.battleAwards).flat().includes('Cheapest'),false);
 app.run.results[2].charged_micro=0;assert.deepEqual(Array.from(app.battleAwards.c),['Fastest','Cheapest']);
 app.run.state='running';assert.equal(Object.keys(app.battleAwards).length,0);
 app.run.state='completed';app.run.mode='waterfall';assert.equal(Object.keys(app.battleAwards).length,0);
});
test('One thumbs-up wins over faster and cheaper unrated results in either mode',()=>{
 const {app}=setup();app.run={mode:'compare',state:'completed',vote:{selected:['fast']},results:[
 {id:'fast',state:'hit',duration_ms:1,charged_micro:0},
 {id:'chosen',state:'hit',duration_ms:200,charged_micro:50,rating:{value:'up'}}]};
 assert.deepEqual(JSON.parse(JSON.stringify(app.battleAwards)),{fast:['Fastest','Cheapest']});assert.deepEqual(Array.from(app.winnerIds),['chosen']);
 assert.match(app.awardTitle('Fastest'),/successful, non-downvoted/);
 app.run.results[0].duration_ms=300;app.run.results[0].charged_micro=100;assert.deepEqual(Array.from(app.battleAwards.chosen),['Fastest','Cheapest']);assert.deepEqual(Array.from(app.winnerIds),['chosen']);
 app.run.mode='waterfall';assert.deepEqual(Array.from(app.winnerIds),['chosen']);
 app.run.results[1].rating.value='down';assert.equal(app.winnerIds.length,0);
});
test('Multiple thumbs-up compare metrics only inside that set, updating when a vote changes',()=>{
 const {app}=setup();app.run={mode:'compare',state:'completed',results:[
 {id:'unrated',state:'hit',duration_ms:0,charged_micro:0},
 {id:'quick',state:'hit',duration_ms:100,charged_micro:50,rating:{value:'up'}},
 {id:'cheap',state:'hit',duration_ms:200,charged_micro:10,rating:{value:'up'}},
 {id:'other',state:'hit',duration_ms:300,charged_micro:100,rating:{value:'up'}}]};
 assert.deepEqual(JSON.parse(JSON.stringify(app.battleAwards)),{quick:['Fastest'],cheap:['Cheapest']});
 assert.deepEqual(Array.from(app.winnerIds),['quick','cheap']);
 app.run.results[2].rating.value='down';assert.deepEqual(JSON.parse(JSON.stringify(app.battleAwards)),{quick:['Fastest','Cheapest']});
 app.run.results[3].rating.value='down';assert.deepEqual(JSON.parse(JSON.stringify(app.battleAwards)),{unrated:['Fastest','Cheapest']});
 app.run.state='running';assert.equal(app.winnerIds.length,0);
});
