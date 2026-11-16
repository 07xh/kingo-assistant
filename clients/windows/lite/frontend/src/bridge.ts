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
