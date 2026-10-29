/* 仅生成课件，不读取或修改应用业务代码。运行方式见使用说明.md。 */
const fs = require('fs');
const path = require('path');
const { pathToFileURL } = require('url');
const root = __dirname;
const assets = path.join(root, 'assets');
fs.mkdirSync(assets, { recursive: true });
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const palettes = {
  blue: ['#eef3ff', '#cad6f6', '#304aa5'],
  teal: ['#eaf7f4', '#b4dfd4', '#146b59'],
  amber: ['#fff6e6', '#f1d49b', '#93601a'],
  gray: ['#f3f5f8', '#d8dfe8', '#4a596f']
};
function box(x,y,w,h,title,lines=[],color='blue') {
  const p=palettes[color];
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="12" fill="${p[0]}" stroke="${p[1]}"/><text x="${x+w/2}" y="${y+30}" text-anchor="middle" font-size="18" font-weight="700" fill="${p[2]}">${esc(title)}</text>${lines.map((t,i)=>`<text x="${x+w/2}" y="${y+57+i*24}" text-anchor="middle" font-size="14" fill="#46546b">${esc(t)}</text>`).join('')}`;
}
function arrow(x1,y1,x2,y2,label='') {
  return `<path d="M ${x1} ${y1} L ${x2} ${y2}" fill="none" stroke="#8b9bb5" stroke-width="2" marker-end="url(#a)"/>${label?`<text x="${(x1+x2)/2+10}" y="${(y1+y2)/2-7}" font-size="13" fill="#62718b">${esc(label)}</text>`:''}`;
}
function svg(name,title,subtitle,height,body) {
  fs.writeFileSync(path.join(assets,name+'.svg'),`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 ${height}" role="img" aria-labelledby="title desc"><title id="title">${esc(title)}</title><desc id="desc">${esc(subtitle)}</desc><defs><marker id="a" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="none" stroke="#8b9bb5"/></marker></defs><rect width="960" height="${height}" rx="18" fill="#fff"/><g font-family="Microsoft YaHei, PingFang SC, sans-serif"><text x="36" y="43" fill="#1b2b47" font-size="24" font-weight="700">${esc(title)}</text><text x="36" y="71" fill="#718097" font-size="14">${esc(subtitle)}</text>${body}</g></svg>`,'utf8');
}

svg('业务流程图','从告警到可核验的处置结果','三 Agent 组织调查；业务服务控制复核、审批、执行与归档。',650,
  box(36,105,260,92,'01 · 告警接入',['原始记录留存 / 去重与事件关联'])+
  box(350,105,260,92,'02 · 初筛 Agent',['资产上下文 / 优先级 / 授权线索'])+
  box(664,105,260,92,'03 · 调查 Agent',['六类工具 / 证据链 / 缺口识别'])+
  arrow(296,151,350,151)+arrow(610,151,664,151)+arrow(794,197,794,247)+
  box(664,247,260,92,'04 · 响应 Agent',['结构化报告 / 候选动作'],'teal')+
  box(350,247,260,92,'05 · 人工复核与审批',['角色权限 / 当前版本 / 动作绑定'],'amber')+
  box(36,247,260,92,'06 · 受控执行',['幂等记录 / 最小权限 / 外部请求'],'teal')+
  arrow(664,293,610,293)+arrow(350,293,296,293)+arrow(166,339,166,389)+
  box(36,389,260,92,'07 · 结果回查',['实际生效 / 业务影响 / 失败处理'],'teal')+
  box(350,389,574,92,'08 · 事件归档或重新调查',['关联工单、证据、报告、审批与操作审计；旧版本保留'],'gray')+
  arrow(296,435,350,435)+box(36,527,888,83,'证据不足或执行结果未知 → 人工接管',['不能把工具超时当作没有异常，也不能把已受理当作处置成功。'],'amber'));

let arch='';
const rows=[
  ['用户入口',['安全分析员 / 审批人','企业 SSO / 事件工作台'],'blue'],
  ['API 与业务控制',['FastAPI / RBAC / 版本校验','事件状态机 / 审批绑定 / 审计'],'blue'],
  ['Agent 编排',['LangGraph：初筛 → 调查 → 响应','Flash：提取与初筛；Plus：综合研判'],'teal'],
  ['工具与执行边界',['六类只读查询工具 / 证据标准化','独立受控执行适配器 / 人工审批'],'teal'],
  ['任务与持久化',['MySQL：事件、任务、审批、outbox','Redis + Celery：投递与消费；证据对象存储'],'gray'],
  ['企业系统与运行保障',['日志 / 资产 / 授权 / 身份 / 业务审计 API','指标、追踪、告警、备份恢复与密钥管理'],'gray']
];
rows.forEach((r,i)=>{const y=105+i*120;arch+=box(36,y,888,92,r[0],r[1],r[2]);if(i<rows.length-1)arch+=arrow(480,y+92,480,y+119);});
svg('系统架构图','企业生产参考架构','应用服务与 Worker 可独立部署；这是课件设计，不代表当前原型已完成生产迁移。',850,arch);

