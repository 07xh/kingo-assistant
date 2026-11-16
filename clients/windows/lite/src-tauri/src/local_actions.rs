#[tauri::command]
pub async fn kingo_local_action(
    app: tauri::AppHandle,
    action: String,
    payload: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    if !matches!(action.as_str(), "preflight" | "plans" | "save-plan" | "apply-plan" | "delete-plan" | "save-strategy") {
        return Err("不支持的本机操作".to_string());
    }
    let body = match (action.as_str(), payload) {
        ("preflight" | "plans", None) => None,
        (_, Some(value)) => {
            let data = serde_json::to_vec(&value).map_err(|_| "请求格式无效".to_string())?;
            if data.len() > 32_000 { return Err("请求过大".to_string()); }
            Some(data)
        }
        _ => return Err("请求缺少数据".to_string())
    };
    tauri::async_runtime::spawn_blocking(move || super::call_sidecar(&app, &action, body))
        .await.map_err(|_| "本机任务中断".to_string())?
}
