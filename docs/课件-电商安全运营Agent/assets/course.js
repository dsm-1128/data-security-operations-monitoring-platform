(() => {
  const menu=document.getElementById('menu-toggle');
  const sidebar=document.getElementById('sidebar');
  const links=[...document.querySelectorAll('.sidebar nav a')];
  const toc=[...document.querySelectorAll('.toc nav a')];
  const sections=[...document.querySelectorAll('article h2, article h3')];
  const search=document.getElementById('toc-search');
  let chapter='1';
  function filter(){
    const query=search.value.trim().toLowerCase();
    let count=0;
    for(const a of toc){const show=query?a.textContent.toLowerCase().includes(query):a.dataset.chapter===chapter;a.hidden=!show;if(show)count++;}
    document.getElementById('toc-label').textContent=query?'匹配的小节':'本章导航';
    document.getElementById('search-empty').hidden=count>0;
  }
  function update(){
    let active=sections[0];
    for(const h of sections){if(h.getBoundingClientRect().top<=150)active=h;else break;}
    if(active){const link=[...links,...toc].find(a=>a.hash==='#'+active.id);if(link)chapter=link.dataset.chapter;links.forEach(a=>a.classList.toggle('active',a.dataset.chapter===chapter));toc.forEach(a=>a.classList.toggle('active',a.hash==='#'+active.id));}
    filter();
    const range=document.documentElement.scrollHeight-window.innerHeight;
    document.getElementById('reading-progress').style.width=(range>0?Math.min(100,window.scrollY/range*100):0)+'%';
  }
  menu.addEventListener('click',()=>{const open=sidebar.classList.toggle('open');menu.setAttribute('aria-expanded',String(open));menu.setAttribute('aria-label',open?'关闭章节目录':'打开章节目录');});
  sidebar.addEventListener('click',e=>{if(e.target.closest('a')){sidebar.classList.remove('open');menu.setAttribute('aria-expanded','false');}});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){sidebar.classList.remove('open');menu.setAttribute('aria-expanded','false');}});
  document.getElementById('print').addEventListener('click',()=>window.print());
  document.getElementById('top').addEventListener('click',()=>window.scrollTo({top:0,behavior:'smooth'}));
  search.addEventListener('input',filter);
  let queued=false;
  window.addEventListener('scroll',()=>{if(!queued){queued=true;requestAnimationFrame(()=>{queued=false;update();});}},{passive:true});
  window.addEventListener('resize',update);
  update();
})();