svg('模型路由图','双模型路由与人工复核','模型输出是辅助判断，权限与业务副作用始终由服务端控制。',695,
  box(260,105,440,92,'输入预处理与确定性规则',['字段校验 / 脱敏 / 工具白名单 / 预算'],'gray')+
  arrow(480,197,480,235)+
  box(260,235,440,92,'qwen3.5-flash',['字段提取、分类辅助、轻量摘要'],'blue')+
  arrow(480,327,480,365)+
  box(260,365,440,92,'qwen3.5-plus',['复杂证据综合、冲突解释、报告与建议'],'teal')+
  arrow(480,457,480,495)+
  box(260,495,440,92,'Schema 校验 + 规则核验 + 人工复核',['结论引用证据；高风险动作进入审批'],'amber')+
  `<path d="M700 281 H850 V541 H706" fill="none" stroke="#8b9bb5" stroke-width="2" marker-end="url(#a)"/><text x="770" y="385" text-anchor="middle" font-size="14" fill="#62718b">简单任务</text><text x="770" y="410" text-anchor="middle" font-size="14" fill="#62718b">可跳过 Plus</text>`+
  `<text x="480" y="640" text-anchor="middle" font-size="15" fill="#62718b">超时、无效结构或关键证据缺失 → 有界重试 / 证据清单 / 人工接管</text>`);

svg('审批执行图','把审批授权绑定到一次明确的操作','调查版本变化、参数变化或审批过期，必须重新确认授权。',695,
  box(36,105,420,108,'候选动作',['目标 + 范围 + 时长 + 调查版本','生成规范化动作摘要 / hash'],'blue')+
  box(504,105,420,108,'人工审批',['审批角色 + 有效期 + 动作摘要','禁止自扩权限或沿用旧版本审批'],'amber')+
  arrow(456,159,504,159)+arrow(714,213,714,261)+
  box(504,261,420,108,'执行前再次校验',['权限 / 当前版本 / hash / 过期时间','登记执行意图与幂等键'],'blue')+
  box(36,261,420,108,'调用外部受控接口',['最小权限凭证 / 外部请求 ID','外部调用不放入长数据库事务'],'teal')+
  arrow(504,315,456,315)+arrow(246,369,246,417)+
  box(36,417,420,108,'目标系统回查',['确认实际生效与影响范围','成功、失败和未知分别记录'],'teal')+
  box(504,417,420,108,'结果未知时',['优先查询外部状态 / 人工核验','没有可靠幂等保证时禁止盲目重试'],'amber')+
  arrow(456,471,504,471)+
  box(36,571,888,83,'全链路审计',['事件 → 调查版本 → 审批 → 执行 → 回查；每一步均可关联和追溯。'],'gray'));

svg('状态记忆图','运行上下文、业务事实与知识分开管理','“记住对话”不能替代业务持久化；恢复执行必须核对外部副作用。',575,
  box(36,105,420,140,'运行上下文',['LangGraph state / 当前节点 / 临时摘要','只引用必要证据，控制上下文体积','必要时使用已验证的持久化 checkpointer'],'blue')+
  box(504,105,420,140,'业务事实 · MySQL',['事件 / 调查 / 任务 / 版本 / 权限','审批 / 执行 / outbox / 审计','数据库事实是恢复与一致性判断依据'],'teal')+
  box(36,293,420,140,'知识与配置',['手册 / 关联规则 / 提示词 / Schema','保留版本，支持回放与变更追溯','未验证需要前，不强制引入向量库'],'gray')+
  box(504,293,420,140,'证据与归档',['原始日志引用 / 查询范围 / 时间戳','脱敏快照 / 报告 / 历史调查版本','访问控制与保留周期按制度配置'],'gray')+
  arrow(456,175,504,175)+arrow(246,245,246,293)+arrow(714,245,714,293)+
  `<text x="480" y="495" text-anchor="middle" font-size="16" fill="#304aa5">服务端范围隔离 → 证据最小化 → 版本可追溯 → 按政策保留</text>`+
  `<text x="480" y="532" text-anchor="middle" font-size="14" fill="#62718b">MySQL 业务持久化不等于 LangGraph 自动续跑，也不提供外部动作的 exactly-once。</text>`);

svg('交付流程图','从可演示流程到可运维交付','阶段安排为建议方案；成果验收需要真实统计台账和业务确认。',510,
  box(36,105,260,110,'01 · 契约与基线',['场景 / 接口 / 权限','样本 / 指标定义 / 数据范围'],'blue')+
  box(350,105,260,110,'02 · 只读调查闭环',['工具与证据模型','三 Agent / 报告 / 人工复核'],'blue')+
  box(664,105,260,110,'03 · 受控响应加固',['审批 / 幂等 / 回查','持久化 / 恢复 / 审计'],'teal')+
  arrow(296,160,350,160)+arrow(610,160,664,160)+arrow(794,215,794,275)+
  box(664,275,260,110,'04 · 回放与故障验证',['质量抽检 / 容量验证','权限、超时与中断恢复'],'teal')+
  box(350,275,260,110,'05 · 灰度与验收',['按场景逐步开放','核对成果分子、分母和范围'],'amber')+
  box(36,275,260,110,'06 · 运维与复盘',['监控 / 手册 / 培训','恢复演练 / 规则与模型评估'],'gray')+
  arrow(664,330,610,330)+arrow(350,330,296,330)+
  `<text x="480" y="449" text-anchor="middle" font-size="16" fill="#62718b">应用回滚与业务动作回退分别设计；发现风险时可先关闭动作执行入口。</text>`);

