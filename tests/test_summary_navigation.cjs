const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const handlers={},stored=new Map();let calls=0;
function node(){return{dataset:{},hidden:false,textContent:'',attrs:{},setAttribute(k,v){this.attrs[k]=v;},classList:{values:new Set(),toggle(k,on){if(on)this.values.add(k);else this.values.delete(k);},contains(k){return this.values.has(k);}},scrollIntoView(){this.scrolled=true;}};}
const cards=['verified','review','unverified'].map(status=>{const c=node();c.dataset.checkStatus=status;c.details=[{open:false},{open:false}];c.querySelector=()=>c.details[0];c.querySelectorAll=()=>c.details;return c;});
const groups=cards.map(card=>{const g=node();g.querySelector=()=>card.classList.contains('summary-filtered-out')?null:card;return g;});
const buttons=['verified','review','unverified'].map(status=>{const b=node();b.dataset.summaryFilter=status;return b;});
const note=node(),clear=node(),all={checked:false};
const main=node();main.querySelectorAll=s=>s==='[data-check-status]'?cards:s==='.system-group'?groups:buttons;
const document={forms:[],hidden:false,querySelector:s=>s==='main'?main:null,querySelectorAll:()=>[],getElementById:id=>({'view-all':all,'summary-filter-status':note,'summary-filter-clear':clear}[id]),addEventListener:(name,fn)=>(handlers[name]??=[]).push(fn)};
const window={};window.parent=window;
vm.runInNewContext(fs.readFileSync('adr_service_agent/dashboard_live.js','utf8'),{document,window,location:{pathname:'/test'},sessionStorage:{getItem:k=>stored.get(k),setItem:(k,v)=>stored.set(k,v),removeItem:k=>stored.delete(k)},addEventListener(){},matchMedia:()=>({matches:false}),setTimeout(){},setInterval(){},Intl,Date,fetch(){calls++;}});
function click(button,selector='button[data-summary-filter]'){for(const fn of handlers.click)fn({preventDefault(){},target:{closest:s=>s===selector?button:null}});}
click(buttons[1]);assert.equal(all.checked,true);assert.equal(main.dataset.summaryFilter,'review');
assert.equal(cards[0].classList.contains('summary-filtered-out'),true);assert.equal(cards[1].details.every(d=>d.open),true);assert.equal(cards[1].scrolled,true);assert.equal(groups[2].classList.contains('summary-filtered-out'),true);assert.equal(buttons[1].attrs['aria-pressed'],'true');assert.match(note.textContent,/1 to review/);
click(clear,'#summary-filter-clear');assert.equal(main.dataset.summaryFilter,undefined);assert.equal(cards.some(c=>c.classList.contains('summary-filtered-out')),false);
click(buttons[2]);assert.equal(cards[2].scrolled,true);
for(const fn of handlers.change)fn({target:{checked:true,id:'view-network',matches:()=>true}});
assert.equal(main.dataset.summaryFilter,undefined);assert.equal(calls,0);
console.log('Summary navigation passed: matching systems, expanded findings, clear filter, category navigation, no device/network actions.');
