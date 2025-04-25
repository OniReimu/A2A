import json
import traceback
import asyncio
from typing import AsyncIterable, Union, Any
from common.types import (
    SendTaskRequest,
    TaskSendParams,
    Message,
    TaskStatus,
    Artifact,
    TaskStatusUpdateEvent,
    TaskArtifactUpdateEvent,
    TextPart,
    TaskState,
    Task,
    SendTaskResponse,
    InternalError,
    JSONRPCResponse,
    SendTaskStreamingRequest,
    SendTaskStreamingResponse,
)
from common.server.task_manager import InMemoryTaskManager
from agent import Web3Agent, custom_serializer
import common.server.utils as utils
import logging

# Constants
BLOCKCHAIN_TIMEOUT = 45  # seconds for blockchain operations which can be slow

logger = logging.getLogger(__name__)

class AgentTaskManager(InMemoryTaskManager):
    """Task manager for the Web3 Agent that handles blockchain requests"""

    def __init__(self, agent: Web3Agent):
        super().__init__()
        self.agent = agent
        
    @classmethod
    async def create(cls, agent: Web3Agent):
        """Create and initialize a new task manager instance"""
        instance = cls(agent)
        # Initialize the agent - this will use the background MCP server
        await agent.initialize()
        return instance

    async def _stream_generator(
      self, request: SendTaskRequest
    ) -> AsyncIterable[SendTaskResponse]:
        """Streaming support is not implemented"""
        raise NotImplementedError("Streaming is not implemented")
    
    def _validate_request(
        self, request: Union[SendTaskRequest, SendTaskStreamingRequest]
    ) -> None:
        """Validate that the request content type is supported"""
        task_send_params: TaskSendParams = request.params
        if not utils.are_modalities_compatible(
            task_send_params.acceptedOutputModes, Web3Agent.SUPPORTED_CONTENT_TYPES
        ):
            logger.warning(
                "Unsupported output mode. Received %s, Support %s",
                task_send_params.acceptedOutputModes,
                Web3Agent.SUPPORTED_CONTENT_TYPES,
            )
            return utils.new_incompatible_types_error(request.id)
        
    async def on_send_task(self, request: SendTaskRequest) -> SendTaskResponse:
        """Handle a new task request"""
        error = self._validate_request(request)
        if error:
            return error
        await self.upsert_task(request.params)
        return await self._invoke(request)
    
    async def on_send_task_subscribe(
        self, request: SendTaskStreamingRequest
    ) -> AsyncIterable[SendTaskStreamingResponse] | JSONRPCResponse:
        """Handle a streaming task request (not implemented)"""
        error = self._validate_request(request)
        if error:
            return error
        await self.upsert_task(request.params)
        return self._stream_generator(request)
    
    async def _update_store(
        self, task_id: str, status: TaskStatus, artifacts: list[Artifact]
    ) -> Task:
        """Update the task store with new status and artifacts"""
        async with self.lock:
            try:
                task = self.tasks[task_id]
            except KeyError:
                logger.error(f"Task {task_id} not found for updating the task")
                raise ValueError(f"Task {task_id} not found")
                
            task.status = status
            
            if artifacts is not None:
                if task.artifacts is None:
                    task.artifacts = []
                task.artifacts.extend(artifacts)
                
            return task
        
    async def _invoke(self, request: SendTaskRequest) -> SendTaskResponse:
        """Process a task by invoking the agent"""
        task_send_params: TaskSendParams = request.params
        query = self._get_user_query(task_send_params)
        
        try:
            print(f"TaskManager: Invoking agent with query: {query}")
            
            try:
                # Wait for agent response with timeout
                result = await asyncio.wait_for(
                    self.agent.invoke(query, task_send_params.sessionId),
                    timeout=BLOCKCHAIN_TIMEOUT
                )
                
                print(f"TaskManager: Agent response received: {result}")
                
                # Ensure we have a result
                if result is None:
                    raise ValueError("No response received from agent")
                
                # Determine if result is JSON or text
                try:
                    # Try to parse as JSON with custom serializer if needed
                    if isinstance(result, (dict, list)) or result.startswith("{") or result.startswith("["):
                        # Try to parse the result directly or convert with custom serializer
                        if isinstance(result, str):
                            try:
                                json_result = json.loads(result)
                            except json.JSONDecodeError:
                                # If direct parsing fails, it might be a complex object stringified
                                print("Initial JSON parse failed, trying custom conversion...")
                                json_result = json.loads(json.dumps(result, default=custom_serializer))
                        else:
                            # Handle non-string objects
                            json_result = json.loads(json.dumps(result, default=custom_serializer))
                            
                        print(f"TaskManager: Detected JSON response: {json_result}")
                        parts = [{"type": "data", "data": json_result}]
                    else:
                        print(f"TaskManager: Detected text response: {result}")
                        parts = [{"type": "text", "text": result}]
                except (json.JSONDecodeError, TypeError) as e:
                    # If all JSON parsing attempts fail, treat as text
                    print(f"TaskManager: JSON parsing failed ({e}), treating as text: {result}")
                    parts = [{"type": "text", "text": str(result)}]
                
                # Determine task state based on content
                task_state = (
                    TaskState.INPUT_REQUIRED 
                    if "MISSING_INFO:" in str(result) 
                    else TaskState.COMPLETED
                )
                
                # Update task store and return response
                task = await self._update_store(
                    task_send_params.id,
                    TaskStatus(
                        state=task_state, 
                        message=Message(role="agent", parts=parts)
                    ),
                    [Artifact(parts=parts)],
                )
                
                return SendTaskResponse(id=request.id, result=task)
                
            except asyncio.TimeoutError:
                print(f"TaskManager: Timeout waiting for agent response after {BLOCKCHAIN_TIMEOUT} seconds")
                return self._create_error_response(
                    request.id,
                    task_send_params.id,
                    f"Timeout waiting for response from blockchain after {BLOCKCHAIN_TIMEOUT} seconds"
                )
                
        except Exception as e:
            print(f"TaskManager: Error invoking agent: {e}")
            logger.error(f"Error invoking agent: {e}")
            logger.error(traceback.format_exc())
            
            return self._create_error_response(
                request.id, 
                task_send_params.id,
                f"Error invoking agent: {str(e)}"
            )
    
    def _safe_json_parse(self, value: Any) -> Any:
        """Safely parse JSON or return original value"""
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                pass
        return value

    def _create_error_response(self, request_id: str, task_id: str, error_msg: str) -> SendTaskResponse:
        """Create a standardized error response"""
        parts = [{"type": "text", "text": error_msg}]
        
        try:
            # Try to update the task with the error
            task = asyncio.run(self._update_store(
                task_id,
                TaskStatus(
                    state=TaskState.COMPLETED, 
                    message=Message(role="agent", parts=parts)
                ),
                [Artifact(parts=parts)],
            ))
            return SendTaskResponse(id=request_id, result=task)
        except Exception:
            # If we can't update the task, raise the original error
            raise ValueError(error_msg)
        
    def _get_user_query(self, task_send_params: TaskSendParams) -> str:
        """Extract the user query from task parameters"""
        part = task_send_params.message.parts[0]
        if not isinstance(part, TextPart):
            raise ValueError("Only text parts are supported")
        return part.text