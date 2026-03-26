import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from skill_orchestrator.adapters.production import ClawHubCliSandbox
from skill_orchestrator.exceptions import RuntimeCommandError, RuntimeSandboxError


def _sandbox_root() -> Path:
    root = Path(".tmp") / f"sandbox-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.mark.asyncio
async def test_clawhub_registry_skill_installs_and_executes():
    root = _sandbox_root()

    async def runner(command, *, cwd, **kwargs):
        if "install" in command:
            skill_dir = Path(cwd) / "skills" / "calendar"
            (skill_dir / "hooks").mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text("# Calendar", encoding="utf-8")
            (skill_dir / "hooks" / "run-hook.cmd").write_text(
                '@echo {"output":"calendar"}',
                encoding="utf-8",
            )
            return ("", "")
        return ('{"output":"calendar"}', "")

    sandbox = ClawHubCliSandbox(
        clawhub_bin="clawhub",
        sandbox_root=str(root),
        command_runner=runner,
        which=lambda _: "clawhub",
    )

    skill = {"source": "clawhub", "slug": "calendar", "name": "calendar"}
    try:
        await sandbox.install(skill)
        assert await sandbox.healthcheck(skill) is True
        result = await sandbox.execute(skill, {})
        assert result == {"output": "calendar"}
    finally:
        await sandbox.rollback(skill)
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.asyncio
async def test_generated_skill_materializes_and_executes():
    root = _sandbox_root()

    async def runner(command, **kwargs):
        return ('{"output":"draft"}', "")

    sandbox = ClawHubCliSandbox(
        clawhub_bin="clawhub",
        sandbox_root=str(root),
        command_runner=runner,
        which=lambda _: "clawhub",
    )

    skill = {
        "name": "draft-skill",
        "description": "Draft skill",
        "skill_md": "# draft-skill",
        "files": {
            "SKILL.md": "# draft-skill",
            "hooks/run-hook.cmd": '@echo {"output":"draft"}',
        },
    }
    try:
        await sandbox.install(skill)
        assert await sandbox.healthcheck(skill) is True
        result = await sandbox.execute(skill, {})
        assert result == {"output": "draft"}
    finally:
        await sandbox.rollback(skill)
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.asyncio
async def test_missing_entrypoint_fails_healthcheck():
    root = _sandbox_root()
    sandbox = ClawHubCliSandbox(
        clawhub_bin="clawhub",
        sandbox_root=str(root),
        command_runner=lambda *args, **kwargs: None,
        which=lambda _: "clawhub",
    )

    skill = {
        "name": "draft-skill",
        "description": "Draft skill",
        "skill_md": "# draft-skill",
        "files": {"SKILL.md": "# draft-skill"},
    }
    try:
        await sandbox.install(skill)
        assert await sandbox.healthcheck(skill) is False
    finally:
        await sandbox.rollback(skill)
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.asyncio
async def test_cli_install_failure_bubbles_up():
    root = _sandbox_root()

    async def runner(command, **kwargs):
        raise RuntimeCommandError("install failed")

    sandbox = ClawHubCliSandbox(
        clawhub_bin="clawhub",
        sandbox_root=str(root),
        command_runner=runner,
        which=lambda _: "clawhub",
    )

    with pytest.raises(RuntimeCommandError):
        await sandbox.install({"source": "clawhub", "slug": "calendar", "name": "calendar"})

    shutil.rmtree(root, ignore_errors=True)


@pytest.mark.asyncio
async def test_cli_execute_failure_bubbles_up():
    root = _sandbox_root()

    async def runner(command, *, cwd, **kwargs):
        if "install" in command:
            skill_dir = Path(cwd) / "skills" / "calendar"
            (skill_dir / "hooks").mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text("# Calendar", encoding="utf-8")
            (skill_dir / "hooks" / "run-hook.cmd").write_text(
                '@echo {"output":"calendar"}',
                encoding="utf-8",
            )
            return ("", "")
        raise RuntimeCommandError("execute failed")

    sandbox = ClawHubCliSandbox(
        clawhub_bin="clawhub",
        sandbox_root=str(root),
        command_runner=runner,
        which=lambda _: "clawhub",
    )

    skill = {"source": "clawhub", "slug": "calendar", "name": "calendar"}
    try:
        await sandbox.install(skill)
        with pytest.raises(RuntimeCommandError):
            await sandbox.execute(skill, {})
    finally:
        await sandbox.rollback(skill)
        shutil.rmtree(root, ignore_errors=True)


def test_validate_configuration_requires_clawhub_binary():
    sandbox = ClawHubCliSandbox(
        clawhub_bin="missing-clawhub",
        sandbox_root=".tmp",
        which=lambda _: None,
    )
    with pytest.raises(RuntimeSandboxError):
        sandbox.validate_configuration()
