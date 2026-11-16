//! A deliberately small desktop adapter for the existing KingoAssistant core.
//! No school endpoints, login credentials or write operations are exposed here.
mod local_actions;
use local_actions::kingo_local_action;
use std::io::Write;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use tauri::Manager;

fn sidecar_path(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    if let Ok(dir) = app.path().resource_dir() {
        let path = dir.join("bin").join("kingo-sidecar.exe");
        if path.is_file() {
            return Ok(path);
        }
    }
    if cfg!(debug_assertions) {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..").join("packaging").join("sidecar").join("kingo-sidecar.exe");
        if path.is_file() {
            return Ok(path);
        }
    }
    Err("本机业务服务未打包，请重新安装 Lite 版本".to_string())
}

fn call_sidecar(app: &tauri::AppHandle, action: &str, payload: Option<Vec<u8>>)
    -> Result<serde_json::Value, String>
{
    let binary = sidecar_path(app)?;
    let mut command = Command::new(binary);
    command.arg(action).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }
    let mut child = command.spawn().map_err(|_| "本机业务服务启动失败".to_string())?;
    if let Some(body) = payload {
        let mut stdin = child.stdin.take().ok_or("本机业务服务通信失败")?;
        stdin.write_all(&body).map_err(|_| "本机业务服务通信失败")?;
    } else {
        drop(child.stdin.take());
    }
    let output = child.wait_with_output().map_err(|_| "本机业务服务没有返回结果".to_string())?;
    if output.stdout.len() > 256_000 {
        return Err("本机数据超出预期大小".to_string());
    }
    let response: serde_json::Value = serde_json::from_slice(&output.stdout)
        .map_err(|_| "本机业务服务返回无效数据".to_string())?;
    if !output.status.success() || response.get("ok") != Some(&serde_json::Value::Bool(true)) {
        let error = response.get("error").and_then(|value| value.as_str()).unwrap_or("UNKNOWN");
        return Err(format!("本机配置处理失败：{}", if error.starts_with("LOCAL_CONFIG_"){error}else{"UNKNOWN"}));
    }
    response.get("data").cloned().ok_or_else(|| "本机数据缺失".to_string())
}

#[tauri::command]
async fn kingo_snapshot(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || call_sidecar(&app, "snapshot", None))
        .await.map_err(|_| "本机任务中断".to_string())?
}

#[tauri::command]
async fn kingo_save_courses(app: tauri::AppHandle, courses: serde_json::Value)
    -> Result<serde_json::Value, String>
{
    let items = courses.as_array().ok_or("课程清单格式无效")?;
    if items.len() > 30 {
        return Err("目标课程不能超过 30 门".to_string());
    }
    let body = serde_json::to_vec(&courses).map_err(|_| "课程序列化失败")?;
    if body.len() > 32_000 {
        return Err("课程配置超过大小限制".to_string());
    }
    tauri::async_runtime::spawn_blocking(move || call_sidecar(&app, "save-courses", Some(body)))
        .await.map_err(|_| "本机任务中断".to_string())?
}

#[tauri::command]
fn kingo_window_action(window: tauri::WebviewWindow, action: &str) -> Result<(), String> {
    match action {
        "minimize" => window.minimize(),
        "maximize" => {
            let maximized = window.is_maximized().map_err(|_| "窗口状态读取失败")?;
            if maximized { window.unmaximize() } else { window.maximize() }
        }
        "close" => window.close(),
        _ => return Err("不支持的窗口操作".to_string()),
    }.map_err(|_| "窗口操作失败".to_string())
}

pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            kingo_snapshot, kingo_save_courses, kingo_local_action, kingo_window_action
        ])
        .run(tauri::generate_context!())
        .expect("Unable to start KingoAssistant Lite");
}