async function main(){
  let marked;
  try { marked = (await import('marked')).marked; }
  catch { const p=process.env.MARKED_MODULE_PATH || 'C:/Users/86187/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/marked/lib/marked.esm.js';marked=(await import(pathToFileURL(p).href)).marked; }
  const md=fs.readFileSync(path.join(root,'项目介绍课件.md'),'utf8');
  const headings=[];
  let chapter=0;
  const renderer = new marked.Renderer();
  renderer.heading=function(token){
    const level=token.depth;
    const content=this.parser.parseInline(token.tokens);
    if(level===2) chapter++;
    const id=level===1?'overview':`section-${headings.length+1}`;
    headings.push({id,level,text:token.text,chapter});
    return `<h${level} id="${id}">${content}</h${level}>\n`;
  };
  const rendered=marked.parse(md,{renderer,gfm:true});
  const content=rendered.replace(/<table>[\s\S]*?<\/table>/g,s=>`<div class="table-scroll" tabindex="0">${s}</div>`);
  const nav=headings.filter(h=>h.level===2).map((h,i)=>`<a href="#${h.id}" data-chapter="${h.chapter}"><span>${i<7?String(i+1).padStart(2,'0'):'A'}</span>${esc(h.text.replace(/^[一二三四五六七附录]+、/,''))}</a>`).join('');
  const toc=headings.filter(h=>h.level===3).map(h=>`<a href="#${h.id}" data-chapter="${h.chapter}">${esc(h.text)}</a>`).join('');
  const html=`<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="广州臻亿电子商务有限公司：电商安全运营智能研判与响应协同平台项目介绍课件。"><title>电商安全运营 Agent · 广州臻亿项目课件</title><link rel="stylesheet" href="assets/course.css"></head><body>
<a class="skip" href="#content">跳到正文</a>
<header class="topbar"><button id="menu-toggle" class="icon-button" aria-label="打开章节目录" aria-expanded="false">☰</button><a class="brand" href="#overview"><span class="brand-icon">A</span><span>电商安全运营 Agent<small>广州臻亿电子商务有限公司 · 项目介绍课件</small></span></a><div class="actions"><a href="项目介绍课件.md" download>Markdown 源稿</a><button id="print">打印 / PDF</button></div></header>
<div class="progress"><div id="reading-progress"></div></div>
<div class="layout"><aside class="sidebar" id="sidebar"><div class="side-label">项目课件 · 七章体系</div><a href="#overview" class="overview-link">概览</a><nav aria-label="章节目录">${nav}</nav><div class="side-note">2026.04 — 2026.10<br>Agent 开发工程师 · 6 人团队<br><span>离线阅读 · 本地 SVG 图示</span></div><a class="source-link" href="使用说明.md">使用说明</a></aside>
<main id="content"><div class="cover"><span class="eyebrow">PROJECT CASEBOOK / 2026</span><p class="cover-title">让安全调查形成<br><em>可追溯的处置闭环</em></p><p class="cover-sub">订单接口 · 运营账号 · 敏感数据导出</p><div class="cover-tags"><span>LangGraph</span><span>qwen3.5-plus / flash</span><span>人审与受控执行</span></div><div class="metrics"><div><strong>60 <i>→</i> 15<small>分钟</small></strong><span>高优先级告警人工初筛</span></div><div><strong>≈30<small>%</small></strong><span>聚合后待处理事件量减少</span></div><div><strong>80<small>%</small></strong><span>事件包含自动化处置步骤</span></div></div><p class="cover-note">成果数据由用户提供；统计周期与样本待补充。企业架构与容量按设计方案展开。</p></div><article>${content}</article><footer>广州臻亿电子商务有限公司 · 项目介绍课件<br>内容源：项目介绍课件.md · 参考资料核对：2026年10月17日</footer></main>
<aside class="toc"><label for="toc-search">目录检索</label><input id="toc-search" type="search" placeholder="查找章节 / 关键词" autocomplete="off"><div id="toc-label">本章导航</div><nav aria-label="小节目录">${toc}</nav><p id="search-empty" hidden>没有匹配标题，可按 Ctrl+F 搜索正文。</p></aside></div><button id="top" aria-label="返回顶部">↑</button><script src="assets/course.js"></script></body></html>`;
  fs.writeFileSync(path.join(root,'项目介绍课件.html'),html,'utf8');
  fs.writeFileSync(path.join(root,'目录索引.json'),JSON.stringify(headings,null,2),'utf8');
  console.log(JSON.stringify({html:'项目介绍课件.html',chapters:headings.filter(h=>h.level===2).length,sections:headings.filter(h=>h.level===3).length,svg:6,markdownBytes:Buffer.byteLength(md)},null,2));
}
main().catch(e=>{console.error(e);process.exit(1)});
