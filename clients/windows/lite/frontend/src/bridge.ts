import {invoke} from "@tauri-apps/api/core";

export type Course = {
  key: string; course_id: string; class_id: string;
  course_name: string; teacher: string; group: string; time_slots: string;
};
export type Snapshot = {
  semester: string;
  courses: Course[];
  mode: string;
  interval_seconds: number[];
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
