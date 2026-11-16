import {invoke} from "@tauri-apps/api/core";

export type Course = {
  key: string; course_id: string; class_id: string;
  course_name: string; teacher: string; group: string; time_slots: string;
};
export type Snapshot = {
  semester: string;
  courses: Course[];
  mode: string;
  target_time: string;
  interval_seconds: number[];
  max_cycles: number;
  max_duration_minutes: number;
  protocol_verified: boolean;
  login_status: "not_checked";
  catalog_status: "not_checked";
  automatic_submit_available: false;
  data_source: "local_config";
};
export const blankCourse = (): Course => ({
  key: "", course_id: "", class_id: "", course_name: "", teacher: "", group: "", time_slots: ""
});

export function isDesktop(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}
export async function snapshot(): Promise<Snapshot> {
  if (!isDesktop()) throw new Error("请在打包后的 Windows EXE 中读取真实本机数据");
  return invoke<Snapshot>("kingo_snapshot");
}
export async function saveCourses(courses: Course[]): Promise<Snapshot> {
  if (!isDesktop()) throw new Error("浏览器预览不能写入本机课程计划");
  return invoke<Snapshot>("kingo_save_courses", {courses});
}
export async function windowAction(action: "minimize" | "maximize" | "close"): Promise<void> {
  if (isDesktop()) await invoke("kingo_window_action", {action});
}

export type LocalCheck = {label:string;passed:boolean;detail:string;severity:"blocker"|"warning"};
export type Preflight = {source:"local_only";checks:LocalCheck[];blockers:number;warnings:number;school_verified:false};
export type Plans = {plans:string[]};
export type AppliedPlan = Snapshot & {backup_name:string;plans:string[]};
export type Strategy = {
  semester:string;
  mode:"monitor"|"burst";
  target_time:string;
  interval_low:number;
  interval_high:number;
  max_cycles:number;
  max_duration_minutes:number;
};
async function action<T>(operation:string,payload?:unknown):Promise<T> {
  if (!isDesktop()) throw new Error("仅 Windows 桌面版支持本机操作");
  return invoke<T>("kingo_local_action", {action:operation, payload:payload??null});
}
export const preflight = () => action<Preflight>("preflight");
export const listPlans = () => action<Plans>("plans");
export const createPlan = (name:string) => action<Plans>("save-plan",{name});
export const applyPlan = (name:string) => action<AppliedPlan>("apply-plan",{name});
export const deletePlan = (name:string) => action<Plans>("delete-plan",{name});
export const saveStrategy = (settings:Strategy) => action<Snapshot>("save-strategy",settings);

export type SessionInfo = {
  status:"not_logged_in"|"verified";
  verified_at_login:boolean;
  persistent:false;
  origin:string;
};
export type LoginReply = {
  code:string;
  message:string;
  session:SessionInfo;
};
export type OfficialCatalogCourse = {
  course_name:string;
  course_code:string;
  class_number:string;
  teacher:string;
  selection_status:string;
  points_label:string;
};
export type OfficialCatalog = {
  code:string;
  message:string;
  window:"closed"|"reported_open"|"unknown";
  stage:string;
  courses:OfficialCatalogCourse[];
  total_courses:number;
  display_limit:number;
  data_source:"live_school"|"unavailable";
  seat_count_verified:false;
  session:SessionInfo;
};
export async function signedLogin(username:string,password:string):Promise<LoginReply>{
  if(!isDesktop()) throw new Error("请在 Windows 客户端中登录");
  return invoke<LoginReply>("kingo_session_action",{
    action:"login",payload:{username,password}
  });
}
export async function sessionStatus():Promise<SessionInfo>{
  if(!isDesktop()) throw new Error("请在 Windows 客户端中查看会话状态");
  return invoke<SessionInfo>("kingo_session_action",{action:"session",payload:null});
}
export async function logout():Promise<SessionInfo>{
  if(!isDesktop()) throw new Error("请在 Windows 客户端中退出登录");
  return invoke<SessionInfo>("kingo_session_action",{action:"logout",payload:null});
}
export async function manualCatalog():Promise<OfficialCatalog>{
  if(!isDesktop()) throw new Error("请在 Windows 客户端中读取学校课程");
  return invoke<OfficialCatalog>("kingo_session_action",{action:"catalog",payload:null});
}

export type MonitorSnapshot = {
  code?: "started"|"stop_requested"|"not_running"|"preflight_rejected"|"missing_session"|"busy"|"start_failed";
  phase:"idle"|"running"|"stopping"|"completed"|"failed"|"stopped";
  active:boolean;
  cycles:number;
  last_ms:number|null;
  parse_ms:number|null;
  message:string;
  read_only:true;
  enrollment_available:false;
};
function monitorAction(action:"monitor-start"|"monitor-stop"|"monitor-status"):Promise<MonitorSnapshot>{
  if(!isDesktop()) throw new Error("只读监测仅支持 Windows EXE");
  return invoke<MonitorSnapshot>("kingo_session_action",{action,payload:null});
}
export const startReadOnlyMonitor = () => monitorAction("monitor-start");
export const stopReadOnlyMonitor = () => monitorAction("monitor-stop");
export const monitorStatus = () => monitorAction("monitor-status");
