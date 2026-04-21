"""
会话管理 — 内存 Session Store

每个会话保存对话历史、当前状态机阶段、以及跨轮需要传递的中间数据。
生产环境可替换为 Redis（只需改 SessionStore 的存取实现）。
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from api.models import SessionState


class Session:
    """单个用户会话"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.state: SessionState = SessionState.INIT
        self.created_at: str = datetime.now(timezone.utc).isoformat()
        self.updated_at: str = self.created_at

        # 用户输入的基本信息（INIT 阶段收集）
        self.city: Optional[str] = None
        self.days: int = 3
        self.people: str = "2人"
        self.transport: str = "公共交通"

        # 偏好信息（COLLECT_PREFERENCES 阶段收集）
        self.preferences: Optional[str] = None

        # 旅行者画像（INIT 阶段 Profiler 识别）
        self.traveler_type: str = "A"  # A-F 六种类型
        self.traveler_type_confidence: float = 0.3
        self.traveler_type_secondary: Optional[str] = None

        # 候选景点（SELECT_SPOTS 阶段）
        self.candidate_spots: list[dict] = []

        # 用户选中的景点（SELECT_SPOTS → GENERATING 阶段）
        self.selected_spots: list[dict] = []

        # LLM 对话历史（用于多轮 follow-up）
        self.messages: list[dict] = []

        # 偏好追问轮次计数（最多追问 2 轮）
        self.pref_rounds: int = 0

        # Exa 网络攻略缓存（SELECT_SPOTS 阶段获取，行程编排复用）
        self.web_guides: list[str] = []

    def touch(self):
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def add_message(self, role: str, content: str):
        """追加对话历史（保留最近 50 条，避免内存泄漏）"""
        self.messages.append({"role": role, "content": content})
        if len(self.messages) > 50:
            self.messages = self.messages[-50:]
        self.touch()

    @property
    def travel_info(self) -> str:
        return f"{self.people}，{self.transport}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "state": self.state,
            "city": self.city,
            "days": self.days,
            "traveler_type": self.traveler_type,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class SessionStore:
    """内存 Session 存储（线程安全：FastAPI 单进程下 asyncio 无并发竞态）"""

    def __init__(self):
        self._store: dict[str, Session] = {}

    def create(self) -> Session:
        sid = str(uuid.uuid4())
        session = Session(sid)
        self._store[sid] = session
        return session

    def get(self, session_id: str) -> Optional[Session]:
        return self._store.get(session_id)

    def get_or_create(self, session_id: Optional[str]) -> tuple[Session, bool]:
        """
        返回 (session, is_new)
        session_id 为 None 或不存在时自动创建
        """
        if session_id and session_id in self._store:
            return self._store[session_id], False
        session = self.create()
        return session, True

    def delete(self, session_id: str) -> bool:
        if session_id in self._store:
            del self._store[session_id]
            return True
        return False

    def count(self) -> int:
        return len(self._store)


# 全局单例，整个应用共享
store = SessionStore()
