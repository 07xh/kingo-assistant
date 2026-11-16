//! Private, process-lifetime school session. Password and cookies never appear
//! in a webview response, resource path, local file, or application log.
use std::io::{BufRead, BufReader, Read, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::{Arc, Mutex};
use tauri::Manager;

const RESPONSE_LIMIT: usize = 1_048_576;
const REQUEST_LIMIT: usize = 128 * 1024;

struct SessionWorker {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

impl SessionWorker {
    fn start(app: &tauri::AppHandle) -> Result<Self, String> {
        let binary = super::sidecar_path(app)?;
        let mut command = Command::new(binary);
        command.arg("--serve")
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command.spawn().map_err(|_| "登录服务启动失败".to_string())?;
        let stdin = child.stdin.take().ok_or("登录服务管道不可用")?;
        let stdout = BufReader::new(child.stdout.take().ok_or("登录服务管道不可用")?);
        Ok(Self { child, stdin, stdout })
    }

    fn exchange(&mut self, action: &str, payload: Option<serde_json::Value>)
        -> Result<serde_json::Value, String>
    {
        let mut request = serde_json::to_vec(
            &serde_json::json!({"action": action, "payload": payload}),
        ).map_err(|_| "请求格式无效".to_string())?;
        if request.len() > REQUEST_LIMIT { return Err("输入数据太长".to_string()); }
        request.push(b'\n');
        self.stdin.write_all(&request).map_err(|_| "登录服务连接中断".to_string())?;
        self.stdin.flush().map_err(|_| "登录服务连接中断".to_string())?;
        // The child writes exactly one bounded JSONL response per command.
        let mut line = Vec::new();
        let read = self.stdout.by_ref()
            .take((RESPONSE_LIMIT + 1) as u64)
            .read_until(b'\n', &mut line)
            .map_err(|_| "登录服务读取失败".to_string())?;
        if read == 0 || read > RESPONSE_LIMIT || !line.ends_with(b"\n") {
            return Err("登录服务响应超出范围".to_string());
        }
        let response: serde_json::Value = serde_json::from_slice(&line)
            .map_err(|_| "登录服务响应格式错误".to_string())?;
        if response.get("ok") != Some(&serde_json::Value::Bool(true)) {
            return Err("本机业务请求被拒绝".to_string());
        }
        response.get("data").cloned().ok_or_else(|| "登录服务没有返回结果".to_string())
    }
}

impl Drop for SessionWorker {
    fn drop(&mut self) {
        // No retained local cookies. The session ends when the Lite shell exits.
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[derive(Default, Clone)]
pub struct SessionState(Arc<Mutex<Option<SessionWorker>>>);

impl SessionState {
    /// Serialize all local configuration writes against monitor-start and
    /// reject them while the original read-only monitoring worker is active.
    pub(crate) fn run_when_idle<F, T>(&self, operation: F) -> Result<T, String>
    where
        F: FnOnce() -> Result<T, String>,
    {
        let mut session = self.0.lock().map_err(|_| "本机会话状态不可用".to_string())?;
        if let Some(worker) = session.as_mut() {
            let state = worker.exchange("monitor-status", None)?;
            if state.get("active").and_then(|value| value.as_bool()) != Some(false) {
                return Err("请先停止只读监测，再修改课程或查询设置".to_string());
            }
        }
        operation()
    }
}


#[tauri::command]
pub async fn kingo_session_action(
    app: tauri::AppHandle,
    state: tauri::State<'_, SessionState>,
    action: String,
    payload: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    match action.as_str() {
        "login" => {
            let body = payload.as_ref().and_then(|x| x.as_object())
                .ok_or("登录请求格式无效")?;
            if body.len() != 3
                || !body.get("username").is_some_and(|x| x.is_string())
                || !body.get("password").is_some_and(|x| x.is_string())
                || !body.get("remember").is_some_and(|x| x.is_boolean()) {
                return Err("登录请求格式无效".to_string());
            }
        }
        "catalog" | "session" | "logout" | "restore" | "restore-readiness" | "forget" | "check-session" | "monitor-start" | "monitor-stop" | "monitor-status" if payload.is_none() => {}
        _ => return Err("不支持的登录服务操作".to_string()),
    }
    let session = state.inner().0.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let mut worker = session.lock().map_err(|_| "登录服务状态错误".to_string())?;
        if worker.is_none() {
            *worker = Some(SessionWorker::start(&app)?);
        }
        let result = worker.as_mut().ok_or("登录服务不可用")?.exchange(&action, payload);
        if result.is_err() {
            // Fail closed: discard the entire session rather than retrying an
            // ambiguous school read or resurrecting a partial login.
            *worker = None;
        }
        result
    }).await.map_err(|_| "登录服务被中断".to_string())?
}
