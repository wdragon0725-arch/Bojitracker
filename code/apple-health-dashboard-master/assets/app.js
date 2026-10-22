const $ = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => [...root.querySelectorAll(s)];
const TODAY = dateKey(new Date());

const FOOD_DB = (window.BOJI_KB?.foods || []).map(food => ({
  ...food,
  note: food.reference_note || food.nutrient_values_note || ''
}));
const STRENGTH_EXERCISES={
  '下肢':['深蹲','前蹲','硬拉','罗马尼亚硬拉','腿举','保加利亚分腿蹲','腿屈伸','腿弯举','站姿提踵'],
  '胸部':['卧推','上斜卧推','哑铃卧推','器械推胸','绳索夹胸','俯卧撑'],
  '背部':['高位下拉','引体向上','杠铃划船','坐姿划船','单臂哑铃划船','直臂下压'],
  '肩部':['肩上推举','哑铃推举','侧平举','反向飞鸟','面拉'],
  '手臂':['杠铃弯举','哑铃弯举','绳索下压','臂屈伸'],
  '核心':['卷腹','平板支撑','悬垂举腿','绳索伐木']
};
const EXERCISE_DEFAULT_WEIGHT={'深蹲':40,'硬拉':50,'卧推':30,'高位下拉':30,'坐姿划船':30,'引体向上':0,'俯卧撑':0,'平板支撑':0};
const STORE_KEY='boji-health-v1';
const emptyState=()=>({profile:null,foods:[],workouts:[],expenses:[],chat:{messages:[]},settings:{monthlyBudgets:{}},health:{daily:{},workouts:[],loaded:false,minDate:null,maxDate:null}});
const state=emptyState();
let currentUser=null,syncTimer=null,syncQueue=Promise.resolve(),serverRevision=null,syncConflict=false,authMode='login';
let recordSummaries={seven:null,thirty:null};
let analyticsReports={seven:null,thirty:null},analyticsRequestId=0;
let pendingFoodLookup=null,foodLookupRequestId=0;
let charts={},agentBusy=false,selectedFoodDate=TODAY;

