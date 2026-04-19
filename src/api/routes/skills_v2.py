"""技能 REST API — 只读，供桌面端可视化。"""

import logging

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("lapwing.api.routes.skills_v2")

router = APIRouter(prefix="/api/v2/skills", tags=["skills-v2"])

_skill_store = None


def init(skill_store) -> None:
    global _skill_store
    _skill_store = skill_store


@router.get("")
async def list_skills(
    maturity: str = Query(None, description="按状态过滤"),
    tag: str = Query(None, description="按标签过滤"),
):
    if _skill_store is None:
        return {"skills": [], "total": 0}
    skills = _skill_store.list_skills(maturity=maturity, tag=tag)
    return {"skills": skills, "total": len(skills)}


@router.get("/{skill_id}")
async def get_skill(skill_id: str):
    if _skill_store is None:
        raise HTTPException(status_code=503, detail="SkillStore not available")
    skill = _skill_store.read(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return {
        "meta": skill["meta"],
        "code": skill["code"],
        "file_path": skill["file_path"],
    }
