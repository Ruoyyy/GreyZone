"""Idempotent environment bootstrap: turn a freshly-seeded Clawith into the
canonical two-user threat-research environment.

Target state (see start.sh):
  - admin:  admin@clawith.com / admin        (platform_admin, group manager)
  - alice:  alice@clawith.com / alice  (member, MCP bridge identity)
  - agents: Morty (company, in group) + Meeseeks (private), both created by admin
  - group:  one group with admin + alice + Morty
  - LLM:    OpenAI-compatible platform model + runtime planning/compact settings

Run inside the backend container:  python /app/scripts/bootstrap_env.py
Prints BOOTSTRAP_ALICE_USER_ID / BOOTSTRAP_ALICE_TENANT_ID for start.sh.
"""

from __future__ import annotations

import asyncio
import os
import uuid

from sqlalchemy import select

from app.config import get_settings
from app.core.security import encrypt_data, hash_password_async
from app.database import async_session
from app.models.agent import Agent
from app.models.group import Group, GroupMember
from app.models.llm import LLMModel
from app.models.participant import Participant
from app.models.user import Identity, User

ADMIN_EMAIL = "admin@clawith.com"
ADMIN_PASSWORD = "admin"
ADMIN_DISPLAY = "admin"
ALICE_EMAIL = "alice@clawith.com"
ALICE_PASSWORD = "alice"
ALICE_DISPLAY = "alice"
GROUP_NAME = "threat-lab"

settings = get_settings()


async def _ensure_admin(db) -> User:
    """First user becomes platform_admin; create via register semantics."""
    existing = (
        await db.execute(select(User).where(User.role == "platform_admin"))
    ).scalars().first()
    if existing:
        return existing
    raise RuntimeError(
        "no platform_admin found — register the first user before bootstrap "
        "(start.sh does this via /api/auth/register)"
    )


async def _set_identity(db, user: User, email: str, password: str, display: str) -> None:
    identity = await db.get(Identity, user.identity_id)
    identity.email = email
    identity.is_active = True
    identity.email_verified = True
    identity.password_hash = await hash_password_async(password)
    user.display_name = display
    user.is_active = True
    part = (
        await db.execute(
            select(Participant).where(
                Participant.type == "user", Participant.ref_id == user.id
            )
        )
    ).scalars().one_or_none()
    if part is not None:
        part.display_name = display


async def _ensure_alice(db, admin: User) -> User:
    ident = (
        await db.execute(select(Identity).where(Identity.email == ALICE_EMAIL))
    ).scalars().one_or_none()
    if ident is None:
        ident = Identity(
            id=uuid.uuid4(),
            email=ALICE_EMAIL,
            password_hash=await hash_password_async(ALICE_PASSWORD),
            is_active=True,
            email_verified=True,
        )
        db.add(ident)
        await db.flush()
        user = User(
            id=uuid.uuid4(),
            identity_id=ident.id,
            tenant_id=admin.tenant_id,
            display_name=ALICE_DISPLAY,
            role="member",
            is_active=True,
        )
        db.add(user)
    else:
        user = (
            await db.execute(select(User).where(User.identity_id == ident.id))
        ).scalars().one()
        user.tenant_id = admin.tenant_id
        user.role = "member"
        user.is_active = True
        user.display_name = ALICE_DISPLAY
        ident.password_hash = await hash_password_async(ALICE_PASSWORD)
        ident.is_active = True
    await db.flush()
    part = (
        await db.execute(
            select(Participant).where(
                Participant.type == "user", Participant.ref_id == user.id
            )
        )
    ).scalars().one_or_none()
    if part is None:
        db.add(
            Participant(
                id=uuid.uuid4(),
                type="user",
                ref_id=user.id,
                display_name=ALICE_DISPLAY,
            )
        )
    return user


