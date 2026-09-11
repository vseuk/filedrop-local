'use strict';
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
let queue = [];
let files = [];   // последний список файлов с компьютера

function fmt(b){ const u=['B','KB','MB','GB']; let i=0;
    while(b>=1024&&i<u.length-1){b/=1024;i++;} return b.toFixed(i?1:0)+' '+u[i]; }

function toast(sel, text, ok){
    const m=$(sel); m.textContent=text; m.className='msg show '+(ok?'ok':'err');
    setTimeout(()=>m.classList.remove('show'),4000);
}

// --- вкладки ---
$$('.tab').forEach(t=>t.onclick=()=>{
    $$('.tab').forEach(x=>x.classList.remove('active'));
    t.classList.add('active');
    $('#tab-send').classList.toggle('hidden', t.dataset.tab!=='send');
    $('#tab-receive').classList.toggle('hidden', t.dataset.tab!=='receive');
    $('#tab-notes').classList.toggle('hidden', t.dataset.tab!=='notes');
    if(t.dataset.tab==='receive') loadFiles();
    if(t.dataset.tab==='notes') loadNotes();
});

// --- отправка ---
const dz=$('#dropZone'), fi=$('#fileInput');
dz.onclick=()=>fi.click();
dz.addEventListener('keydown',e=>{
    if(e.key==='Enter'||e.key===' '){ e.preventDefault(); fi.click(); }
});
dz.addEventListener('dragover',e=>{e.preventDefault();dz.classList.add('over');});
dz.addEventListener('dragleave',()=>dz.classList.remove('over'));
dz.addEventListener('drop',e=>{e.preventDefault();dz.classList.remove('over');addFiles(e.dataTransfer.files);});
fi.onchange=()=>addFiles(fi.files);

function addFiles(list){ for(const f of list) queue.push(f); renderQueue(); }
function renderQueue(){
    const q=$('#queue'); q.innerHTML='';
    const meta=$('#queueMeta');
    if(queue.length){
        const total=queue.reduce((sum,file)=>sum+file.size,0);
        meta.textContent=`Выбрано: ${queue.length} · ${fmt(total)}`;
        meta.classList.remove('hidden');
    } else meta.classList.add('hidden');
    queue.forEach((f,i)=>{
        const li=document.createElement('li');
        li.innerHTML=`<span class="q-name"></span><span class="q-size">${fmt(f.size)}</span>
            <button class="q-del" data-i="${i}">✕</button>`;
        li.querySelector('.q-name').textContent=f.name;
        q.appendChild(li);
    });
    q.querySelectorAll('.q-del').forEach(b=>b.onclick=()=>{queue.splice(+b.dataset.i,1);renderQueue();});
    $('#sendBtn').disabled=queue.length===0;
    $('#sendBtn').textContent='Отправить';
}

$('#sendBtn').onclick=()=>{
    if(!queue.length) return;
    const fd=new FormData();
    queue.forEach(f=>fd.append('file',f));
    const btn=$('#sendBtn'); btn.disabled=true;
    const xhr=new XMLHttpRequest();
    xhr.upload.onprogress=e=>{ if(e.lengthComputable){
        btn.textContent='Загрузка '+Math.round(e.loaded/e.total*100)+'%'; }};
    xhr.onload=()=>{
        if(xhr.status===200){ toast('#msg','Отправлено: '+queue.length+' файл(ов)',true); queue=[]; renderQueue(); }
        else if(xhr.status===401){ showLock(); }
        else toast('#msg','Ошибка загрузки',false);
        btn.disabled=false; btn.textContent='Отправить';
    };
    xhr.onerror=()=>{toast('#msg','Нет соединения',false);btn.disabled=false;btn.textContent='Отправить';};
    xhr.open('POST','/upload'); xhr.send(fd);
};

document.addEventListener('paste',e=>{
    for(const it of e.clipboardData.items){
        if(it.type.indexOf('image')!==-1){
            const blob=it.getAsFile();
            const f=new File([blob],'screenshot_'+Date.now()+'.png',{type:blob.type});
            queue.push(f); renderQueue(); toast('#msg','Скриншот добавлен',true);
        }
    }
});

// --- список файлов ---
const IMG=/\.(png|jpe?g|gif|webp|bmp|svg)$/i;
const TXT=/\.(txt|md|log|json|csv|xml|ini|yml|yaml|js|py|css|html?)$/i;

