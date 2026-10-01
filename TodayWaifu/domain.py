"""TodayWaifu 的纯领域数据结构。

这些类型只承载候选与记录，不依赖 GsCore、数据库或配置：抽出的目的是让筛选、挑选与
持久化之间的边界不被上游类型渗透，使图库与成员的来源差异收敛在构造位置
（``from_role`` / ``from_member``），下游只面对同一套记录。

全部声明为 frozen，避免同一条记录在两个模块间被就地改写后彼此看到的字段不一致；
需要变更时一律构造新实例（参见 gift 模块通过 ``dict(record)`` 复制后再写入）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleCandidate:
    name: str
    role_ids: tuple[str, ...]
    images: tuple[str, ...]


@dataclass(frozen=True)
class MemberCandidate:
    name: str
    user_id: str
    avatar: str


@dataclass(frozen=True)
class WifeRecord:
    name: str
    role_ids: tuple[str, ...]
    image: str
    record_type: str = 'role'
    target_user_id: str = ''

    @classmethod
    def from_role(cls, role: RoleCandidate, image: str) -> 'WifeRecord':
        # 只取本次选中的单张图片：记录一旦落盘即固定，后续图库增删不会改变今日结果。
        return cls(role.name, role.role_ids, image)

    @classmethod
    def from_member(cls, member: MemberCandidate) -> 'WifeRecord':
        # 群友记录用 role_ids=('群友',) 占位而非留空：下游多处按 role_ids 非空判断
        # 记录有效性，留空会让群友需要走另一条分支。target_user_id 保存群友 QQ，
        # 供离婚、赠送等后续操作定位到具体的人。
        return cls(member.name, ('群友',), member.avatar, 'member', member.user_id)

    def to_role(self) -> RoleCandidate:
        # image 是单值而 RoleCandidate 需要元组，这里包成单元组以便复用角色渲染路径。
        return RoleCandidate(self.name, self.role_ids, (self.image,))

    def to_member(self) -> MemberCandidate:
        # 与 from_member 互逆，供记录回落到群友分支时还原候选对象。
        return MemberCandidate(self.name, self.target_user_id, self.image)
