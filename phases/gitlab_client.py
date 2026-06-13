from __future__ import annotations
import urllib.parse
import urllib.request
import urllib.error
import json


class GitLabClient:
    def __init__(self, url: str, token: str, project_id: str):
        self.url = url.rstrip("/")
        self.token = token
        # 数字 ID 直接用，字符串路径才需要 URL 编码
        self._project = project_id if project_id.isdigit() else urllib.parse.quote(project_id, safe="")
        self._headers = {"PRIVATE-TOKEN": token, "Content-Type": "application/json"}

    def _base(self) -> str:
        return f"{self.url}/api/v4/projects/{self._project}/merge_requests"

    def _get(self, url: str) -> dict | list:
        req = urllib.request.Request(url, headers=self._headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            _handle_http_error(e)

    def _post(self, url: str, body: dict) -> None:
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, headers=self._headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30):
                pass
        except urllib.error.HTTPError as e:
            _handle_http_error(e)

    def get_mr_meta(self, mr_iid: int) -> dict:
        """返回 MR 基本信息，含 diff_refs（base/start/head sha）。"""
        return self._get(f"{self._base()}/{mr_iid}")

    def get_mr_diff(self, mr_iid: int) -> str:
        """返回 unified diff 字符串（拼接所有文件 diff）。"""
        data = self._get(f"{self._base()}/{mr_iid}/changes")
        # /changes 返回 MR 对象，file changes 在 data["changes"] 里
        file_changes = data.get("changes", []) if isinstance(data, dict) else data
        parts = []
        for d in file_changes:
            old_path = d.get("old_path", "")
            new_path = d.get("new_path", "")
            header = f"diff --git a/{old_path} b/{new_path}\n"
            if d.get("new_file"):
                header += "new file mode 100644\n"
            elif d.get("deleted_file"):
                header += "deleted file mode 100644\n"
            elif d.get("renamed_file"):
                header += f"rename from {old_path}\nrename to {new_path}\n"
            header += f"--- a/{old_path}\n+++ b/{new_path}\n"
            parts.append(header + d.get("diff", ""))
        return "".join(parts)

    def post_summary_comment(self, mr_iid: int, body: str) -> None:
        """在 MR 顶部发一条总结评论。"""
        self._post(f"{self._base()}/{mr_iid}/notes", {"body": body})

    def post_inline_comment(
        self,
        mr_iid: int,
        body: str,
        file_path: str,
        new_line: int,
        diff_refs: dict,
    ) -> None:
        """在指定文件行发 inline 讨论。"""
        position = {
            "base_sha": diff_refs.get("base_sha", ""),
            "start_sha": diff_refs.get("start_sha", ""),
            "head_sha": diff_refs.get("head_sha", ""),
            "position_type": "text",
            "new_path": file_path,
            "new_line": new_line,
        }
        self._post(
            f"{self._base()}/{mr_iid}/discussions",
            {"body": body, "position": position},
        )


def _handle_http_error(e: urllib.error.HTTPError) -> None:
    status = e.code
    if status == 401:
        raise ValueError("GitLab Token 无效或已过期（401）")
    if status == 403:
        raise ValueError("无权访问该 MR 或项目（403）")
    if status == 404:
        raise ValueError("MR 或项目不存在（404）")
    raise ValueError(f"GitLab API 错误 {status}：{e.reason}")