function loadFiles(){
    fetch('/api/files').then(r=>{
        if(r.status===401){showLock();return null;} return r.json();
    }).then(d=>{
        if(!d) return;
        files=d.files; renderFiles();
    });
}

function renderFiles(){
    const ul=$('#fileList'); ul.innerHTML='';
    $('#selAll').checked=false;
    if(!files.length){ ul.innerHTML='<li class="empty">Пока нет файлов</li>'; updateBulk(); return; }
    files.forEach(f=>{
        const li=document.createElement('li');
        const canPreview=IMG.test(f.name)||TXT.test(f.name);
        li.innerHTML=`<input type="checkbox" class="f-check">
            <div class="f-meta"><span class="f-name"></span><span class="f-size">${f.human}</span></div>
            <div class="f-act">
                ${canPreview?'<button class="pv">Просмотр</button>':''}
                <a class="dl" href="/dl?name=${encodeURIComponent(f.name)}" download>Скачать</a>
            </div>`;
        li.querySelector('.f-name').textContent=f.name;
        li.querySelector('.f-check').onchange=updateBulk;
        const meta=li.querySelector('.f-meta');
        if(canPreview) meta.onclick=()=>preview(f);
        const pv=li.querySelector('.pv'); if(pv) pv.onclick=()=>preview(f);
        ul.appendChild(li);
    });
    updateBulk();
}

function selectedNames(){
    const names=[];
    $$('#fileList li').forEach((li,i)=>{
        const c=li.querySelector('.f-check');
        if(c&&c.checked) names.push(files[i].name);
    });
    return names;
}

function updateBulk(){
    $('#bulkBar').classList.toggle('hidden', selectedNames().length===0);
}

$('#selAll').onchange=e=>{
    $$('#fileList .f-check').forEach(c=>c.checked=e.target.checked);
    updateBulk();
};
$('#refreshBtn').onclick=loadFiles;

$('#zipBtn').onclick=()=>{
    const names=selectedNames(); if(!names.length) return;
    const qs=names.map(n=>'name='+encodeURIComponent(n)).join('&');
    window.location='/dl-zip?'+qs;
};

$('#delBtn').onclick=async ()=>{
    const names=selectedNames(); if(!names.length) return;
    if(!confirm('Удалить выбранные файлы ('+names.length+')?')) return;
    for(const n of names){
        await fetch('/api/delete',{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({name:n})});
    }
    loadFiles();
};

// --- просмотр ---
function preview(f){
    const body=$('#viewerBody'); body.innerHTML='';
    const url='/preview?name='+encodeURIComponent(f.name);
    if(IMG.test(f.name)){
        const img=document.createElement('img'); img.src=url; body.appendChild(img);
    } else {
        const pre=document.createElement('pre'); pre.textContent='Загрузка…'; body.appendChild(pre);
        fetch(url).then(r=>r.text()).then(t=>pre.textContent=t.slice(0,200000));
    }
    $('#viewer').classList.remove('hidden');
}
$('#viewerClose').onclick=()=>$('#viewer').classList.add('hidden');
$('#viewer').onclick=e=>{ if(e.target.id==='viewer') $('#viewer').classList.add('hidden'); };

// --- заметки ---
function loadNotes(){
    fetch('/api/notes').then(r=>{ if(r.status===401){showLock();return null;} return r.json(); })
        .then(d=>{ if(d) $('#notesArea').value=d.text||''; });
}
$('#notesSave').onclick=()=>{
    fetch('/api/notes',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({text:$('#notesArea').value})})
    .then(r=>{ if(r.status===401){showLock();return;} toast('#notesMsg','Сохранено',true); });
};

// --- блокировка / PIN ---
function showLock(){ $('#lock').classList.remove('hidden'); }
$('#pinBtn').onclick=()=>{
    fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({pin:$('#pinInput').value})})
    .then(r=>r.json().then(d=>({status:r.status,d})))
    .then(({status,d})=>{
        if(status===200&&d.ok){ $('#lock').classList.add('hidden'); $('#pinError').textContent=''; }
        else if(status===429) $('#pinError').textContent='Слишком много попыток. Подождите.';
        else $('#pinError').textContent='Неверный код';
    });
};
$('#pinInput').addEventListener('keydown',e=>{ if(e.key==='Enter') $('#pinBtn').click(); });

fetch('/api/auth-required').then(r=>r.json()).then(d=>{ if(d.required&&!d.authorized) showLock(); });
