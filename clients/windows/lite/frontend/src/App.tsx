import {useEffect,useState,type FormEvent} from "react";
import {
  Activity,BookOpen,Check,ChevronRight,Clock3,FileText,LayoutDashboard,
  Minus,Monitor, Moon,PanelLeftClose,PanelLeftOpen,Plus,RefreshCw,
  Save,Settings,ShieldAlert,Sun,Trash2, X, Square, GraduationCap, Pencil
} from "lucide-react";
import {blankCourse,isDesktop,saveCourses,snapshot,windowAction,type Course,type Snapshot} from "./bridge";

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
  useEffect(()=>{localStorage.setItem("kingo-lite.theme",theme);},[theme]);
  useEffect(()=>{localStorage.setItem("kingo-lite.nav.collapsed",String(collapsed));},[collapsed]);
  function openEditor(index:number) {
    setEditError("");
    setEditing(index);
    setDraft(index < 0 ? blankCourse() : {...courses[index]});
  }
  function updateDraft(field:keyof Course,value:string) {
    setDraft(current=>({...current,[field]:value}));
  }
  function acceptEdit(event:FormEvent<HTMLFormElement>) {
    event.preventDefault();
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
    setLoading(true);setError("");
    try {
      const data=await saveCourses(courses);
      setCourses(data.courses);setSnapshotData(data);setDirty(false);
      log("本机课程计划已保存；不代表学校选课成功");
    }catch(cause){setError(shortError(cause));log("本机课程计划保存失败");}
    finally{setLoading(false);}
  }
  function removeCourse(index:number) {
    if(!window.confirm("仅从本机计划中移除此课程？不会访问教务网站。"))return;
    setCourses(current=>current.filter((_,i)=>i!==index));setDirty(true);
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
          <button title="关闭" aria-label="关闭" className="close-window" onClick={()=>void windowAction("close")}><X size={16}/></button>
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
            <button className="button subtle" disabled={loading||dirty} onClick={()=>void reload()} title={dirty?"请先保存或放弃未保存修改":"重新读取本机配置"}>
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
              <div className="card metric"><span>账号登录</span><strong className="metric-word">未验证</strong><small>新界面尚未接入认证</small></div>
              <div className="card metric"><span>在线课程</span><strong className="metric-word">未查询</strong><small>无实时余量数据</small></div>
              <div className="card metric"><span>自动提交</span><strong className="metric-word">不可用</strong><small>真实提交适配器未验收</small></div>
            </div>
            <section className="card section">
              <div className="section-heading"><div><h3>快捷入口</h3><p>只展示当前有明确功能的操作</p></div></div>
              <div className="quick-grid">
                <button className="quick" onClick={()=>go("courses")}><BookOpen size={22}/><strong>管理选课计划</strong><small>添加课程、调整志愿</small><ChevronRight size={16} className="quick-arrow"/></button>
                <button className="quick" onClick={()=>go("execution")}><Monitor size={22}/><strong>查看执行状态</strong><small>区分只读、调度与正式提交</small><ChevronRight size={16} className="quick-arrow"/></button>
              </div>
            </section>
          </>}
          {page==="courses"&&<>
            <div className="section-heading top-actions"><div><h3>目标课程</h3><p>仅保存到本机配置，最多 30 门；示例课程不会作为真实在线课程显示。</p></div><button className="button primary" disabled={!snapshotData||courses.length>=30} onClick={()=>openEditor(-1)}><Plus size={16}/>添加课程</button></div>
            <section className="card table-wrap">
              <table><thead><tr><th>优先级</th><th>志愿</th><th>课程</th><th>教学班</th><th>备选组</th><th>状态</th><th>操作</th></tr></thead>
              <tbody>{courses.map((course,index)=><tr key={index}><td>{index+1}</td><td>{course.key}</td><td><strong>{presentCourse(course)}</strong><small>{course.course_id||"无课程代码"} · {course.teacher||"未填教师"}</small></td><td>{course.class_id||"—"}</td><td>{course.group||"—"}</td><td><span className="status muted">{isPlaceholder(course)?"示例 · 未连接":"未查询"}</span></td><td><div className="row-actions"><button aria-label={"编辑"+presentCourse(course)} onClick={()=>openEditor(index)}><Pencil size={15}/></button><button aria-label={"删除"+presentCourse(course)} onClick={()=>removeCourse(index)}><Trash2 size={15}/></button></div></td></tr>)}</tbody></table>
              {courses.length===0&&<div className="empty"><BookOpen size={26}/><strong>还没有配置课程</strong><span>使用右上角「添加课程」建立本机计划</span></div>}
            </section>
            <div className="course-footer"><small>学期：{snapshotData?.semester||"未读取"} · 当前界面不调用学校选课接口</small><button className="button primary" disabled={!dirty||loading} onClick={()=>void persist()}><Save size={16}/>保存本机计划</button></div>
          </>}
          {page==="execution"&&<>
            <section className="card section"><h3>真实能力状态</h3>
              <div className="cap-row"><span>本地课程计划</span><span className={"status "+(snapshotData?"good":"muted")}>{snapshotData?"可读取":"不可用"}</span></div>
              <div className="cap-row"><span>官方账号会话</span><span className="status muted">新界面未接通</span></div>
              <div className="cap-row"><span>学校实时课程</span><span className="status muted">未查询</span></div>
              <div className="cap-row"><span>只读监测</span><span className="status muted">等待迁移后台控制器</span></div>
              <div className="cap-row"><span>自动提交</span><span className="status blocked">学校写入适配器缺失</span></div>
            </section>
            <section className="card section"><h3>当前本机查询配置</h3>
              <p>模式：{snapshotData?.mode==="burst"?"定时单次查询":"常规只读监测"}　查询间隔：{snapshotData?.interval_seconds?.join("–")||"未读取"} 秒</p>
              <p>此页不会自行启动任务或打开浏览器。原有 Tk Windows 客户端仍是当前监测入口，直到完成有状态任务桥接与 Windows 验收。</p>
              <button disabled className="button primary" title="当前 Lite 版本未接入后台监测与学校提交">启动任务（尚未接入）</button>
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
            <div className="setting-row"><div><strong>侧边栏</strong><p>收起后只显示图标</p></div><button className="button" onClick={()=>setCollapsed(x=>!x)}>{collapsed?"展开":"收起"}</button></div>
            <h3>关于</h3><p>KingoAssistant Lite · 独立实现的轻量 UI。参考 LightC 的布局理念；不包含 LightC 原版源码、标识或受限资源。</p>
            <p>此版本正在迁移桌面界面。旧版已具备的真实业务逻辑不会被本界面替代或删除。</p></div>
          </div>
        </section>
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
