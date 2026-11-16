import {useEffect,useState,type FormEvent} from "react";
import {
  Activity,ArrowDown,ArrowUp,BookOpen,Check,ChevronRight,Clock3,FileText,LayoutDashboard,
  Minus,Monitor, Moon,PanelLeftClose,PanelLeftOpen,Plus,RefreshCw,
  Save,Settings,ShieldAlert,Sun,Trash2, X, Square, GraduationCap, Pencil
} from "lucide-react";
import {applyPlan,blankCourse,createPlan,deletePlan,isDesktop,listPlans,preflight,saveCourses,saveStrategy,snapshot,windowAction,signedLogin,logout,restoreSavedSession,forgetSavedSession,recheckSession,manualCatalog,sessionStatus,startReadOnlyMonitor,stopReadOnlyMonitor,monitorStatus,type MonitorSnapshot,type OfficialCatalog,type LoginReply,type SessionInfo,type Course,type Preflight,type Snapshot,type Strategy} from "./bridge";

type Page = "home" | "courses" | "execution" | "logs";
const navigation = [
  {id:"home" as const,label:"工作台",icon:LayoutDashboard},
  {id:"courses" as const,label:"课程计划",icon:BookOpen},
  {id:"execution" as const,label:"运行中心",icon:Activity},
  {id:"logs" as const,label:"日志记录",icon:FileText}
];
type EventEntry = {at:string;message:string};
const courseFields: {key:keyof Course;label:string}[] = [
  {key:"key",label:"志愿标识"},{key:"course_id",label:"课程代码"},
  {key:"class_id",label:"教学班"},{key:"course_name",label:"课程名称"},
  {key:"teacher",label:"任课教师"},{key:"group",label:"备选组"},
  {key:"time_slots",label:"上课节次"}
];
function presentCourse(course:Course) {
  return course.course_name.trim() || course.course_id.trim() || "未填写课程名称";
}
function isPlaceholder(course:Course) {
  return ["FILL_FROM_OFFICIAL_PAGE","SECOND_OPTION_ID"].includes(course.course_id);
}
function shortError(error:unknown) {
  const msg = String(error);
  if(msg.includes("LOCAL_CONFIG_ValueError")) return "配置未通过校验，请核对名称、课程数据或查询参数";
  if(msg.includes("LOCAL_CONFIG_OSError")) return "本机文件操作失败，请检查权限及磁盘空间";
  return msg.length > 140 ? "操作失败；请在日志中查看本机诊断" : msg;
}
export default function App() {
  const [page,setPage]=useState<Page>("home");
  const [collapsed,setCollapsed]=useState(()=>localStorage.getItem("kingo-lite.nav.collapsed")==="true");
  const [theme,setTheme]=useState<"light"|"dark">(()=>localStorage.getItem("kingo-lite.theme")==="light"?"light":"dark");
  const [settingsOpen,setSettingsOpen]=useState(false);
  const [snapshotData,setSnapshotData]=useState<Snapshot|null>(null);
  const [courses,setCourses]=useState<Course[]>([]);
  const [dirty,setDirty]=useState(false);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState("");
  const [editError,setEditError]=useState("");
  const [events,setEvents]=useState<EventEntry[]>([]);
  const [editing,setEditing]=useState<number|null>(null);
  const [loginOpen,setLoginOpen]=useState(false);
  const [username,setUsername]=useState("");
  const [password,setPassword]=useState("");
  const [rememberLogin,setRememberLogin]=useState(false);
  const [session,setSession]=useState<SessionInfo|null>(null);
  const [loginResult,setLoginResult]=useState<LoginReply|null>(null);
  const [catalog,setCatalog]=useState<OfficialCatalog|null>(null);
  const [schoolBusy,setSchoolBusy]=useState(false);
  const [monitor,setMonitor]=useState<MonitorSnapshot|null>(null);
  const [plansOpen,setPlansOpen]=useState(false);
  const [planName,setPlanName]=useState("");
  const [plans,setPlans]=useState<string[]>([]);
  const [planBusy,setPlanBusy]=useState(false);
  const [dialogError,setDialogError]=useState("");
  const [readiness,setReadiness]=useState<Preflight|null>(null);
  const [checking,setChecking]=useState(false);
  const [strategyOpen,setStrategyOpen]=useState(false);
  const [strategyDraft,setStrategyDraft]=useState<Strategy|null>(null);
  const [draft,setDraft]=useState<Course>(blankCourse());

  function log(message:string) {
    setEvents(current=>[{at:new Date().toLocaleTimeString(),message},...current].slice(0,60));
  }
  async function reload() {
    setLoading(true);setError("");
    try {
      const data=await snapshot();
      setSnapshotData(data);setCourses(data.courses);setDirty(false);
      log("已读取本机课程计划；未连接教务系统");
    } catch (cause) {
      setSnapshotData(null);setCourses([]);setDirty(false);
      setError(shortError(cause));log("本机课程配置读取失败或尚未处于桌面运行环境");
    } finally {setLoading(false);}
  }
  useEffect(()=>{void reload();},[]);
  useEffect(()=>{
    if(!isDesktop())return;
    void sessionStatus().then(setSession).catch(()=>setSession(null));
    void monitorStatus().then(setMonitor).catch(()=>setMonitor(null));
  },[]);
  useEffect(()=>{
    if(!monitor || (monitor.phase!=="running"&&monitor.phase!=="stopping"))return;
    let active=true;
    const handle=window.setInterval(()=>{
      void monitorStatus().then(value=>{if(active)setMonitor(value);}).catch(()=>{
        if(active)setMonitor(null);
      });
    },2200);
    return ()=>{active=false;window.clearInterval(handle);};
  },[monitor?.phase]);
  useEffect(()=>{localStorage.setItem("kingo-lite.theme",theme);},[theme]);
  useEffect(()=>{localStorage.setItem("kingo-lite.nav.collapsed",String(collapsed));},[collapsed]);
  function openEditor(index:number) {
    if(monitor?.active){setError("只读监测运行中，请先停止，再编辑课程");return;}
    setEditError("");
    setEditing(index);
    setDraft(index < 0 ? blankCourse() : {...courses[index]});
  }
  function updateDraft(field:keyof Course,value:string) {
    setDraft(current=>({...current,[field]:value}));
  }
  function acceptEdit(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if(monitor?.active){setEditError("请先停止只读监测");return;}
    if(!draft.key.trim() || ![draft.course_id,draft.course_name,draft.teacher].some(x=>x.trim())) {
      setEditError("请填写志愿标识，以及课程代码、课程名称或任课教师中的至少一项");return;
    }
    if(courses.some((course,index)=>index!==editing && course.key===draft.key.trim())) {
      setEditError("志愿标识不能重复");return;
    }
    const value={...draft,key:draft.key.trim()};
    setCourses(current=>editing===-1?[...current,value]:current.map((course,index)=>index===editing?value:course));
    setDirty(true);setEditing(null);setEditError("");
  }
  async function persist() {
    if(!snapshotData || loading) return;
    if(monitor?.active){setError("请先停止监测，再保存课程配置");return;}
    setLoading(true);setError("");
    try {
      const data=await saveCourses(courses);
      setCourses(data.courses);setSnapshotData(data);setDirty(false);
      log("本机课程计划已保存；不代表学校选课成功");
    }catch(cause){setError(shortError(cause));log("本机课程计划保存失败");}
    finally{setLoading(false);}
  }
  function removeCourse(index:number) {
    if(monitor?.active){setError("监测运行中不能修改课程");return;}
    if(!window.confirm("仅从本机计划中移除此课程？不会访问教务网站。"))return;
    setCourses(current=>current.filter((_,i)=>i!==index));setDirty(true);
  }
  function moveCourse(index:number,direction:-1|1) {
    if(monitor?.active){setError("监测运行中不能调整志愿顺序");return;}
    const target=index+direction;
    if(target<0||target>=courses.length)return;
    setCourses(current=>{
      const next=[...current];
      [next[index],next[target]]=[next[target],next[index]];
      return next;
    });
    setDirty(true);
  }
  async function showPlans() {
    if(monitor?.active){setError("请先停止只读监测，再管理本机方案");return;}
    if(dirty){setError("请先保存课程修改，再管理课程方案");return;}
    try {
      setPlanBusy(true);setError("");setDialogError("");
      const result=await listPlans();
      setPlans(result.plans);setPlansOpen(true);
    }catch(e){setError(shortError(e));}
    finally{setPlanBusy(false);}
  }
  async function handlePlan(action:"save"|"apply"|"delete",name:string) {
    const trimmed=name.trim();
    if(!trimmed || planBusy || !snapshotData) return;
    if(action==="save" && plans.includes(trimmed) && !window.confirm("同名课程方案已存在，是否覆盖本机备份？"))return;
    if(action==="apply" && !window.confirm("切换方案将改变当前课程计划；旧计划会另外保存恢复点。确定继续？"))return;
    if(action==="delete" && !window.confirm("确定删除本机课程方案？"))return;
    setPlanBusy(true);setError("");setDialogError("");
    try {
      if(action==="save") {
        const result=await createPlan(trimmed);setPlans(result.plans);
        log("已保存本机课程方案："+trimmed);
      } else if(action==="delete") {
        const result=await deletePlan(trimmed);setPlans(result.plans);
        log("已删除本机课程方案："+trimmed);
      } else {
        const result=await applyPlan(trimmed);
        setSnapshotData(result);setCourses(result.courses);setDirty(false);
        setPlans(result.plans);setPlansOpen(false);setReadiness(null);
        log("已切换本机方案："+trimmed+"（旧方案已生成恢复点）");
      }
    }catch(e){setDialogError(shortError(e));}
    finally{setPlanBusy(false);}
  }
  async function checkReadiness() {
    if(dirty){setError("先保存课程修改，才能对当前本机配置进行检查");return;}
    setChecking(true);setError("");
    try {
      const result=await preflight();setReadiness(result);
      log("完成本地运行条件检查：阻止项 "+result.blockers+"，提示 "+result.warnings);
    }catch(e){setError(shortError(e));}
    finally{setChecking(false);}
  }
  function editStrategy() {
    if(monitor?.active){setError("请先停止只读监测，再修改查询设置");return;}
    if(!snapshotData||dirty){setError("请先保存课程草稿并刷新配置");return;}
    setStrategyDraft({
      semester:snapshotData.semester,
      mode:snapshotData.mode==="burst"?"burst":"monitor",
      target_time:snapshotData.target_time,
      interval_low:snapshotData.interval_seconds[0],
      interval_high:snapshotData.interval_seconds[1],
      max_cycles:snapshotData.max_cycles,
      max_duration_minutes:snapshotData.max_duration_minutes
    });
    setStrategyOpen(true);setError("");setDialogError("");
  }
  async function persistStrategy(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if(!strategyDraft||loading) return;
    setLoading(true);setError("");setDialogError("");
    try {
      const result=await saveStrategy(strategyDraft);
      setSnapshotData(result);setCourses(result.courses);setStrategyOpen(false);setReadiness(null);
      log("已保存本机只读查询设置；未开启学校查询");
    }catch(e){setDialogError(shortError(e));}
    finally{setLoading(false);}
  }
  async function startMonitor() {
    if(schoolBusy || monitor?.active || session?.status!=="verified")return;
    if(dirty){setError("请先保存本机计划，再启动只读监测");return;}
    setSchoolBusy(true);setError("");
    try{
      const result=await startReadOnlyMonitor();
      setMonitor(result);
      if(result.code==="started"){log("已启动原版只读监测；学校查询仍受请求预算限制");}
      else if(result.code==="preflight_rejected"){setError("只读监测未启动：本机协议、课程或学校请求预算未通过核验");}
      else{setError("只读监测未启动："+(result.code||"unknown"));}
    }catch(e){setError(shortError(e));log("只读监测启动失败");}
    finally{setSchoolBusy(false);}
  }
  async function stopMonitor() {
    if(schoolBusy)return;
    setSchoolBusy(true);setError("");
    try{
      const result=await stopReadOnlyMonitor();setMonitor(result);
      log("已请求停止本次只读监测");
    }catch(e){setError(shortError(e));}
    finally{setSchoolBusy(false);}
  }
  async function loginUser(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if(schoolBusy || !username.trim() || !password)return;
    setSchoolBusy(true);setError("");setLoginResult(null);
    try {
      const result=await signedLogin(username.trim(),password,rememberLogin);
      setSession(result.session);setLoginResult(result);
      setCatalog(null);
      log(result.session.verified_at_login?"本人账号身份回读通过（未查询课程）":"EXE 登录未通过："+result.code);
      if(result.session.verified_at_login){
        setLoginOpen(false);setUsername("");setRememberLogin(false);
        if(result.remember_error)setError("本次登录成功，但 Windows 加密保存失败，下次启动仍需重新授权");
      }
    }catch(e){setSession(null);setCatalog(null);setError(shortError(e));log("EXE 登录服务请求失败（无凭据被保存）");}
    finally{setPassword("");setSchoolBusy(false);}
  }
  async function restoreRememberedLogin() {
    if(schoolBusy||monitor?.active||!session?.saved_session_available)return;
    setSchoolBusy(true);setError("");setLoginResult(null);
    try{
      const result=await restoreSavedSession();setSession(result.session);setLoginResult(result);
      setCatalog(null);
      log(result.code==="restored"?"已重新核验并恢复保存的教务会话":"免密码会话未恢复："+result.code);
    }catch(e){setError(shortError(e));log("免密码恢复未完成");}
    finally{setSchoolBusy(false);}
  }
  async function checkCurrentSession() {
    if(schoolBusy||monitor?.active||session?.status!=="verified")return;
    setSchoolBusy(true);setError("");
    try {
      const result=await recheckSession();
      setSession(result.session);setLoginResult(result);
      if(!result.session.verified_at_login)setCatalog(null);
      log(result.code==="identity_verified"?"学校身份回读成功，当前会话仍有效":"学校身份回读未成功："+result.code);
    }catch(e){setError(shortError(e));setCatalog(null);log("学校身份回读未完成，不认定会话有效");}
    finally{setSchoolBusy(false);}
  }
  async function forgetRememberedLogin() {
    if(schoolBusy||monitor?.active)return;
    if(!window.confirm("清除本机加密会话并退出当前登录？之后需要重新获取官方授权。"))return;
    setSchoolBusy(true);setError("");
    try{
      const result=await forgetSavedSession();
      setSession(result);setCatalog(null);setLoginResult(null);
      setPassword("");setUsername("");log("已清除本机保存的加密会话");
    }catch(e){setError(shortError(e));}
    finally{setSchoolBusy(false);}
  }
  async function endSession() {
    if(schoolBusy)return;
    setSchoolBusy(true);setError("");
    try{
      const result=await logout();setSession(result);setCatalog(null);setLoginResult(null);
      setUsername("");setPassword("");setRememberLogin(false);
      log("已退出并删除本机加密会话；未向教务系统发送退出请求");
    }catch(e){setError(shortError(e));}
    finally{setSchoolBusy(false);}
  }
  async function loadCatalog() {
    if(schoolBusy || session?.status!=="verified")return;
    setSchoolBusy(true);setError("");
    try{
      const result=await manualCatalog();
      setCatalog(result);setSession(result.session);
      log(result.code==="catalog_read"?"S202 官网只读查询完成："+result.total_courses+" 条课程":"S202 官网查询未完成："+result.code);
    }catch(e){setSession(null);setError(shortError(e));setCatalog(null);log("S202 只读查询失败，没有模拟数据");}
    finally{setSchoolBusy(false);}
  }
  function go(next:Page) {setPage(next);setError("");}
  return (
    <div className={"app "+theme}>
      <header className="titlebar" data-tauri-drag-region>
        <div className="brand" data-tauri-drag-region><GraduationCap size={18}/><strong data-tauri-drag-region>KingoAssistant</strong><span data-tauri-drag-region>Lite</span></div>
        <div className="window-actions">
          <button title="主题切换" aria-label="主题切换" onClick={()=>setTheme(x=>x==="dark"?"light":"dark")}>{theme==="dark"?<Sun size={16}/>:<Moon size={16}/>}</button>
          <button title="设置" aria-label="设置" onClick={()=>setSettingsOpen(true)}><Settings size={16}/></button>
          <span className="window-divider" />
          <button title="最小化" aria-label="最小化" onClick={()=>void windowAction("minimize")}><Minus size={16}/></button>
          <button title="最大化或还原" aria-label="最大化或还原" onClick={()=>void windowAction("maximize")}><Square size={13}/></button>
          <button title="关闭" aria-label="关闭" className="close-window" onClick={()=>{if((monitor?.active||dirty)&&!window.confirm("关闭将停止当前监测，并丢弃未保存的界面修改。确定关闭？"))return;void windowAction("close");}}><X size={16}/></button>
        </div>
      </header>
      <div className="workspace">
        <aside className={"sidebar "+(collapsed?"collapsed":"")}>
          <div className="sidebar-header">
            {!collapsed&&<span>工作空间</span>}
            <button title={collapsed?"展开导航":"收起导航"} aria-label={collapsed?"展开导航":"收起导航"} onClick={()=>setCollapsed(x=>!x)}>
              {collapsed?<PanelLeftOpen size={17}/>:<PanelLeftClose size={17}/>}
            </button>
          </div>
          <nav aria-label="主导航">
            {navigation.map(item=><button key={item.id} aria-current={page===item.id?"page":undefined} title={item.label}
              className={"nav-item "+(page===item.id?"active":"")} onClick={()=>go(item.id)}>
              <item.icon size={18}/>{!collapsed&&<span>{item.label}</span>}
            </button>)}
          </nav>
          <div className="sidebar-bottom">
            <button className="nav-item" title="设置" onClick={()=>setSettingsOpen(true)}><Settings size={18}/>{!collapsed&&<span>设置</span>}</button>
            {!collapsed&&<small>Lightweight Windows UI</small>}
          </div>
        </aside>
        <main className="main-content">
          <div className="page-heading">
            <div>
              <span className="eyebrow">NCWU · 本机选课助手</span>
              <h1>{page==="home"?"选课工作台":page==="courses"?"课程计划":page==="execution"?"运行中心":"日志记录"}</h1>
              <p>{page==="home"?"准备课程与检查运行条件":page==="courses"?"编辑本机目标课程和备选志愿":page==="execution"?"查看各项能力的真实接入状态":"本次 Lite 界面会话的本机操作记录"}</p>
            </div>
            <button className="button subtle" disabled={loading||dirty||monitor?.active} onClick={()=>void reload()} title={dirty?"请先保存或放弃未保存修改":"重新读取本机配置"}>
              <RefreshCw size={15} className={loading?"spin":""}/>刷新
            </button>
          </div>
          {error&&<div role="alert" className="notice error"><ShieldAlert size={18}/><span>{error}</span><button aria-label="关闭提示" onClick={()=>setError("")}><X size={15}/></button></div>}
          {dirty&&<div className="notice warning"><span>本机课程计划有未保存的修改，尚未写入配置文件。</span><button className="button small" onClick={()=>void persist()} disabled={loading}>保存</button></div>}
          {page==="home"&&<>
            <section className="hero card">
              <div className="hero-copy">
                <span className="eyebrow">当前状态</span>
                <h2>选课准备</h2>
                <p>这是一套独立的轻量桌面界面。已读取的数据仅来自你的本机计划；不代表学校在线查询结果。</p>
              </div>
              <span className="status muted"><Clock3 size={14}/>未启动学校任务</span>
            </section>
            <div className="stats">
              <div className="card metric"><span>本机课程</span><strong>{snapshotData?courses.length:"—"}</strong><small>仅本地草稿</small></div>
              <div className="card metric"><span>账号登录</span><strong className="metric-word">{session?.status==="verified"?"已核验":"未登录"}</strong><small>{session?.status==="verified"?"本次登录已通过身份回读":"需在应用内授权"}</small></div>
              <div className="card metric"><span>在线课程</span><strong className="metric-word">{catalog?.code==="catalog_read"?catalog.total_courses:"未查询"}</strong><small>{catalog?.code==="catalog_read"?"最近一次手动读取；非已选结果":"无实时余量数据"}</small></div>
              <div className="card metric"><span>自动提交</span><strong className="metric-word">不可用</strong><small>真实提交适配器未验收</small></div>
            </div>
            <section className="card section">
              <div className="section-heading"><div><h3>教务登录</h3><p>支持账号密码登录，或使用 Windows 账户加密保存的有效学校会话免密码继续；不保存密码。</p></div></div>
              <div className="login-actions">
                <span className={"status "+(session?.status==="verified"?"good":"muted")}>{session?.status==="verified"?"本次身份已核验":"尚无已核验会话"}</span>
                {session?.status==="verified"?<button className="button" disabled={schoolBusy||monitor?.active} onClick={()=>void endSession()}>退出登录</button>:<>
                  {session?.saved_session_available&&<button className="button primary" disabled={schoolBusy||!isDesktop()} onClick={()=>void restoreRememberedLogin()}>{schoolBusy?"核验中…":"免密码继续"}</button>}
                  <button className={session?.saved_session_available?"button":"button primary"} disabled={schoolBusy||!isDesktop()} onClick={()=>{setLoginResult(null);setLoginOpen(true);}}>账号密码登录</button>
                  {session?.saved_session_available&&<button className="button subtle" disabled={schoolBusy} onClick={()=>void forgetRememberedLogin()}>清除已保存登录</button>}
                </>}
                {session?.status==="verified"&&<button className="button" disabled={schoolBusy||monitor?.active} onClick={()=>void checkCurrentSession()}>核验会话是否有效</button>}
                {session?.status==="verified"&&<button className="button primary" disabled={schoolBusy||monitor?.active} onClick={()=>{go("execution");void loadCatalog();}}>手动读取课程</button>}
              </div>
              {loginResult&&<div className={"notice "+(loginResult.session.verified_at_login?"warning":"error")} role="status">{loginResult.message}</div>}
              <div className="section-heading" style={{marginTop:18}}><div><h3>快捷入口</h3><p>只展示当前有明确功能的操作</p></div></div>
              <div className="quick-grid">
                <button className="quick" onClick={()=>go("courses")}><BookOpen size={22}/><strong>管理选课计划</strong><small>添加课程、调整志愿</small><ChevronRight size={16} className="quick-arrow"/></button>
                <button className="quick" onClick={()=>go("execution")}><Monitor size={22}/><strong>查看执行状态</strong><small>区分只读、调度与正式提交</small><ChevronRight size={16} className="quick-arrow"/></button>
              </div>
            </section>
          </>}
          {page==="courses"&&<>
            <div className="section-heading top-actions"><div><h3>目标课程</h3><p>仅保存到本机配置，最多 30 门；示例课程不会作为真实在线课程显示。</p></div><div className="button-cluster"><button className="button" disabled={!snapshotData||planBusy||monitor?.active} onClick={()=>void showPlans()}>管理方案</button><button className="button primary" disabled={!snapshotData||courses.length>=30||monitor?.active} onClick={()=>openEditor(-1)}><Plus size={16}/>添加课程</button></div></div>
            <section className="card table-wrap">
              <table><thead><tr><th>优先级</th><th>志愿</th><th>课程</th><th>教学班</th><th>备选组</th><th>状态</th><th>操作</th></tr></thead>
              <tbody>{courses.map((course,index)=><tr key={index}><td>{index+1}</td><td>{course.key}</td><td><strong>{presentCourse(course)}</strong><small>{course.course_id||"无课程代码"} · {course.teacher||"未填教师"}</small></td><td>{course.class_id||"—"}</td><td>{course.group||"—"}</td><td><span className="status muted">{isPlaceholder(course)?"示例 · 未连接":"未查询"}</span></td><td><div className="row-actions"><button aria-label={"上移"+presentCourse(course)} disabled={index===0||monitor?.active} onClick={()=>moveCourse(index,-1)}><ArrowUp size={15}/></button><button aria-label={"下移"+presentCourse(course)} disabled={index===courses.length-1||monitor?.active} onClick={()=>moveCourse(index,1)}><ArrowDown size={15}/></button><button aria-label={"编辑"+presentCourse(course)} disabled={monitor?.active} onClick={()=>openEditor(index)}><Pencil size={15}/></button><button aria-label={"删除"+presentCourse(course)} disabled={monitor?.active} onClick={()=>removeCourse(index)}><Trash2 size={15}/></button></div></td></tr>)}</tbody></table>
              {courses.length===0&&<div className="empty"><BookOpen size={26}/><strong>还没有配置课程</strong><span>使用右上角「添加课程」建立本机计划</span></div>}
            </section>
            <div className="course-footer"><small>学期：{snapshotData?.semester||"未读取"} · 当前界面不调用学校选课接口</small><button className="button primary" disabled={!dirty||loading||monitor?.active} onClick={()=>void persist()}><Save size={16}/>保存本机计划</button></div>
          </>}
          {page==="execution"&&<>
            <section className="card section"><div className="section-heading"><div><h3>本机运行前检查</h3><p>不访问教务系统，不证明账号有效或可选课程有余量。</p></div><button className="button" disabled={checking||!snapshotData} onClick={()=>void checkReadiness()}>{checking?"检查中…":"检查运行条件"}</button></div>
              {readiness?<><div className="notice warning">发现 {readiness.blockers} 项本机阻止条件，{readiness.warnings} 项建议核对；不代表学校实时验收。</div>
                {readiness.checks.map((check,index)=><div className="readiness-row" key={index}><span className={check.passed?"check-good":"check-warn"}>{check.passed?"通过":check.severity==="blocker"?"未通过":"提示"}</span><div><strong>{check.label}</strong>{!check.passed&&<small>{check.detail}</small>}</div></div>)}</>:<p>按需检查本机课程、节次和查询配置；结果不会发起任何网络请求。</p>}
            </section>
            <section className="card section"><h3>真实能力状态</h3>
              <div className="cap-row"><span>本地课程计划</span><span className={"status "+(snapshotData?"good":"muted")}>{snapshotData?"可读取":"不可用"}</span></div>
              <div className="cap-row"><span>官方账号会话</span><span className={"status "+(session?.status==="verified"?"good":"muted")}>{session?.status==="verified"?"本次登录身份已核验":"未登录"}</span></div>
              <div className="cap-row"><span>学校实时课程</span><span className={"status "+(catalog?.code==="catalog_read"?"good":"muted")}>{catalog?.code==="catalog_read"?"最近一次查询已返回":"未获得可展示数据"}</span></div>
              <div className="cap-row"><span>只读监测</span><span className={"status "+(monitor?.active?"good":"muted")}>{monitor?.active?"运行中":monitor?.phase==="stopped"?"已停止":monitor?.phase==="failed"?"执行失败":"未运行"}</span></div>
              <div className="cap-row"><span>自动提交</span><span className="status blocked">学校写入适配器缺失</span></div>
            </section>
            <section className="card section"><div className="section-heading"><div><h3>NCWU S202 课程目录</h3><p>仅在你点击时从官网读取一次。状态字段不是已选结果，也不包含已核验的剩余名额。</p></div>
                <button className="button primary" disabled={schoolBusy||session?.status!=="verified"||!isDesktop()||monitor?.active} onClick={()=>void loadCatalog()}>{schoolBusy?"读取中…":"手动读取官网目录"}</button></div>
                {catalog&&<div className={"notice "+(catalog.code==="catalog_read"?"warning":"error")}>{catalog.message}</div>}
                {catalog?.code==="catalog_read"&&<><div className="course-footer"><small>学校返回 {catalog.total_courses} 条；显示 {catalog.courses.length} 条。本次读取不会提交选课。</small></div>
                  <div className="table-wrap"><table><thead><tr><th>课程名称</th><th>课程代码</th><th>教学班</th><th>任课教师</th><th>选课状态</th></tr></thead>
                  <tbody>{catalog.courses.map((row,index)=><tr key={index}><td>{row.course_name||"—"}</td><td>{row.course_code}</td><td>{row.class_number}</td><td>{row.teacher||"—"}</td><td>{row.selection_status||"未提供"}</td></tr>)}</tbody></table></div>
                </>}
                {!catalog&&<p>尚无本次会话的官网查询结果。需先登录本人账号；Lite 不会自动轮询，也不会读取浏览器 Cookie。</p>}
            </section>
            <section className="card section"><h3>当前本机查询配置</h3>
              <p>模式：{snapshotData?.mode==="burst"?"定时单次查询":"常规只读监测"}　查询间隔：{snapshotData?.interval_seconds?.join("–")||"未读取"} 秒</p>
              <p>此页不会自行启动任务或打开浏览器。原有 Tk Windows 客户端仍是当前监测入口，直到完成有状态任务桥接与 Windows 验收。</p>
              <div className="monitor-actions">
                <button className="button primary" disabled={schoolBusy||monitor?.active||session?.status!=="verified"||dirty} onClick={()=>void startMonitor()}>启动只读监测</button>
                <button className="button" disabled={schoolBusy||!monitor?.active} onClick={()=>void stopMonitor()}>停止监测</button>
              </div>
              <div className="monitor-metrics">
                <span>运行阶段：<strong>{monitor?.phase||"未初始化"}</strong></span>
                <span>检查轮次：<strong>{monitor?.cycles??0}</strong></span>
                <span>最近请求：<strong>{monitor?.last_ms==null?"—":monitor.last_ms.toFixed(1)+" ms"}</strong></span>
              </div>
              {monitor?.message&&<p>{monitor.message}</p>}
              <p>仅迁移只读监测；学校选课提交依旧不可用。关闭 Lite 将终止进程内任务，当前版本不支持托盘驻留。</p>
            </section>
          </>}
          {page==="logs"&&<section className="card section"><div className="section-heading"><div><h3>本次界面操作</h3><p>仅用于 Lite 本机界面的临时记录，不是学校接口日志。</p></div></div>
            {events.length?events.map((event,index)=><div className="event-row" key={index}><span>{event.at}</span><strong>{event.message}</strong></div>):<div className="empty">暂无本次会话操作记录</div>}
          </section>}
          <footer className="app-footer"><span>{isDesktop()?"Windows 桌面运行环境":"浏览器静态预览 · 无本地权限"}</span><span>本机配置 ≠ 学校实时选课结果</span></footer>
        </main>
      </div>
      {settingsOpen&&<div className="overlay" onMouseDown={event=>{if(event.target===event.currentTarget)setSettingsOpen(false);}}>
        <section className="modal settings-modal" role="dialog" aria-modal="true" aria-label="设置">
          <div className="modal-heading"><div><span className="eyebrow">偏好与说明</span><h2>设置</h2></div><button aria-label="关闭设置" onClick={()=>setSettingsOpen(false)}><X size={19}/></button></div>
          <div className="settings-layout"><div className="settings-menu"><strong>通用设置</strong><small>外观与窗口</small><strong>关于</strong><small>版本与边界</small></div>
            <div className="settings-body"><h3>外观</h3><div className="setting-row"><div><strong>深色主题</strong><p>仅保存本机界面偏好</p></div><button className="button" onClick={()=>setTheme(x=>x==="dark"?"light":"dark")}>{theme==="dark"?"已启用":"切换深色"}</button></div>
            <div className="setting-row"><div><strong>侧边栏</strong><p>收起后只显示图标</p></div><button className="button" onClick={()=>setCollapsed(x=>!x)}>{collapsed?"展开":"收起"}</button></div><div className="setting-row"><div><strong>本机查询设置</strong><p>学期、查询模式、间隔及次数，不改变学校访问权限</p></div><button className="button" onClick={()=>{setSettingsOpen(false);editStrategy();}}>编辑</button></div>
            <h3>关于</h3><p>KingoAssistant Lite · 独立实现的轻量 UI。参考 LightC 的布局理念；不包含 LightC 原版源码、标识或受限资源。</p>
            <p>此版本正在迁移桌面界面。旧版已具备的真实业务逻辑不会被本界面替代或删除。</p></div>
          </div>
        </section>
      </div>}
      {loginOpen&&<div className="overlay" onMouseDown={e=>{if(e.target===e.currentTarget&&!schoolBusy){setLoginOpen(false);setPassword("");}}}>
        <form className="modal edit-modal" role="dialog" aria-modal="true" aria-label="本人教务登录" onSubmit={e=>void loginUser(e)}>
          <div className="modal-heading"><h2>登录本人教务账号</h2><button type="button" aria-label="关闭登录" disabled={schoolBusy} onClick={()=>{setLoginOpen(false);setPassword("");}}><X size={18}/></button></div>
          <p>账号密码只用于本次本人身份验证，不会保存在磁盘。可选记住学校颁发的会话，之后免密码恢复；每次恢复都必须通过学校身份回读。</p>
          <div className="form-grid">
            <label>教务账号<input required autoComplete="username" value={username} maxLength={128} onChange={e=>setUsername(e.target.value)}/></label>
            <label>教务密码<input required type="password" autoComplete="current-password" value={password} maxLength={256} onChange={e=>setPassword(e.target.value)}/></label>
          </div>
          <label className="remember-row">
            <input type="checkbox" checked={rememberLogin} onChange={e=>setRememberLogin(e.target.checked)} />
            <span><strong>记住本机登录（Windows 加密）</strong><small>仅保存学校会话，不保存密码；本机最多保留 7 天，学校会话可提前失效</small></span>
          </label>
          {loginResult&&!loginResult.session.verified_at_login&&<div className="notice warning" role="status">{loginResult.message}</div>}
          <div className="modal-actions"><button className="button" type="button" disabled={schoolBusy} onClick={()=>{setPassword("");setLoginOpen(false);}}>取消</button>
            <button className="button primary" type="submit" disabled={schoolBusy||!username.trim()||!password}>{schoolBusy?"登录核验中…":"登录并核验身份"}</button></div>
        </form>
      </div>}
      {plansOpen&&<div className="overlay" onMouseDown={e=>{if(e.target===e.currentTarget)setPlansOpen(false);}}>
        <section className="modal" role="dialog" aria-modal="true" aria-label="管理课程方案">
          <div className="modal-heading"><h2>本机课程方案</h2><button aria-label="关闭方案管理" onClick={()=>setPlansOpen(false)}><X size={18}/></button></div>
          <p>保存课程和查询设置的本地快照，不包含账号、Cookie 或教务协议。</p>
          {dialogError&&<div role="alert" className="notice error">{dialogError}</div>}
          <div className="plan-create"><input aria-label="新方案名称" value={planName} maxLength={40} placeholder="输入方案名称" onChange={e=>setPlanName(e.target.value)}/><button className="button primary" disabled={!planName.trim()||planBusy} onClick={()=>void handlePlan("save",planName)}>保存当前方案</button></div>
          <div className="plan-list">{plans.length===0?<div className="empty">暂无保存的课程方案</div>:plans.map(name=><div className="plan-item" key={name}><strong>{name}</strong><div className="button-cluster"><button className="button" disabled={planBusy} onClick={()=>void handlePlan("apply",name)}>切换</button><button className="button" disabled={planBusy} onClick={()=>void handlePlan("delete",name)}>删除</button></div></div>)}</div>
        </section>
      </div>}
      {strategyOpen&&strategyDraft&&<div className="overlay" onMouseDown={e=>{if(e.target===e.currentTarget)setStrategyOpen(false);}}>
        <form className="modal edit-modal" role="dialog" aria-modal="true" aria-label="编辑本机查询设置" onSubmit={e=>void persistStrategy(e)}>
          <div className="modal-heading"><h2>本机查询设置</h2><button type="button" aria-label="关闭查询设置" onClick={()=>setStrategyOpen(false)}><X size={18}/></button></div>
          <p>使用原有 Python 配置校验。所有设置仅对允许范围内的只读查询生效，不提供选课提交。</p>
          {dialogError&&<div role="alert" className="notice error">{dialogError}</div>}
          <div className="form-grid">
            <label>学年学期<input value={strategyDraft.semester} maxLength={60} onChange={e=>setStrategyDraft(v=>v?{...v,semester:e.target.value}:v)}/></label>
            <label>查询模式<select value={strategyDraft.mode} onChange={e=>setStrategyDraft(v=>v?{...v,mode:e.target.value as Strategy["mode"]}:v)}><option value="monitor">普通只读监测</option><option value="burst">定时单次查询</option></select></label>
            <label>计划时间（北京时间）<input value={strategyDraft.target_time} maxLength={60} onChange={e=>setStrategyDraft(v=>v?{...v,target_time:e.target.value}:v)}/></label>
            {(["interval_low","interval_high","max_cycles","max_duration_minutes"] as const).map(key=><label key={key}>{({interval_low:"查询间隔下限（秒）",interval_high:"查询间隔上限（秒）",max_cycles:"最多查询次数",max_duration_minutes:"最长监测时间（分钟）"})[key]}<input type="number" value={strategyDraft[key]} onChange={e=>setStrategyDraft(v=>v?{...v,[key]:Number(e.target.value)}:v)}/></label>)}
          </div>
          <div className="modal-actions"><button type="button" className="button" onClick={()=>setStrategyOpen(false)}>取消</button><button className="button primary" type="submit" disabled={loading}>保存本机设置</button></div>
        </form>
      </div>}
      {editing!==null&&<div className="overlay" onMouseDown={event=>{if(event.target===event.currentTarget)setEditing(null);}}>
        <form className="modal edit-modal" role="dialog" aria-modal="true" aria-label="编辑目标课程" onSubmit={acceptEdit}>
          <div className="modal-heading"><h2>{editing===-1?"添加课程":"编辑课程"}</h2><button type="button" aria-label="取消编辑" onClick={()=>setEditing(null)}><X size={19}/></button></div>
          <p>以下字段仅用于本机课程计划，不会直接向教务系统提交。</p>
          {editError&&<div role="alert" className="notice error">{editError}</div>}
          <div className="form-grid">{courseFields.map(({key,label})=><label key={key}>{label}<input value={draft[key]} maxLength={key==="time_slots"?200:120} onChange={e=>updateDraft(key,e.target.value)} placeholder={label}/></label>)}</div>
          <div className="modal-actions"><button type="button" className="button" onClick={()=>setEditing(null)}>取消</button><button type="submit" className="button primary"><Check size={16}/>应用到草稿</button></div>
        </form>
      </div>}
    </div>
  );
}
