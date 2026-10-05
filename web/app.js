const token=document.querySelector('meta[name="local-token"]').content;
const $=id=>document.getElementById(id);
let lastLog=0;

async function api(path,options={}){const response=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Local-Token':token,...options.headers}});const data=await response.json();if(!response.ok)throw new Error(data.error||'操作失败');return data}
function payload(){return{room:$('room').value.trim(),segment:Number($('segment').value),stream:$('stream').value,seconds:Number($('seconds').value),interval:Number($('interval').value),watch:$('watch').checked}}
function message(text,error=false){$('message').textContent=text;$('message').classList.toggle('error',error)}
function setBusy(busy){$('checkButton').disabled=busy;$('startButton').disabled=busy}
function renderState(data){$('statusPill').classList.toggle('running',data.active);$('statusPill').querySelector('strong').textContent=data.active?'录音中':'空闲';$('startButton').disabled=data.active;$('stopButton').disabled=!data.active;$('taskStatus').textContent=data.active?'运行中':data.returncode===null?'未运行':`已结束（${data.returncode}）`;$('startedAt').textContent=data.started_at?new Date(data.started_at).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}):'—';$('pid').textContent=data.pid||'—';if(data.logs?.length){$('emptyLog').classList.add('hidden');for(const item of data.logs){lastLog=Math.max(lastLog,item.id);const row=document.createElement('div');row.className='log-line';const time=document.createElement('span');time.className='log-time';time.textContent=item.time;const text=document.createElement('span');text.className='log-text';text.textContent=item.text;row.append(time,text);$('console').append(row)}$('console').scrollTop=$('console').scrollHeight}}
async function refresh(){try{renderState(await api(`/api/state?after=${lastLog}`))}catch(error){message(error.message,true)}}

$('checkButton').addEventListener('click',async()=>{setBusy(true);message('正在检查直播间…');try{const data=await api('/api/check',{method:'POST',body:JSON.stringify({room:$('room').value.trim()})});const room=data.room;$('roomCard').classList.remove('hidden');$('roomState').textContent=room['开播']?'正在直播':'未开播';$('roomState').classList.toggle('offline',!room['开播']);$('roomTitle').textContent=room['标题']||'未命名直播间';$('creator').textContent=room['主播']||'—';$('streams').textContent=(room['可用流']||[]).map(value=>value.replace('_pull_url','').toUpperCase()).join(' / ')||'无';message('状态已更新')}catch(error){message(error.message,true)}finally{setBusy(false)}});
$('recordForm').addEventListener('submit',async event=>{event.preventDefault();setBusy(true);message('正在启动…');try{const data=await api('/api/start',{method:'POST',body:JSON.stringify(payload())});renderState(data);message('录音任务已启动')}catch(error){message(error.message,true)}finally{setBusy(false)}});
$('stopButton').addEventListener('click',async()=>{try{await api('/api/stop',{method:'POST',body:'{}'});message('正在安全停止并保存最后一段…')}catch(error){message(error.message,true)}});
$('watch').addEventListener('change',()=>{if($('watch').checked)$('seconds').value='0';$('seconds').disabled=$('watch').checked});
$('clearButton').addEventListener('click',()=>{$('console').replaceChildren($('emptyLog'));$('emptyLog').classList.remove('hidden')});
refresh();setInterval(refresh,1500);