function snapshotState(){return {profile:state.profile,foods:state.foods,workouts:state.workouts,expenses:state.expenses,chat:state.chat,settings:state.settings,health:state.health};}
function accountState(payload=snapshotState()){return {profile:payload.profile,chat:payload.chat,settings:payload.settings,health:payload.health};}
function recordMeta(recordType,valueSource,sourceRef=null,confidence=null,entryMethod='manual',createdAt=new Date().toISOString()){
  return {schemaVersion:1,recordType,ownerUsername:currentUser?.username||'',entryMethod,valueSource,sourceRef,confidence,createdAt,updatedAt:createdAt,version:1};
}
function hydrateState(data={}){
  const next=Object.assign(emptyState(),data||{});
  next.foods=Array.isArray(next.foods)?next.foods:[];next.workouts=Array.isArray(next.workouts)?next.workouts:[];next.expenses=Array.isArray(next.expenses)?next.expenses:[];
  next.chat=next.chat||{messages:[]};next.chat.messages=Array.isArray(next.chat.messages)?next.chat.messages:[];
  next.settings=next.settings||{monthlyBudgets:{}};next.settings.monthlyBudgets=next.settings.monthlyBudgets||{};
  next.health=next.health||{daily:{},workouts:[],loaded:false,minDate:null,maxDate:null};next.health.daily=next.health.daily||{};next.health.workouts=next.health.workouts||[];
  Object.keys(state).forEach(k=>delete state[k]);Object.assign(state,next);
}
async function api(path,options={}){
  const response=await fetch(path,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(options.headers||{})},...options});
  let payload={};try{payload=await response.json();}catch{}
  if(!response.ok){const error=new Error(payload.error||`请求失败（${response.status}）`);error.status=response.status;throw error;}
  return payload;
}
function setSyncStatus(text){const el=$('#syncStatus');if(el)el.textContent=text;}
function syncNow(payload=snapshotState()){
  if(!currentUser)return Promise.resolve();
  if(syncConflict)return Promise.reject(new Error('数据已在其他位置更新，请刷新页面后重试'));
  setSyncStatus('正在保存到本机数据库…');
  syncQueue=syncQueue.catch(()=>{}).then(async()=>{
    if(syncConflict)throw new Error('数据已在其他位置更新，请刷新页面后重试');
    const result=await api('/api/data',{method:'PUT',body:JSON.stringify({data:accountState(payload),expectedUpdatedAt:serverRevision})});
    serverRevision=result.updatedAt;
    setSyncStatus('数据已保存到本机数据库，并按用户名隔离。');
  }).catch(error=>{if(error.status===409)syncConflict=true;setSyncStatus(error.status===409?'发现其他位置的新数据，请刷新页面；当前页面不会覆盖数据库。':'保存失败，请保持本地服务运行。');toast(error.message);throw error;});
  return syncQueue;
}
function save(){
  if(!currentUser)return;
  const payload=snapshotState();
  cacheState(payload);
  clearTimeout(syncTimer);syncTimer=setTimeout(()=>syncNow(payload).catch(()=>{}),350);
}
function cacheState(payload=snapshotState()){
  if(currentUser)localStorage.setItem(`${STORE_KEY}:${currentUser.username}`,JSON.stringify(payload));
}
async function createRecord(kind,record,{refresh=true}={}){
  const result=await api(`/api/records/${kind}`,{method:'POST',body:JSON.stringify({record})});
  const collection={food:'foods',workout:'workouts',expense:'expenses'}[kind];
  state[collection].push(result.record);cacheState();if(refresh){renderAll();refreshRecordSummaries();}
  return result.record;
}
async function removeRecord(kind,record){
  await api(`/api/records/${kind}/${encodeURIComponent(record.id)}`,{method:'DELETE',body:JSON.stringify({expectedVersion:record.meta?.version||1})});
  const collection={food:'foods',workout:'workouts',expense:'expenses'}[kind];
  state[collection]=state[collection].filter(item=>item.id!==record.id);cacheState();renderAll();refreshRecordSummaries();
}
async function refreshRecordSummaries(){
  if(!currentUser)return;
  const requestId=++analyticsRequestId,user=currentUser.username;
  try{
    const [seven,thirty]=await Promise.all([api('/api/analytics?days=7'),api('/api/analytics?days=30')]);
    if(requestId!==analyticsRequestId||currentUser?.username!==user)return;
    analyticsReports={seven:seven.report,thirty:thirty.report};
    recordSummaries={seven:seven.report.summary,thirty:thirty.report.summary};renderAll();
  }catch(error){if(requestId!==analyticsRequestId)return;analyticsReports={seven:null,thirty:null};recordSummaries={seven:null,thirty:null};console.warn('分析汇总暂不可用',error);renderToday();}
}
function fmt(n,d=0){return Number.isFinite(n)?n.toLocaleString('zh-CN',{maximumFractionDigits:d}):'—';}
function money(n){return `¥${Number(n||0).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})}`;}
function currentMonth(){return TODAY.slice(0,7);}
function escapeHTML(value){return String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));}
function toast(msg){const t=$('#toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2200);}
function openModal(id){$('#'+id).classList.remove('hidden');}
function closeModal(id){$('#'+id).classList.add('hidden');}
function dateText(d=new Date()){return d.toLocaleDateString('zh-CN',{month:'long',day:'numeric',weekday:'long'});}
function dateKey(d){const x=new Date(d);return `${x.getFullYear()}-${String(x.getMonth()+1).padStart(2,'0')}-${String(x.getDate()).padStart(2,'0')}`;}
function days(n){return Array.from({length:n},(_,i)=>{const d=new Date();d.setDate(d.getDate()-n+1+i);return dateKey(d);});}
function daysEnding(day,n){const end=new Date(`${day}T12:00:00`);return Array.from({length:n},(_,i)=>{const d=new Date(end);d.setDate(d.getDate()-n+1+i);return dateKey(d);});}
function pct(a,b){return b?Math.min(100,Math.max(0,a/b*100)):0;}

function targets(profile=state.profile){
  if(!profile) return null;
  const report=analyticsReports.seven;
  if(profile===state.profile&&report?.targets&&report.targetInputs&&['weight','height','age','sex','goal','trainingDays'].every(key=>String(profile[key]??'')===String(report.targetInputs[key]??'')))return report.targets;
  const p=profile,w=+p.weight,h=+p.height,a=+p.age;
  const bmr=9.99*w+6.25*h-4.92*a+(p.sex==='male'?5:-161);
  const base=bmr/.7;
  const trainingBonus=(p.sex==='male'?200:150)*(+p.trainingDays/7);
  const tdee=base+trainingBonus;
  const calories=Math.round(tdee*(p.goal==='lose'?.8:p.goal==='gain'?1.05:1));
  const protein=Math.round(w*(p.goal==='maintain'?1.6:1.8));
  const fat=Math.round(p.goal==='gain'?(p.sex==='male'?80:70):(p.sex==='male'?60:50));
  const carbs=Math.max(80,Math.round((calories-protein*4-fat*9)/4));
  const bmi=w/((h/100)**2);
  return {bmr:Math.round(bmr),tdee:Math.round(tdee),calories,protein,fat,carbs,bmi};
}
function foodNutrients(food,amount,unit){
  const factor=unit==='serving'?amount:amount/100;
  return {kcal:food.kcal*factor,carb:food.carb*factor,protein:food.protein*factor,fat:food.fat*factor};
}
function todayFoods(){return state.foods.filter(x=>x.date===TODAY);}
function foodsOn(day=selectedFoodDate){return state.foods.filter(x=>x.date===day);}
function sums(list){return list.reduce((a,x)=>({kcal:a.kcal+x.kcal,carb:a.carb+x.carb,protein:a.protein+x.protein,fat:a.fat+x.fat}),{kcal:0,carb:0,protein:0,fat:0});}

function setAuthMode(mode){
  authMode=mode;$$('[data-auth-mode]').forEach(b=>b.classList.toggle('active',b.dataset.authMode===mode));
  const registering=mode==='register';$('#confirmPasswordRow').classList.toggle('hidden',!registering);$('#confirmPasswordRow input').required=registering;
  $('#authTitle').textContent=registering?'创建本地账户':'欢迎回来';$('#authSubtitle').textContent=registering?'用户名不可重复，注册后会为你创建独立数据空间。':'登录后读取保存在本机数据库中的个人记录。';
  $('#authSubmit').textContent=registering?'注册并进入':'登录';$('#authForm [name=password]').autocomplete=registering?'new-password':'current-password';$('#authMessage').textContent='';
}
function legacyState(){try{return JSON.parse(localStorage.getItem(STORE_KEY)||'{}');}catch{return {};}}
function hasUserData(data){return !!(data?.profile||data?.foods?.length||data?.workouts?.length||data?.expenses?.length);}
async function activateUser(result){
  currentUser=result.user;serverRevision=result.updatedAt;syncConflict=false;let data=result.data||{},migrated=false;
  if(result.created){const old=legacyState();if(hasUserData(old)){data=old;migrated=true;}}
  hydrateState(data);$('#currentUsername').textContent=currentUser.username;$('#authGate').classList.add('hidden');document.body.classList.remove('auth-locked');renderAll();
  const requestedView=location.hash.slice(1);if($(`#view-${requestedView}`))showView(requestedView);else showView('today');
  if(migrated){
    const legacyRecords={foods:[...state.foods],workouts:[...state.workouts],expenses:[...state.expenses]};
    state.foods=[];state.workouts=[];state.expenses=[];
    await syncNow(snapshotState()).catch(()=>{});
    try{
      for(const [kind,records] of [['expense',legacyRecords.expenses],['workout',legacyRecords.workouts],['food',legacyRecords.foods]]){
        for(const record of records)await createRecord(kind,record,{refresh:false});
      }
      renderAll();refreshRecordSummaries();
      toast('旧版浏览器数据已迁移到该账户');
    }catch(error){toast(`旧记录迁移未完成：${error.message}`);}
  }
  else setSyncStatus('数据已从本机数据库载入，并按用户名隔离。');
  refreshRecordSummaries();
  if(!state.profile)setTimeout(()=>openModal('profileModal'),350);
}
function showAuth(message=''){
  currentUser=null;serverRevision=null;syncConflict=false;analyticsRequestId++;analyticsReports={seven:null,thirty:null};recordSummaries={seven:null,thirty:null};clearTimeout(syncTimer);hydrateState();$('#authGate').classList.remove('hidden');document.body.classList.add('auth-locked');$('#currentUsername').textContent='—';$('#authMessage').textContent=message;renderAll();
}
function setupAuth(){
  $$('[data-auth-mode]').forEach(b=>b.onclick=()=>setAuthMode(b.dataset.authMode));setAuthMode('login');
  $('#authForm').onsubmit=async e=>{
    e.preventDefault();const authForm=e.currentTarget,form=Object.fromEntries(new FormData(authForm).entries()),submit=$('#authSubmit');$('#authMessage').textContent='';
    if(authMode==='register'&&form.password!==form.confirmPassword){$('#authMessage').textContent='两次输入的密码不一致';return;}
    submit.disabled=true;submit.textContent=authMode==='register'?'正在创建…':'正在登录…';
    try{const result=await api(`/api/auth/${authMode}`,{method:'POST',body:JSON.stringify({username:form.username,password:form.password})});authForm.reset();await activateUser(result);}
    catch(error){$('#authMessage').textContent=error.status===404?'请使用“启动本地服务.bat”运行网站。':error.message;}
    finally{submit.disabled=false;submit.textContent=authMode==='register'?'注册并进入':'登录';}
  };
  $('#logoutBtn').onclick=async()=>{clearTimeout(syncTimer);await syncNow().catch(()=>{});await api('/api/auth/logout',{method:'POST',body:'{}'}).catch(()=>{});closeModal('profileModal');showAuth('已安全退出，数据库中的记录仍会保留。');};
}
async function restoreSession(){
  try{const result=await api('/api/session');await activateUser(result);}
  catch(error){showAuth(error.status===401?'':'无法连接本地数据库，请使用“启动本地服务.bat”运行网站。');}
}

function setupNav(){
  $$('.nav-item').forEach(b=>b.addEventListener('click',()=>showView(b.dataset.view)));
  $$('[data-go]').forEach(b=>b.addEventListener('click',()=>showView(b.dataset.go)));
  $('#mobileMenu').onclick=()=>$('.sidebar').classList.toggle('open');
}
function showView(name){
  if(!$(`#view-${name}`))return;
  $$('.view').forEach(v=>v.classList.toggle('active',v.id===`view-${name}`));
  $$('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.view===name));
  $('.sidebar').classList.remove('open');
  const titles={today:`${greeting()}，${state.profile?.name||'你好'}`,food:'今天吃得怎么样？',training:'训练记录',expense:'每日花销',agent:'智能助理'};
  $('#viewTitle').textContent=titles[name];
  if(location.hash!==`#${name}`)history.replaceState(null,'',`#${name}`);
  if(name==='expense') renderExpenses();
  if(name==='agent') renderAgent(true);
}
function greeting(){const h=new Date().getHours();return h<11?'早上好':h<14?'中午好':h<18?'下午好':'晚上好';}

function renderProfile(){
  const p=state.profile,t=targets();
  const username=currentUser?.username||'用户';
  if(!p){$('#profileName').textContent='设置个人资料';$('#profileAvatar').textContent=username.slice(0,1).toUpperCase();$('#profileMeta').textContent=`@${username} · 点击完善`;return;}
  const displayName=p.name||username;$('#profileName').textContent=displayName;$('#profileAvatar').textContent=displayName.slice(0,1).toUpperCase();
  $('#profileMeta').textContent=`@${username} · 长期${p.goal==='lose'?'减脂':p.goal==='gain'?'增肌':'维持'} · ${p.weight} kg`;
  $('#weightToday').textContent=`${p.weight} kg`;$('#bmiToday').textContent=`BMI ${t.bmi.toFixed(1)}`;
}
function renderToday(){
  const t=targets(),metrics=analyticsReports.seven?.metrics,serverToday=recordSummaries.seven?.food?.daily?.find(row=>row.day===TODAY),hasToday=metrics?metrics['food.kcal_today']?.status==='observed':todayFoods().length>0;
  const s=serverToday?{kcal:serverToday.kcal,carb:serverToday.carb,protein:serverToday.protein,fat:serverToday.fat}:sums(todayFoods());
  if(t){
    const remaining=Math.max(0,t.calories-s.kcal);$('#energyRemaining').textContent=hasToday?fmt(remaining):'—';
    $('#energyRing').style.setProperty('--percent',hasToday?pct(s.kcal,t.calories)+'%':'0%');
    [['carb',s.carb,t.carbs],['protein',s.protein,t.protein],['fat',s.fat,t.fat]].forEach(([k,a,b])=>{$(`#${k}Text`).textContent=`${hasToday?fmt(a,1):'—'} / ${b} g`;$(`#${k}Bar`).style.width=hasToday?pct(a,b)+'%':'0%';});
    $('#energyDetail').textContent=hasToday?`已记录摄入 ${fmt(s.kcal)} / ${t.calories} kcal · 基础代谢 ${t.bmr} kcal`:`今天尚无饮食记录 · 建议目标 ${t.calories} kcal`;
    $('#goalLabel').textContent=`长期 · ${state.profile.goal==='lose'?'稳健减脂':state.profile.goal==='gain'?'干净增肌':'维持体重'}`;
  }
  const ws=thisWeekWorkouts(),recentDays=days(7);$('#weeklyWorkouts').textContent=metrics?.['training.sessions']?.value??ws.length;$('#workoutMinutes').textContent=`${fmt(metrics?.['training.volume_kg']?.value??ws.reduce((a,x)=>a+(+x.volume||0),0))} kg 训练容量`;
  $('#mealsToday').textContent=serverToday?.records??todayFoods().length;$('#foodDaysWeekly').textContent=metrics?.['food.recorded_days']?.value??recentDays.filter(day=>state.foods.some(x=>x.date===day)).length;
  renderTimeline();renderWeekChart();
}
function renderTimeline(){
  const list=todayFoods();const el=$('#todayTimeline');
  if(!list.length){el.innerHTML='<div class="empty-state">今天还没有饮食记录。<br>从记录第一餐开始。</div>';return;}
  el.innerHTML=list.slice(-5).reverse().map(x=>`<div class="timeline-item"><span class="timeline-time">${x.time||'今天'}</span><div><strong>${x.name}</strong><small>${x.meal} · ${x.amount}${x.unit==='serving'?' 份':' g'}</small></div><span class="timeline-value">${fmt(x.kcal)} kcal</span></div>`).join('');
}
function renderFood(){
  const t=targets(),all=foodsOn(),s=sums(all),isToday=selectedFoodDate===TODAY,selectedLabel=isToday?'今天':new Date(`${selectedFoodDate}T12:00:00`).toLocaleDateString('zh-CN',{month:'long',day:'numeric',weekday:'short'});
  if($('#foodDate'))$('#foodDate').value=selectedFoodDate;
  $('#foodPageTitle').textContent=`${selectedLabel}吃了什么`;
  $('#foodSummary').innerHTML=[['当日热量',`${all.length?fmt(s.kcal):'—'} / ${t?.calories||'—'} kcal`],['当日碳水',`${all.length?fmt(s.carb,1):'—'} / ${t?.carbs||'—'} g`],['当日蛋白质',`${all.length?fmt(s.protein,1):'—'} / ${t?.protein||'—'} g`],['当日脂肪',`${all.length?fmt(s.fat,1):'—'} / ${t?.fat||'—'} g`]].map(x=>`<div><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');
  $('#mealGrid').innerHTML=['早餐','午餐','晚餐','加餐'].map(meal=>{const rows=all.filter(x=>x.meal===meal);return `<article class="meal-card"><div class="meal-header"><h3>${meal}</h3><span>${fmt(sums(rows).kcal)} kcal</span></div>${rows.length?rows.map(x=>`<div class="food-row"><div><strong>${x.name}</strong><small>${x.amount}${x.unit==='serving'?' 份':' g'} · 碳 ${fmt(x.carb,1)}g · 蛋 ${fmt(x.protein,1)}g · 脂 ${fmt(x.fat,1)}g${x.nutritionSource?` · ${x.nutritionSource==='ai_estimate'?'AI估算':'知识库'}`:''}</small></div><b>${fmt(x.kcal)} kcal</b><button class="delete-btn" data-del-food="${x.id}" aria-label="删除">×</button></div>`).join(''):'<div class="empty-state">尚未记录</div>'}<button class="add-meal" data-add-meal="${meal}">＋ 添加${meal}</button></article>`}).join('');
  $$('[data-del-food]').forEach(b=>b.onclick=async()=>{const record=state.foods.find(x=>x.id===b.dataset.delFood);if(!record)return;try{await removeRecord('food',record);}catch(error){toast(error.message);}});
  $$('[data-add-meal]').forEach(b=>b.onclick=()=>{$('#foodForm [name=meal]').value=b.dataset.addMeal;openFood();});
  const insights=[];if(t&&all.length){if(s.protein<t.protein*.6)insights.push(['蛋白质仍有空间',`距离目标约 ${fmt(t.protein-s.protein)} g，可优先选择瘦肉、鱼虾、蛋奶或豆制品。`]);if(s.fat>t.fat*1.1)insights.push(['脂肪已接近上限','留意坚果、烹调油、高脂肉和糖油混合食物。']);if(s.carb<t.carbs*.4)insights.push(['碳水摄入偏少','主食可按训练安排分配，训练前后优先保证易消化碳水。']);}
  insights.push(['不要只看单餐','优先观察一周平均值与体重趋势，日间误差很常见。']);
  $('#foodInsights').innerHTML=insights.slice(0,3).map(x=>`<div><strong>${x[0]}</strong><p>${x[1]}</p></div>`).join('');
  const range=daysEnding(selectedFoodDate,7),daily=range.map(day=>foodsOn(day).length?sums(foodsOn(day)):null),recorded=daily.filter(Boolean),recordedDays=recorded.length;
  const avgKcal=recordedDays?recorded.reduce((a,x)=>a+x.kcal,0)/recordedDays:0,avgProtein=recordedDays?recorded.reduce((a,x)=>a+x.protein,0)/recordedDays:0;
  const onTarget=t?recorded.filter(x=>x.kcal>=t.calories*.8&&x.kcal<=t.calories*1.2).length:0;
  $('#foodHistoryStats').innerHTML=[['已记录天数',`${recordedDays} / 7`,'有记录不代表当天三餐完整'],['平均热量',recordedDays?`${fmt(avgKcal)} kcal`:'—','仅按有记录的日期计算'],['平均蛋白质',recordedDays?`${fmt(avgProtein,1)} g`:'—',t?`每日建议 ${t.protein} g`:'完善资料后生成建议'],['有记录且建议范围内',t&&recordedDays?`${onTarget} 天`:'—','按每日建议的 80%–120% 计算']].map(x=>`<div><span>${x[0]}</span><strong>${x[1]}</strong><small>${x[2]}</small></div>`).join('');
  chart('foodHistoryChart',{type:'bar',data:{labels:range.map(d=>d.slice(5)),datasets:[{label:'已记录摄入热量',data:daily.map(x=>x?.kcal??null),backgroundColor:'#db7444',borderRadius:5},{label:'每日建议',type:'line',data:range.map(()=>t?.calories||null),borderColor:'#1f6b52',borderWidth:2,pointRadius:0,tension:0}]},options:{...baseOptions(),plugins:{legend:{display:false}}}});
}
function strengthWorkouts(){return state.workouts.filter(x=>x.type==='力量训练'||Array.isArray(x.exercises)&&x.exercises.length);}
function thisWeekWorkouts(){const d=new Date();d.setDate(d.getDate()-6);const key=dateKey(d);return strengthWorkouts().filter(x=>x.date>=key);}
function exerciseOptions(){return Object.entries(STRENGTH_EXERCISES).map(([group,names])=>`<optgroup label="${group}">${names.map(name=>`<option value="${name}">${name}</option>`).join('')}</optgroup>`).join('');}
function estimateStrengthCalories(exercises=workoutExercises(),rpe=+$('#workoutRpe')?.value||7){const bodyWeight=+state.profile?.weight||70,totalSets=exercises.reduce((sum,x)=>sum+x.sets,0),totalReps=exercises.reduce((sum,x)=>sum+x.sets*x.reps,0),volume=exercises.reduce((sum,x)=>sum+x.volume,0);if(!totalSets)return {calories:0,totalSets,totalReps,volume,bodyWeight,rpe};const averageLoad=totalReps?volume/totalReps:0,estimatedMinutes=Math.max(8,totalSets*2.4+exercises.length*2),met=3.5+Math.max(0,rpe-4)*.45+Math.min(1.5,averageLoad/bodyWeight*1.5),calories=Math.round(met*3.5*bodyWeight/200*estimatedMinutes);return {calories,totalSets,totalReps,volume,bodyWeight,rpe};}
function workoutCalories(record){return Array.isArray(record.exercises)&&record.exercises.length?estimateStrengthCalories(record.exercises,+record.rpe||7).calories:+record.calories||0;}
function updateTrainingEstimate(){const estimate=estimateStrengthCalories();$('#trainingCalories').textContent=`${fmt(estimate.calories)} kcal`;$('#trainingEstimateNote').textContent=estimate.totalSets?`按 ${fmt(estimate.bodyWeight,1)} kg 体重 · ${estimate.totalSets} 组 · RPE ${estimate.rpe} 估算`:'添加动作并填写组次后自动估算';return estimate;}
function updateTrainingVolume(){let total=0;$$('.exercise-row',$('#exerciseRows')).forEach(row=>{const weight=+row.querySelector('[data-weight]').value||0,sets=+row.querySelector('[data-sets]').value||0,reps=+row.querySelector('[data-reps]').value||0,volume=weight*sets*reps;row.querySelector('[data-row-volume]').textContent=`${fmt(volume)} kg`;total+=volume;});$('#trainingVolume').textContent=`${fmt(total)} kg`;updateTrainingEstimate();return total;}
function addExerciseRow(name='深蹲',weight=null,sets=3,reps=8){const row=document.createElement('div');row.className='exercise-row';row.innerHTML=`<label>训练动作<select data-exercise>${exerciseOptions()}</select></label><label>重量 kg<input data-weight type="number" min="0" max="1000" step="0.5" value="${weight??EXERCISE_DEFAULT_WEIGHT[name]??20}"></label><label>组数<input data-sets type="number" min="1" max="20" step="1" value="${sets}"></label><label>每组次数<input data-reps type="number" min="1" max="100" step="1" value="${reps}"></label><div class="exercise-row-output"><span>容量</span><strong data-row-volume>0 kg</strong></div><button class="exercise-remove" type="button" aria-label="删除动作">×</button>`;row.querySelector('[data-exercise]').value=name;row.addEventListener('input',updateTrainingVolume);row.querySelector('[data-exercise]').onchange=e=>{const input=row.querySelector('[data-weight]');if(input.value===''||+input.value===0)input.value=EXERCISE_DEFAULT_WEIGHT[e.currentTarget.value]??20;updateTrainingVolume();};row.querySelector('.exercise-remove').onclick=()=>{row.remove();updateTrainingVolume();};$('#exerciseRows').appendChild(row);updateTrainingVolume();}
function resetWorkoutBuilder(){const rows=$('#exerciseRows');rows.innerHTML='';addExerciseRow();}
function workoutExercises(){return $$('.exercise-row',$('#exerciseRows')).map(row=>{const name=row.querySelector('[data-exercise]').value,weight=+row.querySelector('[data-weight]').value||0,sets=+row.querySelector('[data-sets]').value||0,reps=+row.querySelector('[data-reps]').value||0;return {name,weight,sets,reps,volume:weight*sets*reps};}).filter(x=>x.name&&x.sets>0&&x.reps>0);}
function renderTraining(){
  const ws=thisWeekWorkouts(),totalSets=ws.reduce((sum,x)=>sum+(Array.isArray(x.exercises)?x.exercises.reduce((a,item)=>a+(+item.sets||0),0):0),0),volume=ws.reduce((a,x)=>a+(+x.volume||0),0),calories=ws.reduce((a,x)=>a+workoutCalories(x),0),averageRpe=ws.length?ws.reduce((a,x)=>a+(+x.rpe||0),0)/ws.length:0,records=strengthWorkouts().slice().sort((a,b)=>b.date.localeCompare(a.date));
  $('#trainingStats').innerHTML=[['训练次数',ws.length],['总组数',totalSets],['训练容量',`${fmt(volume)} kg`],['预计消耗',`${fmt(calories)} kcal`]].map(x=>`<div><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');
  $('#trainingStatus').textContent=ws.length?`平均 RPE ${fmt(averageRpe,1)}`:'尚未记录';
  $('#workoutTable').innerHTML=records.length?records.map(x=>{const structured=Array.isArray(x.exercises)&&x.exercises.length,details=structured?x.exercises.map(item=>`${escapeHTML(item.name)} ${fmt(+item.weight,1)}kg · ${item.sets}×${item.reps}`).join('；'):escapeHTML(x.note||'未填写动作明细');return `<tr><td>${x.date}</td><td><b>力量训练</b><small class="workout-exercise-summary">${details}${x.note&&structured?`<br>${escapeHTML(x.note)}`:''}</small></td><td class="workout-volume">${x.volume?`${fmt(+x.volume)} kg`:'—'}</td><td><span class="estimated-badge">${structured?'估算':'历史'}</span>${fmt(workoutCalories(x))} kcal</td><td>RPE ${x.rpe}</td><td><button class="delete-btn" data-del-workout="${x.id}">×</button></td></tr>`;}).join(''):'<tr><td colspan="6"><div class="empty-state">还没有力量训练记录</div></td></tr>';
  $$('[data-del-workout]').forEach(b=>b.onclick=async()=>{const record=state.workouts.find(x=>x.id===b.dataset.delWorkout);if(!record)return;try{await removeRecord('workout',record);}catch(error){toast(error.message);}});
}
const EXPENSE_COLORS={'餐饮':'#db7444','交通':'#477c91','购物':'#73556f','住房':'#a8824b','运动健康':'#1f6b52','娱乐':'#d3a55d','学习':'#65866f','人情往来':'#9d6b53','其他':'#858980'};
function renderExpenses(){
  const month=$('#expenseMonth')?.value||currentMonth();
  const entries=state.expenses.filter(x=>x.date?.startsWith(month)).sort((a,b)=>b.date.localeCompare(a.date)||(b.createdAt||'').localeCompare(a.createdAt||''));
  const todayEntries=state.expenses.filter(x=>x.date===TODAY),todayTotal=todayEntries.reduce((a,x)=>a+(+x.amount||0),0);
  const monthTotal=entries.reduce((a,x)=>a+(+x.amount||0),0);
  const [year,monthNumber]=month.split('-').map(Number),daysInMonth=new Date(year,monthNumber,0).getDate();
  const budget=+state.settings.monthlyBudgets[month]||0,remaining=budget-monthTotal;
  $('#expenseSummary').innerHTML=[['今日已记录支出',todayEntries.length?money(todayTotal):'—'],['本月已记录支出',entries.length?money(monthTotal):'—'],['预算余额',budget&&entries.length?money(remaining):budget?'尚无记录':'尚未设置'],['有记录日均支出',entries.length?money(monthTotal/new Set(entries.map(x=>x.date)).size):'—']].map(x=>`<div><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');
  $('#monthlyBudget').value=budget||'';
  const ratio=budget?monthTotal/budget*100:0;
  $('#budgetPercent').textContent=budget&&entries.length?`${fmt(ratio,1)}%`:'—';
  $('#budgetCaption').textContent=budget?(entries.length?`已记录 ${money(monthTotal)} / ${money(budget)}${remaining<0?` · 超支 ${money(Math.abs(remaining))}`:` · 账面剩余 ${money(remaining)}`}`:'本月尚无支出记录，无法据此判断实际预算余额'):'为这个月设置预算后，可实时查看使用进度。';
  $('#budgetBar').style.width=`${Math.min(100,Math.max(0,ratio))}%`;
  $('#budgetBar').classList.toggle('over',ratio>100);
  const byCategory=entries.reduce((all,x)=>{all[x.category]=(all[x.category]||0)+(+x.amount||0);return all;},{});
  const categories=Object.entries(byCategory).sort((a,b)=>b[1]-a[1]);
  $('#expenseCategories').innerHTML=categories.length?categories.map(([name,value])=>`<div class="category-row"><span class="category-dot" style="background:${EXPENSE_COLORS[name]||EXPENSE_COLORS['其他']}"></span><div><strong>${escapeHTML(name)}</strong><small>${monthTotal?fmt(value/monthTotal*100,1):0}%</small></div><b>${money(value)}</b></div>`).join(''):'<div class="empty-state">这个月还没有支出记录</div>';
  $('#expenseCount').textContent=`${entries.length} 笔`;
  $('#expenseTable').innerHTML=entries.length?entries.map(x=>`<tr><td>${x.date}</td><td><span class="category-dot" style="background:${EXPENSE_COLORS[x.category]||EXPENSE_COLORS['其他']}"></span>${escapeHTML(x.category)}</td><td><b>${escapeHTML(x.title)}</b>${x.note?`<br><small>${escapeHTML(x.note)}</small>`:''}</td><td>${escapeHTML(x.payment)}</td><td class="expense-amount">${money(x.amount)}</td><td><button class="delete-btn" data-del-expense="${x.id}" aria-label="删除">×</button></td></tr>`).join(''):'<tr><td colspan="6"><div class="empty-state">还没有花销记录，记下第一笔吧。</div></td></tr>';
  $$('[data-del-expense]').forEach(b=>b.onclick=async()=>{const record=state.expenses.find(x=>x.id===b.dataset.delExpense);if(!record)return;try{await removeRecord('expense',record);}catch(error){toast(error.message);}});
  const daily=Array(daysInMonth).fill(null);entries.forEach(x=>{const day=+x.date.slice(8,10);if(day)daily[day-1]=(daily[day-1]??0)+(+x.amount||0);});
  chart('expenseTrendChart',{type:'bar',data:{labels:daily.map((_,i)=>`${i+1}日`),datasets:[{data:daily,backgroundColor:'#db7444',borderRadius:4}]},options:baseOptions()});
}
async function refreshAgentStatus(){
  if(!currentUser)return;
  try{const [result,toolResult]=await Promise.all([api('/api/agent/status'),api('/api/agent/tools')]);$('#agentStatus').textContent=result.configured?'已连接':'尚未配置';$('#agentModel').textContent=result.configured?result.model:'DeepSeek 未配置';$('#agentTools').innerHTML=toolResult.tools.map(tool=>`<div class="tool-card"><strong>${escapeHTML(tool.name)}</strong><small>${escapeHTML(tool.description)}</small></div>`).join('');}
  catch{$('#agentStatus').textContent='连接异常';$('#agentModel').textContent='本地服务异常';}
}
const AGENT_CATEGORY_LABELS={nutrition:'饮食营养',training:'力量训练',expense:'日常花销',general:'综合建议',health:'健康问题'};
const AGENT_URGENCY_LABELS={seek_care:'建议就医评估',emergency:'请立即寻求急救'};
function renderAgentContent(message){
  const response=message.response;
  if(!response||!AGENT_CATEGORY_LABELS[response.category])return escapeHTML(message.content);
  const evidence=Array.isArray(response.evidence)?response.evidence:[],actions=Array.isArray(response.actions)?response.actions:[],recordIds=Array.isArray(response.recordIds)?response.recordIds:[],toolTrace=Array.isArray(message.toolTrace)?message.toolTrace:[];
  return `<div class="agent-structured"><span class="agent-category">${AGENT_CATEGORY_LABELS[response.category]}</span>${AGENT_URGENCY_LABELS[response.urgency]?`<span class="agent-urgency ${response.urgency}">${AGENT_URGENCY_LABELS[response.urgency]}</span>`:''}<p>${escapeHTML(response.answer)}</p>${evidence.length?`<div class="agent-detail"><strong>依据</strong><ul>${evidence.map(x=>`<li>${escapeHTML(x)}</li>`).join('')}</ul></div>`:''}${recordIds.length?`<small class="agent-uncertainty">证据记录 ID：${recordIds.map(escapeHTML).join('、')}</small>`:''}${toolTrace.length?`<small class="agent-uncertainty">已调用只读工具：${toolTrace.map(x=>escapeHTML(x.tool)).join('、')}</small>`:''}${actions.length?`<div class="agent-detail"><strong>建议</strong><ul>${actions.map(x=>`<li>${escapeHTML(x)}</li>`).join('')}</ul></div>`:''}${response.uncertainty?`<small class="agent-uncertainty">${escapeHTML(response.uncertainty)}</small>`:''}</div>`;
}
function renderAgent(checkStatus=false){
  const messages=state.chat?.messages||[],el=$('#agentMessages');
  el.innerHTML=messages.length?messages.map(x=>`<div class="agent-message ${x.role==='user'?'user':'assistant'}"><div class="agent-bubble">${x.role==='user'?escapeHTML(x.content):renderAgentContent(x)}</div></div>`).join(''):'<div class="agent-empty"><span class="agent-orb">✦</span><h3>从你的数据出发</h3><p>可以问我今天怎么吃、是否适合训练，或者这个月的花销有哪些可以调整。</p></div>';
  if(agentBusy)el.insertAdjacentHTML('beforeend','<div class="agent-message pending"><div class="agent-bubble">正在结合你的数据思考…</div></div>');
  el.scrollTop=el.scrollHeight;$('#agentPrompts').classList.toggle('hidden',messages.length>0);$('#agentForm button').disabled=agentBusy;
  if(checkStatus)refreshAgentStatus();
}
async function sendAgentMessage(content){
  const text=String(content||'').trim();if(!text||agentBusy)return;
  state.chat.messages.push({role:'user',content:text,createdAt:new Date().toISOString()});state.chat.messages=state.chat.messages.slice(-50);agentBusy=true;renderAgent();
  try{
    clearTimeout(syncTimer);await syncNow(snapshotState());
    const history=state.chat.messages.filter(x=>!x.error).slice(-12).map(({role,content})=>({role,content}));
    const result=await api('/api/chat',{method:'POST',body:JSON.stringify({messages:history})});
    $('#agentModel').textContent=result.model||'DeepSeek';
    state.chat.messages.push({role:'assistant',content:result.message,response:result.response,toolTrace:result.toolTrace||[],createdAt:new Date().toISOString()});state.chat.messages=state.chat.messages.slice(-50);save();
  }catch(error){toast(error.message);state.chat.messages.push({role:'assistant',content:`暂时无法回答：${error.message}`,error:true,createdAt:new Date().toISOString()});save();}
  finally{agentBusy=false;renderAgent(true);}
}
async function lookupFoodNutrition(name,amount,description=''){return (await api('/api/tools/food-nutrition',{method:'POST',body:JSON.stringify({name,amount,description})})).result;}
function chart(id,config){if(charts[id])charts[id].destroy();const c=$('#'+id);if(!c)return;charts[id]=new Chart(c,config);}
function baseOptions(){return {responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{grid:{display:false},ticks:{color:'#858980',font:{size:9}}},y:{grid:{color:'#eeeee9'},ticks:{color:'#858980',font:{size:9}}}}};}
function renderWeekChart(){const ds=days(7),serverRows=recordSummaries.seven?.food?.daily,foodBy=ds.map(d=>serverRows?serverRows.find(row=>row.day===d)?.kcal??null:state.foods.some(x=>x.date===d)?sums(state.foods.filter(x=>x.date===d)).kcal:null),target=targets()?.calories||null;chart('weekChart',{type:'bar',data:{labels:ds.map(d=>d.slice(5)),datasets:[{label:'已记录摄入',data:foodBy,backgroundColor:'#db7444',borderRadius:5},{label:'每日建议',type:'line',data:ds.map(()=>target),borderColor:'#1f6b52',borderWidth:2,pointRadius:0,tension:0}]},options:{...baseOptions(),plugins:{legend:{display:true,labels:{boxWidth:8,boxHeight:8,font:{size:9}}}}}});}
function renderAll(){renderProfile();renderToday();renderFood();renderTraining();renderExpenses();renderAgent();}

function fillProfileForm(){const form=$('#profileForm'),p=state.profile||{};form.reset();Object.entries(p).forEach(([k,v])=>{const el=$(`#profileForm [name=${k}]`);if(el)el.value=v;});if(!p.stepGoal)form.elements.stepGoal.value=8000;const username=currentUser?.username||'—',displayName=p.name||username;$('#currentUsername').textContent=username;$('#profileModalAvatar').textContent=displayName.slice(0,1).toUpperCase();previewTargets();}
function previewTargets(){const f=new FormData($('#profileForm'));const p=Object.fromEntries(f.entries());if(!p.weight||!p.height||!p.age){$('#calcPreview').innerHTML='填写年龄、身高和体重后生成估算。';return;}const t=targets(p);$('#calcPreview').innerHTML=[['基础代谢',`${t.bmr} kcal`],['每日营养建议',`${t.calories} kcal`],['每日蛋白质',`${t.protein} g`]].map(x=>`<div><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');}
function foodLookupInput(){const form=$('#foodForm');return {name:form.elements.foodName.value.trim(),amount:+form.elements.amount.value,unit:form.elements.unit.value,description:form.elements.description.value.trim()};}
function foodLookupSignature(input=foodLookupInput()){return JSON.stringify(input);}
function updateFoodPreview(){foodLookupRequestId++;pendingFoodLookup=null;$('#foodSaveButton').disabled=true;$('#foodLookupButton').disabled=false;$('#foodLookupButton').textContent='查询营养';const input=foodLookupInput(),known=FOOD_DB.some(food=>food.name===input.name);$('#foodPreview').textContent=known?'已选知识库食物。点击“查询营养”确认份量和来源后保存。':'输入食物名称与克数后查询：先查知识库，未命中才使用 AI 估算。';}
function openFood(){$('#foodRecordDate').textContent=selectedFoodDate===TODAY?'今天':selectedFoodDate;updateFoodPreview();openModal('foodModal');}
function showFoodLookupResult(){if(!pendingFoodLookup)return;const {result,items}=pendingFoodLookup,source=result.source==='knowledge'?'知识库计算':'AI 估算',totals=['kcal','carb','protein','fat'].map(key=>items.reduce((sum,item)=>sum+(+item[key]||0),0));$('#foodPreview').innerHTML=`<strong>${items.length} 项 · ${fmt(totals[0],1)} kcal</strong><br>碳水 ${fmt(totals[1],1)}g · 蛋白质 ${fmt(totals[2],1)}g · 脂肪 ${fmt(totals[3],1)}g<br><small>${source}${result.source==='knowledge'?'':'，请以实际份量或包装营养表为准'}${result.note?` · ${escapeHTML(result.note)}`:''}</small>${items.length>1?`<br><small>${items.map(item=>escapeHTML(item.name)).join('、')}</small>`:''}`;}
async function queryFoodForForm(){const form=$('#foodForm');if(!form.reportValidity())return;const input=foodLookupInput(),known=FOOD_DB.find(food=>food.name===input.name);let grams=input.amount;if(input.unit==='serving'){if(!known||!Number(known.grams)){toast('自定义食物请填写克数；只有知识库中标明每份重量的食物可按份查询');return;}grams=input.amount*Number(known.grams);}if(!Number.isFinite(grams)||grams<=0||grams>5000){toast('食用重量需在 0–5000g 之间');return;}const requestId=++foodLookupRequestId,button=$('#foodLookupButton');button.disabled=true;button.textContent='查询中…';$('#foodSaveButton').disabled=true;$('#foodPreview').textContent='正在查询本地知识库；未命中时可能需要等待 AI 估算…';try{const result=await lookupFoodNutrition(input.name,grams,input.description);if(requestId!==foodLookupRequestId)return;const items=result.items?.length?result.items:[result];pendingFoodLookup={signature:foodLookupSignature(input),result,items:[...items]};showFoodLookupResult();$('#foodSaveButton').disabled=false;}catch(error){if(requestId!==foodLookupRequestId)return;pendingFoodLookup=null;$('#foodPreview').textContent=`查询失败：${error.message}`;toast(error.message);}finally{if(requestId===foodLookupRequestId){button.disabled=false;button.textContent='重新查询营养';}}}
function calcRM(){const w=+$('#rmWeight').value,r=+$('#rmReps').value;$('#rmResult').textContent=w&&r<37?`${(w/(1.0278-.0278*r)).toFixed(1)} kg`:'—';}

function setupForms(){
  $('#dateLabel').textContent=dateText();$('#foodSuggestions').innerHTML=FOOD_DB.map(f=>`<option value="${escapeHTML(f.name)}"></option>`).join('');
  $('#foodDate').value=selectedFoodDate;$('#foodDate').max=TODAY;
  $('#expenseMonth').value=currentMonth();
  $('#openProfile').onclick=()=>{fillProfileForm();openModal('profileModal');};
  $('#profileForm').addEventListener('input',previewTargets);$('#profileForm').onsubmit=async e=>{e.preventDefault();state.profile=Object.fromEntries(new FormData(e.currentTarget).entries());analyticsRequestId++;analyticsReports={seven:null,thirty:null};cacheState();clearTimeout(syncTimer);closeModal('profileModal');renderAll();try{await syncNow(snapshotState());await refreshRecordSummaries();toast('个人资料与每日目标已更新');}catch(error){toast(`个人资料保存失败：${error.message}`);}};
  $('#addFoodTop').onclick=()=>openFood();$('#foodToday').onclick=()=>{selectedFoodDate=TODAY;renderFood();};$('#foodDate').onchange=e=>{selectedFoodDate=e.currentTarget.value||TODAY;renderFood();};
  ['foodName','amount','unit','description'].forEach(name=>{$(`#foodForm [name=${name}]`).addEventListener('input',updateFoodPreview);});
  $('#foodLookupButton').onclick=queryFoodForForm;
  $('#foodForm').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,pending=pendingFoodLookup,input=foodLookupInput();if(!pending||pending.signature!==foodLookupSignature(input)){toast('食物信息已变化，请重新查询营养');updateFoodPreview();return;}const button=$('#foodSaveButton'),meal=form.elements.meal.value;button.disabled=true;let saved=0;try{while(pending.items.length){const item=pending.items[0],source=item.source||pending.result.source,basis=source==='knowledge'?'knowledge_base':'ai_estimate',sourceRef=item.sourceRef||pending.result.sourceRef||null,confidence=item.confidence||pending.result.confidence||null;await createRecord('food',{id:crypto.randomUUID(),date:selectedFoodDate,time:selectedFoodDate===TODAY?new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}):'',meal,name:item.name,amount:+item.amount||input.amount,unit:'g',kcal:+item.kcal,carb:+item.carb,protein:+item.protein,fat:+item.fat,nutritionSource:source,nutritionRef:sourceRef,nutritionConfidence:confidence,nutritionNote:item.note||pending.result.note,inputName:input.name,meta:recordMeta('food',basis,sourceRef,confidence)},{refresh:false});pending.items.shift();saved++;}renderAll();refreshRecordSummaries();form.reset();pendingFoodLookup=null;closeModal('foodModal');toast(`${saved} 项饮食已保存 · ${pending.result.source==='knowledge'?'知识库计算':'含 AI 估算'}`);}catch(error){if(saved){renderAll();refreshRecordSummaries();}showFoodLookupResult();toast(`已保存 ${saved} 项，剩余 ${pending.items.length} 项未保存：${error.message}`);}finally{button.disabled=!pendingFoodLookup?.items.length;}};
  const workoutForm=$('#workoutForm');
  $('#addWorkoutTop').onclick=()=>{workoutForm.reset();resetWorkoutBuilder();updateTrainingEstimate();openModal('workoutModal');};
  $('#workoutRpe').onchange=updateTrainingEstimate;
  $('#addExercise').onclick=()=>addExerciseRow('深蹲');
  $$('[data-exercise-preset]').forEach(button=>button.onclick=()=>addExerciseRow(button.dataset.exercisePreset));
  workoutForm.onsubmit=async e=>{e.preventDefault();const x=Object.fromEntries(new FormData(e.currentTarget).entries()),exercises=workoutExercises();if(!exercises.length){toast('请至少添加一个力量训练动作');return;}const estimate=estimateStrengthCalories(exercises,+x.rpe);const record={id:crypto.randomUUID(),date:dateKey(new Date()),type:'力量训练',note:x.note||'',rpe:+x.rpe,exercises,volume:estimate.volume,calories:estimate.calories,calorieSource:'estimated',meta:recordMeta('workout','formula_estimate')};try{await createRecord('workout',record);closeModal('workoutModal');toast(`训练已保存 · ${exercises.length} 个动作 · 预计消耗 ${estimate.calories} kcal`);}catch(error){toast(error.message);}};
  resetWorkoutBuilder();updateTrainingEstimate();
  $('#addExpenseTop').onclick=()=>{$('#expenseForm [name=date]').value=TODAY;openModal('expenseModal');};
  const expenseFoodToggle=$('#expenseForm [name=syncFood]'),expenseFoodName=$('#expenseForm [name=foodName]');
  expenseFoodToggle.onchange=()=>{const enabled=expenseFoodToggle.checked;$('#expenseFoodFields').classList.toggle('hidden',!enabled);expenseFoodName.required=enabled;$('#expenseForm [name=foodWeight]').required=enabled;if(enabled){$('#expenseForm [name=category]').value='餐饮';if(!expenseFoodName.value)expenseFoodName.value=$('#expenseForm [name=title]').value;}};
  $('#expenseForm [name=title]').oninput=e=>{if(expenseFoodToggle.checked&&!expenseFoodName.value)expenseFoodName.value=e.currentTarget.value;};
  $('#expenseForm').onsubmit=async e=>{
    e.preventDefault();const form=e.currentTarget,button=form.querySelector('[type=submit]'),x=Object.fromEntries(new FormData(form).entries()),expenseId=crypto.randomUUID();
    const {syncFood,foodName,foodWeight,foodMeal,...expenseData}=x,createdAt=new Date().toISOString(),expenseRecord={id:expenseId,createdAt,...expenseData,amount:+expenseData.amount,meta:recordMeta('expense','user_reported',null,null,'manual',createdAt)};button.disabled=true;
    let foodResult=null,foodError=null;
    try{await createRecord('expense',expenseRecord);}catch(error){button.disabled=false;toast(error.message);return;}
    if(syncFood){button.textContent='正在读取备注并查询营养…';try{foodResult=await lookupFoodNutrition(foodName,+foodWeight,expenseData.note||'');const items=foodResult.items?.length?foodResult.items:[foodResult];for(const item of items){const source=item.source||foodResult.source,basis=source==='knowledge'?'knowledge_base':source,sourceRef=item.sourceRef||foodResult.sourceRef||null,confidence=item.confidence||foodResult.confidence||null;await createRecord('food',{id:crypto.randomUUID(),linkedExpenseId:expenseId,date:expenseData.date,time:expenseData.date===TODAY?new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}):'',meal:foodMeal,name:item.name,amount:+item.amount||+foodWeight,unit:'g',kcal:+item.kcal,carb:+item.carb,protein:+item.protein,fat:+item.fat,nutritionSource:source,nutritionRef:sourceRef,nutritionConfidence:confidence,nutritionNote:item.note||foodResult.note,meta:recordMeta('food',basis,sourceRef,confidence,'expense_sync')});}}catch(error){foodError=error;}}
    closeModal('expenseModal');form.reset();form.elements.date.value=TODAY;$('#expenseFoodFields').classList.add('hidden');expenseFoodName.required=false;form.elements.foodWeight.required=false;button.disabled=false;button.textContent='保存支出';renderAll();
    if(foodResult){const count=foodResult.items?.length||1;toast(`支出与 ${count} 项饮食已记录 · ${foodResult.source==='knowledge'?'知识库计算':'AI 估算'}`);}else if(foodError)toast(`支出已保存，饮食同步失败：${foodError.message}`);else toast('这笔花销已记下');
  };
  $('#expenseMonth').onchange=renderExpenses;
  $('#budgetForm').onsubmit=e=>{e.preventDefault();const month=$('#expenseMonth').value||currentMonth(),amount=+$('#monthlyBudget').value||0;if(amount>0)state.settings.monthlyBudgets[month]=amount;else delete state.settings.monthlyBudgets[month];save();renderExpenses();toast(amount>0?'月度预算已更新':'月度预算已清除');};
  $('#agentForm').onsubmit=e=>{e.preventDefault();const input=e.currentTarget.elements.message,text=input.value;input.value='';sendAgentMessage(text);};
  $('#agentForm textarea').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();e.currentTarget.form.requestSubmit();}};
  $$('[data-agent-prompt]').forEach(b=>b.onclick=()=>sendAgentMessage(b.dataset.agentPrompt));
  $('#clearAgentChat').onclick=()=>{if(!state.chat.messages.length||!confirm('确定清空当前用户的 Agent 对话记录吗？'))return;state.chat.messages=[];save();renderAgent();toast('对话已清空');};
  $('#foodToolForm').onsubmit=async e=>{e.preventDefault();const form=e.currentTarget,resultEl=$('#foodToolResult'),button=form.querySelector('button'),values=Object.fromEntries(new FormData(form).entries());button.disabled=true;button.textContent='查询中';resultEl.classList.remove('hidden');resultEl.textContent='正在查询本地知识库…';try{const result=await lookupFoodNutrition(values.name,+values.amount);resultEl.innerHTML=`<strong>${escapeHTML(result.name)} · ${fmt(result.kcal,1)} kcal</strong><br>碳水 ${fmt(result.carb,1)}g · 蛋白质 ${fmt(result.protein,1)}g · 脂肪 ${fmt(result.fat,1)}g<br>${result.source==='knowledge'?'知识库计算':'AI 估算，请以包装营养表为准'}`;}catch(error){resultEl.textContent=error.message;}finally{button.disabled=false;button.textContent='查询';}};
  $$('[data-close]').forEach(b=>b.onclick=()=>closeModal(b.dataset.close));$$('.modal-backdrop').forEach(x=>x.addEventListener('click',e=>{if(e.target===x)x.classList.add('hidden');}));
  $('#rmWeight').oninput=calcRM;$('#rmReps').oninput=calcRM;calcRM();
  $('#exportData').onclick=()=>{const blob=new Blob([JSON.stringify({profile:state.profile,foods:state.foods,workouts:state.workouts,expenses:state.expenses,chat:state.chat,settings:state.settings},null,2)],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`boji-health-${TODAY}.json`;a.click();URL.revokeObjectURL(a.href);};
}

setupAuth();setupNav();setupForms();renderAll();restoreSession();
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden'&&currentUser){clearTimeout(syncTimer);syncNow().catch(()=>{});}});
