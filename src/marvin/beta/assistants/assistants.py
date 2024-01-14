from typing import TYPE_CHECKING, Callable, Optional, Union

from pydantic import BaseModel, Field, PrivateAttr

import marvin.utilities.tools
from marvin.tools.assistants import AssistantTool
from marvin.types import Tool
from marvin.utilities.asyncio import (
    ExposeSyncMethodsMixin,
    expose_sync_method,
    run_sync,
)
from marvin.utilities.logging import get_logger
from marvin.utilities.openai import get_openai_client

from .threads import Thread

if TYPE_CHECKING:
    from .runs import Run

logger = get_logger("Assistants")


class Assistant(BaseModel, ExposeSyncMethodsMixin):
    id: Optional[str] = None
    name: str = "Assistant"
    model: str = "gpt-4-1106-preview"
    instructions: Optional[str] = Field(None, repr=False)
    tools: list[Union[AssistantTool, Callable]] = []
    file_ids: list[str] = []
    metadata: dict[str, str] = {}
    # context level tracks nested assistant contexts
    _context_level: int = PrivateAttr(0)

    default_thread: Thread = Field(
        default_factory=Thread,
        repr=False,
        description="A default thread for the assistant.",
    )

    def clear_default_thread(self):
        self.default_thread = Thread()

    def get_tools(self) -> list[AssistantTool]:
        return [
            (
                tool
                if isinstance(tool, Tool)
                else marvin.utilities.tools.tool_from_function(tool)
            )
            for tool in self.tools
        ]

    def get_instructions(self) -> str:
        return self.instructions or ""

    @expose_sync_method("say")
    async def say_async(
        self,
        message: str,
        file_paths: Optional[list[str]] = None,
        thread: Optional[Thread] = None,
        **run_kwargs,
    ) -> "Run":
        """
        A convenience method for adding a user message to the assistant's
        default thread, running the assistant, and returning the assistant's
        messages.
        """
        thread = thread or self.default_thread

        last_message = await thread.get_messages_async(limit=1)
        if last_message:
            last_msg_id = last_message[0].id
        else:
            last_msg_id = None

        # post the message
        if message:
            await thread.add_async(message, file_paths=file_paths)

        # run the thread
        async with self:
            await thread.run_async(assistant=self, **run_kwargs)

        # load all messages, including the user message
        response_messages = await thread.get_messages_async(after_message=last_msg_id)
        return response_messages

    def __enter__(self):
        return run_sync(self.__aenter__())

    def __exit__(self, exc_type, exc_val, exc_tb):
        return run_sync(self.__aexit__(exc_type, exc_val, exc_tb))

    async def __aenter__(self):
        self._context_level += 1
        # if this is the outermost context and no ID is set, create the assistant
        if self.id is None and self._context_level == 1:
            await self.create_async()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # If this is the outermost context, delete the assistant
        if self._context_level == 1:
            await self.delete_async()
        self._context_level -= 1
        return False

    @expose_sync_method("create")
    async def create_async(self):
        if self.id is not None:
            raise ValueError(
                "Assistant has an ID and has already been created in the OpenAI API."
            )
        client = get_openai_client()
        response = await client.beta.assistants.create(
            **self.model_dump(
                include={"name", "model", "metadata", "file_ids", "metadata"}
            ),
            tools=[tool.model_dump() for tool in self.get_tools()],
            instructions=self.get_instructions(),
        )
        self.id = response.id

    @expose_sync_method("delete")
    async def delete_async(self):
        if not self.id:
            raise ValueError("Assistant has no ID and doesn't exist in the OpenAI API.")
        client = get_openai_client()
        await client.beta.assistants.delete(assistant_id=self.id)
        self.id = None

    @classmethod
    def load(cls, assistant_id: str, **kwargs):
        return run_sync(cls.load_async(assistant_id, **kwargs))

    @classmethod
    async def load_async(cls, assistant_id: str, **kwargs):
        client = get_openai_client()
        response = await client.beta.assistants.retrieve(assistant_id=assistant_id)
        return cls(**(response.model_dump() | kwargs))

    def chat(self, thread: Thread = None):
        if thread is None:
            thread = self.default_thread
        return thread.chat(assistant=self)

    def pre_run_hook(self, run: "Run"):
        pass

    def post_run_hook(self, run: "Run"):
        pass
