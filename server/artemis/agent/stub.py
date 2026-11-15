"""StubAgent: the scripted Hello World, for the walking skeleton before the real agent."""

from artemis.contracts import AgentOutcome, Answered, Emit, Failed, TaskContext, ToolGateway

from .base import say

PROJECT = "hello-world"
ENTRY = "main.py"
CONTENT = 'print("Hello, World!")\n'


class StubAgent:
    """Ignores the request text and always creates, writes and runs hello-world/main.py."""

    async def run(self, task: TaskContext, tools: ToolGateway, emit: Emit) -> AgentOutcome:
        async def finish(text: str) -> Answered:
            await say(emit, task.task_id, text)
            return Answered(text=text)

        await tools.call(task.task_id, "plan", {"goal": "Create a Python program that prints Hello World", "steps": [
            {"say": "I'll write main.py.", "action": "write", "target": "editor"},
            {"say": "Then I'll run it.", "action": "run", "target": "terminal"},
        ]})
        created = await tools.call(task.task_id, "create_project", {"name": PROJECT})
        if not created.ok:
            return Failed(code=created.error, message=created.message or "")
        project = created.data["project"]

        wrote = await tools.call(
            task.task_id, "write_files", {"files": [{"path": ENTRY, "content": CONTENT}]}
        )
        if not wrote.ok:
            if wrote.error in ("rejected_by_user", "scan_blocked"):
                return await finish("Okay, I didn't create anything.")
            return Failed(code=wrote.error, message=wrote.message or "")

        ran = await tools.call(task.task_id, "run_program", {"entry": ENTRY})
        if not ran.ok:
            if ran.error in ("rejected_by_user", "scan_blocked"):
                return await finish(f"Okay, I created {project} with {ENTRY} but didn't run it.")
            return Failed(code=ran.error, message=ran.message or "")

        if ran.data["exit_code"] != 0:
            return await finish(
                f"I created {project} with {ENTRY} and ran it, but it failed "
                f"with exit code {ran.data['exit_code']}."
            )
        return await finish(f"I created {project} with {ENTRY} and ran it. It printed Hello, World!")