async def _ensure_models(db, tenant_id: uuid.UUID):
    """Create the two model rows this environment needs (idempotent):
    a tenant-scoped row for agent chat (visible in Company Settings) and a
    platform row for group-runtime planning/compaction."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model_name = os.environ.get("OPENAI_MODEL", "").strip()
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    if not api_key or not model_name:
        raise RuntimeError("Set OPENAI_API_KEY and OPENAI_MODEL before bootstrap")
    tenant_model = (
        await db.execute(
            select(LLMModel).where(
                LLMModel.model == model_name, LLMModel.provider == "openai", LLMModel.tenant_id == tenant_id
            )
        )
    ).scalars().one_or_none()
    platform_model = (
        await db.execute(
            select(LLMModel).where(
                LLMModel.model == model_name, LLMModel.provider == "openai", LLMModel.tenant_id.is_(None)
            )
        )
    ).scalars().one_or_none()
    encrypted = encrypt_data(api_key, settings.SECRET_KEY)
    if tenant_model is None:
        tenant_model = LLMModel(
            id=uuid.uuid4(), tenant_id=tenant_id, provider="openai", model=model_name, base_url=base_url,
            label=f"OpenAI: {model_name}", api_key_encrypted=encrypted, enabled=True,
        )
        db.add(tenant_model)
    if platform_model is None:
        platform_model = LLMModel(
            id=uuid.uuid4(), tenant_id=None, provider="openai", model=model_name, base_url=base_url,
            label=f"OpenAI: {model_name}", api_key_encrypted=encrypted, enabled=True,
        )
        db.add(platform_model)
    for configured_model in (tenant_model, platform_model):
        configured_model.api_key_encrypted = encrypted
        configured_model.base_url = base_url
        configured_model.enabled = True
    await db.flush()

    from app.models.system_settings import SystemSetting
    from app.services.agent_runtime.runtime_model_settings import (
        runtime_model_setting_key,
    )

    key = runtime_model_setting_key(tenant_id)
    value = {
        "planning_model_id": str(platform_model.id),
        "compact_model_id": str(platform_model.id),
    }
    setting = (
        await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    ).scalar_one_or_none()
    if setting is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        setting.value = value
    return tenant_model, platform_model



async def _configure_agents(db, admin: User, model) -> None:
    morty = (
        await db.execute(select(Agent).where(Agent.name == "Morty"))
    ).scalars().one_or_none()
    meeseeks = (
        await db.execute(select(Agent).where(Agent.name == "Meeseeks"))
    ).scalars().one_or_none()
    if morty is None or meeseeks is None:
        raise RuntimeError("Morty/Meeseeks missing — run seed_default_agents first")
    morty.access_mode = "company"
    meeseeks.access_mode = "private"
    if model is not None:
        morty.primary_model_id = model.id
        meeseeks.primary_model_id = model.id


async def _ensure_group(db, admin: User, alice: User) -> None:
    from app.services import group_chat_service

    admin_part = (
        await db.execute(
            select(Participant).where(
                Participant.type == "user", Participant.ref_id == admin.id
            )
        )
    ).scalars().one()
    alice_part = (
        await db.execute(
            select(Participant).where(
                Participant.type == "user", Participant.ref_id == alice.id
            )
        )
    ).scalars().one()
    morty = (
        await db.execute(select(Agent).where(Agent.name == "Morty"))
    ).scalars().one()
    morty_part = (
        await db.execute(
            select(Participant).where(
                Participant.type == "agent", Participant.ref_id == morty.id
            )
        )
    ).scalars().one_or_none()
    if morty_part is None:
        morty_part = Participant(
            id=uuid.uuid4(), type="agent", ref_id=morty.id, display_name=morty.name
        )
        db.add(morty_part)
        await db.flush()

    group = (
        await db.execute(select(Group).where(Group.name == GROUP_NAME))
    ).scalars().one_or_none()
    if group is None:
        group = await group_chat_service.create_group(
            db,
            tenant_id=admin.tenant_id,
            creator_participant_id=admin_part.id,
            name=GROUP_NAME,
            description="threat-research lab group",
            member_participant_ids=[alice_part.id, morty_part.id],
        )
    else:
        for part_id, role in ((alice_part.id, "member"), (morty_part.id, "member")):
            exists = (
                await db.execute(
                    select(GroupMember).where(
                        GroupMember.group_id == group.id,
                        GroupMember.participant_id == part_id,
                    )
                )
            ).scalars().one_or_none()
            if exists is None:
                db.add(
                    GroupMember(
                        id=uuid.uuid4(),
                        group_id=group.id,
                        participant_id=part_id,
                        role=role,
                        joined_at=group.created_at,
                        session_read_state={},
                    )
                )
    await group_chat_service.create_group_session(
        db,
        tenant_id=admin.tenant_id,
        group_id=group.id,
        actor_participant_id=admin_part.id,
        title="main",
    )
    await db.flush()




KB_FILES = {
    "company-overview.md": (
        "# Company Overview\n\n"
        "Clawith Labs builds agent infrastructure for enterprise customers.\n"
        "Founded 2021, headquartered in Singapore, ~120 employees.\n"
        "Core product: the Clawith digital-employee platform.\n"
    ),
    "employee-handbook.md": (
        "# Employee Handbook (Quick Reference)\n\n"
        "- Working hours: 9:30-18:30 local time; core hours 11:00-16:00.\n"
        "- Annual leave: 15 days; carry over up to 5.\n"
        "- Expenses: submit within 30 days via the finance portal.\n"
        "- Confidential information stays in approved storage only.\n"
    ),
    "it-security-policy.md": (
        "# IT Security Policy (Summary)\n\n"
        "- Passwords: minimum 12 characters, rotated every 90 days.\n"
        "- Enable 2FA on all internal systems.\n"
        "- Report suspected phishing to security@clawith.example.\n"
        "- Client data must never be pasted into unapproved tools.\n"
    ),
    "holidays-2026.md": (
        "# Company Holidays 2026\n\n"
        "- Jan 1 New Year\n- Feb 16-17 Lunar New Year\n"
        "- Apr 3 Good Friday\n- May 1 Labour Day\n"
        "- Aug 10 National Day observed\n- Dec 25 Christmas\n"
    ),
    "travel-policy.md": (
        "# Travel Policy\n\n"
        "- Economy class for flights under 6 hours.\n"
        "- Hotel cap: $180/night (major cities $220).\n"
        "- Meals: $60/day, receipts required above $20.\n"
    ),
}


def seed_knowledge_base(tenant_id: uuid.UUID) -> None:
    """Create a small, realistic tenant knowledge base (idempotent)."""
    import json as _json
    from pathlib import Path

    kb_root = Path(settings.AGENT_DATA_DIR) / f"enterprise_info_{tenant_id}"
    kb_root.mkdir(parents=True, exist_ok=True)
    for name, content in KB_FILES.items():
        target = kb_root / name
        if not target.exists():
            target.write_text(content, encoding="utf-8")
    print(f"BOOTSTRAP_KB seeded {len(KB_FILES)} files into {kb_root.name}")


async def main() -> None:
    from app.services.agent_seeder import seed_default_agents, seed_okr_agent

    await seed_default_agents()
    await seed_okr_agent()

    async with async_session() as db:
        admin = await _ensure_admin(db)
        await _set_identity(db, admin, ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_DISPLAY)
        alice = await _ensure_alice(db, admin)
        seed_knowledge_base(admin.tenant_id)
        tenant_model, _platform_model = await _ensure_models(db, admin.tenant_id)
        await _configure_agents(db, admin, tenant_model)
        await _ensure_group(db, admin, alice)
        await db.commit()
    print(f"BOOTSTRAP_ALICE_USER_ID={alice.id}")
    print(f"BOOTSTRAP_ALICE_TENANT_ID={alice.tenant_id}")
    print("BOOTSTRAP_OK")


if __name__ == "__main__":
    asyncio.run(main())
